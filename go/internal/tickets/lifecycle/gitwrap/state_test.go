package gitwrap

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/ospath"
)

// 比べのキーは、どの機械でも `/` 区切りで、大文字小文字を畳む（`/` を含む型と比べるため）。
func TestFoldKeepsSlashes(t *testing.T) {
	if got := fold(`.CCNAVI/Approved/doing/X.md`); got != ".ccnavi/approved/doing/x.md" {
		t.Errorf("fold = %q", got)
	}
}

// worktree add の行き先は、`.claude/worktrees/` の下 1 階層だけ。Windows 以外の `\` は名前の字なので、下には入らない。
func TestWorktreeDestinations(t *testing.T) {
	e := &Env{Root: "/ws", Cwd: "/ws"}
	cases := map[string]bool{
		".claude/worktrees/x":       true,
		".CLAUDE/Worktrees/x":       true,
		".claude/worktrees/x/y":     false,
		".claude/worktrees":         false,
		"projects/p/.claude/w/x":    false,
		`.claude\worktrees\x`:       ospath.Windows,
		"../ws/.claude/worktrees/x": true,
	}
	for dest, want := range cases {
		if got := e.inWorktrees(e.abs(dest)); got != want {
			t.Errorf("%s: %v（期待 %v）", dest, got, want)
		}
	}
}

// 引数の無い worktree は list として読む（git を起こさずに通す）。
func TestBareWorktreeIsList(t *testing.T) {
	e := &Env{Root: "/ws", Cwd: "/ws"}
	if d, warn := e.worktree(nil); d != nil || warn != "" {
		t.Errorf("worktree: %v %q", d, warn)
	}
	if d, _ := e.worktree([]string{"remove", "--force", "x"}); d == nil || !strings.Contains(d.ID, "remove-force") {
		t.Errorf("worktree remove --force: %v", d)
	}
}
