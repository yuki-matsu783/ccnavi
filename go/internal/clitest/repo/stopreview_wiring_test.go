package repo

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// Stop の AI 判定の入口（`ccnavi stop-review material|record`）が、読めない入力で止めないこと。
// 判定役が渡す結果が読めないとき・transcript から鍵を導けないときも、判定役の手を止めず `{"ok": true}` や理由を返す。
// 計画・材料・判定結果を通す筋は stopreview_scenarios_test.go が、計画の作り方と試行の状態遷移は
// records/stopreview・hook/events の単体の試験が見る。

// stopReviewEnv は判定の入口を起こす環境（学びを切り、判定役を経由する）。
var stopReviewEnv = map[string]string{
	env.Learning:        "disable",
	env.StopReviewAgent: "1",
}

// stopReviewWiringRoot は transcript を 1 行置いた使い捨ての木を作る。
func stopReviewWiringRoot(t *testing.T) (root, transcript string) {
	t.Helper()
	root = base.TempDir(t)
	transcript = filepath.Join(root, "t.jsonl")
	const line = `{"type": "assistant", "uuid": "a1", "message": {"content": "x"}}` + "\n"
	if err := os.WriteFile(transcript, []byte(line), 0o644); err != nil {
		t.Fatal(err)
	}
	return root, transcript
}

// stopReviewRun は `ccnavi --root <root> stop-review <verb> s1 <transcript>` を起こし、標準出力を返す。終了コードは 0 のはず。
func stopReviewRun(t *testing.T, root, verb, transcript, stdin string) string {
	t.Helper()
	r := run.Ccnavi(t, []string{"--root", root, "stop-review", verb, "s1", transcript},
		run.Opts{Input: stdin, Dir: root, Env: stopReviewEnv})
	if r.Code != 0 {
		t.Fatalf("code = %d, stderr = %s", r.Code, r.Stderr)
	}
	return r.Stdout
}

func TestRecordWithGarbageReturnsOK(t *testing.T) {
	t.Parallel()
	root, transcript := stopReviewWiringRoot(t)
	requireOK(t, stopReviewRun(t, root, "record", transcript, "not json"))
}

func TestRecordWithoutMaterialReturnsOK(t *testing.T) {
	t.Parallel()
	root, transcript := stopReviewWiringRoot(t)
	requireOK(t, stopReviewRun(t, root, "record", transcript, "{}"))
}

// requireOK は答えが JSON の {"ok": true} だけであることを見る（余計な欄や偽を許さない）。
func requireOK(t *testing.T, got string) {
	t.Helper()
	var body map[string]any
	if err := json.Unmarshal([]byte(got), &body); err != nil || len(body) != 1 || body["ok"] != true {
		t.Errorf("答えが {\"ok\": true} でない: %s", got)
	}
}

func TestMaterialWithoutTheTranscriptSaysNoKey(t *testing.T) {
	t.Parallel()
	root, _ := stopReviewWiringRoot(t)
	missing := filepath.Join(root, "nonexistent")
	if got := stopReviewRun(t, root, "material", missing, ""); !strings.Contains(got, "no_key") {
		t.Errorf("material の答えに no_key が無い: %s", got)
	}
}
