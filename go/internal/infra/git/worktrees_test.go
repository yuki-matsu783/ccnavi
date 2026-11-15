package git

import "testing"

func TestWorktreesAndHolding(t *testing.T) {
	list := parseWorktrees("worktree /ws\nHEAD aaa\nbranch refs/heads/main\n\nworktree /ws/.claude/worktrees/i0001\nHEAD bbb\n" +
		"branch refs/heads/i0001\r\n\nworktree /ws/x\nHEAD ccc\ndetached\n")
	if len(list) != 3 || list[0].Path != "/ws" || list[1].Branch() != "i0001" || list[2].Ref != "" {
		t.Fatalf("parseWorktrees = %+v", list)
	}
	for _, c := range []struct{ branch, want string }{
		{"main", "/ws"}, {"i0001", "/ws/.claude/worktrees/i0001"}, {"I0001", ""}, {"ma", ""}, {"", ""},
	} {
		if w, _ := Holding(list, c.branch); w.Path != c.want {
			t.Errorf("Holding(%q) = %q, want %q", c.branch, w.Path, c.want)
		}
	}
}
