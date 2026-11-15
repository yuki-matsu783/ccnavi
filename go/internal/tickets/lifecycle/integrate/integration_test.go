package integrate

import (
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// 統合先は手元の origin/HEAD、無ければ origin/main・origin/master の順。どれも無ければ空で、ローカルの main では
// 代わりにしない。案内は origin が無いときと origin/HEAD が決まらないときで言い分ける。
func TestIntegrationNameAndHint(t *testing.T) {
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Commit(t, root, "seed")
	if got := IntegrationName(root); got != "" {
		t.Errorf("何も無いのに %q", got)
	}
	if hint := IntegrationHint(root); !strings.Contains(hint, "origin が無いので統合先が決まらない") {
		t.Errorf("origin が無いときの案内: %q", hint)
	}
	testkit.Git(t, root, "remote", "add", "origin", "https://example.invalid/x.git")
	if hint := IntegrationHint(root); !strings.Contains(hint, "統合先 (origin/HEAD) が決まらない") {
		t.Errorf("origin/HEAD が無いときの案内: %q", hint)
	}
	steps := []struct{ ref, head, want string }{
		{"master", "", "master"},
		{"main", "", "main"},
		{"develop-v1.0.0", "develop-v1.0.0", "develop-v1.0.0"},
	}
	for _, s := range steps {
		testkit.Git(t, root, "update-ref", "refs/remotes/origin/"+s.ref, "HEAD")
		if s.head != "" {
			testkit.Git(t, root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/"+s.head)
		}
		if got := IntegrationName(root); got != s.want {
			t.Errorf("%s を足した後: %q, want %q", s.ref, got, s.want)
		}
	}
}
