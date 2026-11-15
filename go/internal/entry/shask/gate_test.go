package shask

import (
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// push のゲートの次の一手（pass・stop・ls-remote）。親のワークツリーで送った形跡があれば ls-remote、承認済みを
// 読めず形跡も無ければ stop、親でない・親のワークツリーの形でない・識別子が違うブランチは pass。
func TestPushGate(t *testing.T) {
	root := shaskWorkspace(t)
	w := func(name string) string { return testkit.Worktree(root, name) }
	conf := testkit.Settings(t, root)
	cases := []struct {
		path, branch, ident, want string
	}{
		{w("i0001"), "i0001", "i0001", "gate ls-remote"},
		{w("i0002"), "i0002", "i0002", "gate stop"},
		{w("fix-a"), "fix-a", "fix-a", "gate pass"},
		{root, "i0001", "i0001", "gate pass"},
		{w("i0001"), "feature/other", "i0001", "gate pass"},
	}
	for _, c := range cases {
		var out, errOut strings.Builder
		if !pushGate(&out, &errOut, root, conf, c.path, c.branch, c.ident, "15") {
			t.Fatalf("%s: 答えなかった（%s）", c.path, errOut.String())
		}
		lines := strings.Split(out.String(), "\n")
		if lines[0] != head || lines[1] != c.want {
			t.Errorf("%s %s: %q", c.path, c.branch, out.String())
		}
	}
}

// 送った形跡（pushTrace）。リモートの参照が無いとき（fetch.prune で消えた・一度も送っていない）は、依頼した・
// 片付けた・着手したのどれかで形跡ありとする。承認しただけでは形跡にしない（最初の push を通す）。
func TestPushTraceWithoutTheRemoteReference(t *testing.T) {
	path := t.TempDir()
	cases := []struct {
		name string
		f    parentFacts
		want bool
	}{
		{"一度も送っていない", parentFacts{approved: no, requested: no, archived: no, started: no}, false},
		{"承認しただけ", parentFacts{approved: yes, requested: no, archived: no, started: no}, false},
		{"着手した（参照は消えた）", parentFacts{approved: yes, requested: no, archived: no, started: yes}, true},
		{"依頼した", parentFacts{approved: yes, requested: yes, archived: no, started: no}, true},
		{"片付けた", parentFacts{approved: no, requested: no, archived: yes, started: no}, true},
	}
	for _, c := range cases {
		if got := pushTrace(path, "feature/123-login", c.f); got != c.want {
			t.Errorf("%s: %v、want %v", c.name, got, c.want)
		}
	}
}
