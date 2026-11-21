package start

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
)

// `ccnavi-idle.sh`（放置したセッションの引き継ぎ促しの待機）の受入テスト。
//
// 実行ファイルを起こす小さな sh を置いて、Stop の payload から「待つ → 起きたら聞く → 起こす（終了コード 2 と指示）」までを通しで見る。
// 待ち時間は `CCNAVI_HANDOFF_WAIT_SECONDS` で 1 秒に縮める。

type idleEnv struct {
	t          testing.TB
	ws         string
	exe        string
	transcript string
}

func newIdle(t testing.TB) *idleEnv {
	t.Helper()
	need(t, "sh")
	ws := base.TempDir(t)
	copyTree(t, base.ScriptsDir(), filepath.Join(ws, ".ccnavi", "scripts"))
	return &idleEnv{t: t, ws: ws, exe: exeWrapper(t, filepath.Join(ws, "fake")), transcript: filepath.Join(ws, "t.jsonl")}
}

func transcriptRow(tokens int) string {
	usage := map[string]any{
		"input_tokens": 2, "cache_read_input_tokens": tokens - 12, "cache_creation_input_tokens": 10,
		"cache_creation": map[string]any{"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 10},
	}
	row, err := json.Marshal(map[string]any{"type": "assistant", "message": map[string]any{"usage": usage}})
	if err != nil {
		panic(err)
	}
	return string(row)
}

// start は Stop の payload を渡して `ccnavi-idle.sh start` を起こす。
func (e *idleEnv) start(tokens int, extra map[string]string) (int, string) {
	e.t.Helper()
	write(e.t, e.transcript, transcriptRow(tokens)+"\n")
	payload, err := json.Marshal(map[string]any{"hook_event_name": "Stop", "session_id": "sess-sh", "transcript_path": e.transcript})
	if err != nil {
		e.t.Fatal(err)
	}
	r := runWithInput(e.t, filepath.Join(e.ws, ".ccnavi", "scripts", "ccnavi-idle.sh"), e.ws, string(payload), merge(map[string]string{
		"CLAUDE_PROJECT_DIR": e.ws, "CCNAVI_ENTRY_PATH": e.exe, "CCNAVI_HANDOFF_WAIT_SECONDS": "1",
	}, extra), "start")
	return r.Code, r.Stderr
}

func TestIdleAnIdleSessionOverTheThresholdExitsWith2AndTheInstruction(t *testing.T) {
	t.Parallel()
	e := newIdle(t)
	got, stderr := e.start(500_000, nil)
	eq(t, got, 2, "終了コード ("+stderr+")")
	has(t, stderr, "[idle-handoff]", "scratchpad/")
}

// 発火したあとはユーザが打つまで始め直さない。
func TestIdleOnceFiredItDoesNotStartAgainUntilTheUserTypes(t *testing.T) {
	t.Parallel()
	e := newIdle(t)
	first, _ := e.start(500_000, nil)
	again, _ := e.start(500_000, nil)
	eq(t, first, 2, "1 回目")
	eq(t, again, 0, "2 回目")
}

func TestIdleExitsWith0WithoutFiring(t *testing.T) {
	t.Parallel()
	for name, c := range map[string]struct {
		tokens int
		env    map[string]string
	}{
		"below the threshold it does not wait":            {10_000, nil},
		"an unattended run does not wait":                 {500_000, map[string]string{"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}},
		"without the executable it does nothing":          {500_000, map[string]string{"CCNAVI_ENTRY_PATH": "none"}},
		"setting the threshold variable to 0 disables it": {500_000, map[string]string{"CCNAVI_HANDOFF_SUGGEST_TOKENS": "0"}},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			e := newIdle(t)
			env := c.env
			if env["CCNAVI_ENTRY_PATH"] == "none" {
				env = map[string]string{"CCNAVI_ENTRY_PATH": filepath.Join(e.ws, "none")}
			}
			got, stderr := e.start(c.tokens, env)
			eq(t, got, 0, "終了コード ("+stderr+")")
			if c.tokens == 10_000 {
				eq(t, stderr, "", "標準エラー")
			}
		})
	}
}
