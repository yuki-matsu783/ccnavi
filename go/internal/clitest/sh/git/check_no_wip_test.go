package git

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// noWipSh は配る sh ではなく、リポジトリの scripts/ にある（.ccnavi/scripts/ ではない）。
func noWipSh() string { return filepath.Join(base.Root(), "scripts", "ccnavi-check-no-wip.sh") }

// ccnavi-check-no-wip.sh: 統合先へ入れる差分に wip/ が含まれていれば失敗させる。wip/handoff/ は通す。大文字小文字は区別しない。
func TestCheckNoWipFailsOnlyWhenTheDiffHoldsWip(t *testing.T) {
	t.Parallel()
	need(t, "sh", "git")
	for _, tc := range []struct {
		name    string
		files   []string
		code    int
		stderrH string // 標準エラーに含むもの
	}{
		{"wip の下にある", []string{"src/a.py", "wip/proposals/todo/x.md"}, 1, "wip/proposals/todo/x.md"},
		{"wip が無い", []string{"src/a.py"}, 0, ""},
		{"WIP は大文字でも断る", []string{"WIP/proposals/x.md"}, 1, ""},
		{"wip/handoff は大文字小文字を違えても通す", []string{"Wip/Handoff/learnings/a.md"}, 0, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			dir := base.TempDir(t)
			seedRepo(t, dir, "main")
			git(t, dir, "checkout", "-q", "-b", "topic")
			for _, f := range tc.files {
				write(t, filepath.Join(dir, f), "x\n")
			}
			git(t, dir, "add", "-A")
			git(t, dir, "commit", "-q", "--allow-empty", "-m", "topic")
			r := run.Sh(t, noWipSh(), []string{"main"}, run.Opts{Dir: dir})
			if r.Code != tc.code || !strings.Contains(r.Stderr, tc.stderrH) {
				t.Errorf("code = %d, stderr = %q", r.Code, r.Stderr)
			}
		})
	}
}

func TestCheckNoWipFailsOnAnUnknownRef(t *testing.T) {
	t.Parallel()
	need(t, "sh", "git")
	dir := base.TempDir(t)
	seedRepo(t, dir, "main")
	r := run.Sh(t, noWipSh(), []string{"nosuch"}, run.Opts{Dir: dir})
	if r.Code == 0 {
		t.Errorf("存在しない参照で 0 になった: %q", r.Stdout)
	}
}
