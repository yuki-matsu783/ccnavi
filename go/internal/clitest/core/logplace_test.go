package core

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// 記録は `<root>/logs/decisions.jsonl`、控えは `<root>/logs/state`。空文字の口は無い（置き場の固定の A4。
// 設計 wip/design/i0064-fixed-places.md §6）。診断のフラグ `--log ""` / `--state ""` は残る（テスト 20 本以上がこれで置き場を指している）。
// ここはフラグを渡さずに起動する。

// `CCNAVI_LOG=""` を入れても、判定の記録は `logs/decisions.jsonl` に 1 件書かれる。
func TestAnEmptyLogEnvStillWritesToTheDefaultPlace(t *testing.T) {
	t.Parallel()
	ws := emptyRoot(t)
	writeFile(t, ws, ".ccnavi/config/common/rules.yml", `{"version": 1}`)
	run.Git(t, ws, "init", "-q") // 実行後の監視の控えは git のツリーに対して取る。
	payload := map[string]any{
		"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": ws, "session_id": "s1",
		"tool_input": map[string]any{"command": "ls"},
	}
	done := run.Ccnavi(t, []string{"--root", ws, "--mode", "enable"}, run.Opts{Input: mustJSON(t, payload), Env: map[string]string{"CCNAVI_LOG": ""}})
	if done.Code != 0 {
		t.Fatalf("code = %d: %s%s", done.Code, done.Stdout, done.Stderr)
	}
	f, err := os.Open(filepath.Join(ws, "logs", "decisions.jsonl"))
	if err != nil {
		t.Fatalf("logs/decisions.jsonl が書かれていない: %v", err)
	}
	defer f.Close()
	var records []map[string]any
	scan := bufio.NewScanner(f)
	for scan.Scan() {
		if strings.TrimSpace(scan.Text()) == "" {
			continue
		}
		var row map[string]any
		if err := json.Unmarshal(scan.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		records = append(records, row)
	}
	if len(records) != 1 || records[0]["tool"] != "Bash" {
		t.Errorf("records = %v", records)
	}
}
