package core

import (
	"encoding/json"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// `scratchpad/` の容量の知らせが、hook を通した SessionStart の文脈に出ること（サブエージェントには出ない）。
// 容量の数え方としきい値の読み方は go/internal/tickets/notice の go test が見る。

const mb = 1024 * 1024

func putScratch(t *testing.T, root, rel string, size int) {
	t.Helper()
	writeFile(t, root, "scratchpad/"+rel, strings.Repeat("x", size))
}

// sessionContext は容量のしきい値（MB）を渡して SessionStart を 1 回起こし、additionalContext を返す。
func sessionContext(t *testing.T, root, limit string, extra map[string]any) string {
	t.Helper()
	payload := map[string]any{"hook_event_name": "SessionStart", "session_id": "s1", "cwd": root, "source": "startup"}
	for k, v := range extra {
		payload[k] = v
	}
	done := run.Ccnavi(t, []string{"--root", root, "--mode", "enable"}, run.Opts{
		Input: mustJSON(t, payload), Dir: root,
		Env: map[string]string{"CCNAVI_ENTRY_PATH": ".ccnavi/scripts/ccnavi-launcher.sh", "CCNAVI_SCRATCHPAD_WARN_MB": limit},
	})
	if done.Code != 0 {
		t.Fatalf("code = %d: %s", done.Code, done.Stderr)
	}
	if strings.TrimSpace(done.Stdout) == "" {
		return ""
	}
	var out struct {
		HookSpecificOutput struct{ AdditionalContext string }
	}
	if err := json.Unmarshal([]byte(done.Stdout), &out); err != nil {
		t.Fatal(err)
	}
	return out.HookSpecificOutput.AdditionalContext
}

func TestScratchpadSizeNoticeOnSessionStart(t *testing.T) {
	t.Parallel()
	const notice = "しきい値"
	newRepo := func(t *testing.T) string {
		root := emptyRoot(t)
		git(t, root, "init", "-q")
		return root
	}
	t.Run("知らせは文脈に届く", func(t *testing.T) {
		t.Parallel()
		root := newRepo(t)
		putScratch(t, root, "big.bin", 2*mb)
		mustContain(t, "文脈", sessionContext(t, root, "1", nil), notice)
	})
	t.Run("しきい値より小さければ出ない", func(t *testing.T) {
		t.Parallel()
		root := newRepo(t)
		putScratch(t, root, "small.bin", 100)
		mustNotContain(t, "文脈", sessionContext(t, root, "1", nil), notice)
	})
	t.Run("startup のときだけ知らせる", func(t *testing.T) {
		t.Parallel()
		root := newRepo(t)
		putScratch(t, root, "big.bin", 2*mb)
		for _, source := range []string{"resume", "compact", "clear"} {
			mustNotContain(t, source, sessionContext(t, root, "1", map[string]any{"source": source}), notice)
		}
	})
	t.Run("サブエージェントには出ない", func(t *testing.T) {
		t.Parallel()
		root := newRepo(t)
		putScratch(t, root, "big.bin", 2*mb)
		mustNotContain(t, "文脈", sessionContext(t, root, "1", map[string]any{"agent_id": "a1"}), notice)
	})
}
