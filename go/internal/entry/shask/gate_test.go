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
