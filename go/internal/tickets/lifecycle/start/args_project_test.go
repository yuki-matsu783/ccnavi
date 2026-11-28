package start

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// `start args` は、打った場所がプロジェクト（projects/<名前>）か、そこから切ったワークツリーなら、その名前を `project` で答える。
// ワークスペースと、ワークスペースから切ったワークツリーでは答えない。
func TestArgsNamesTheProject(t *testing.T) {
	root := testkit.Workspace(t, "self-wt")
	projects := filepath.Join(root, "projects")
	project := filepath.Join(projects, "foo")
	if err := os.MkdirAll(project, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, project, "init", "-q", "-b", "main")
	testkit.Commit(t, project, "init")
	testkit.Git(t, project, "worktree", "add", "-q", "-b", "pwt", filepath.Join(root, ".claude", "worktrees", "pwt"), "main")
	for _, c := range []struct{ cwd, want string }{
		{root, ""},
		{filepath.Join(root, ".claude", "worktrees", "self-wt"), ""},
		{project, "foo"},
		{filepath.Join(root, ".claude", "worktrees", "pwt"), "foo"},
	} {
		got, _ := Args(c.cwd, root, projects, []string{"--issue", "3"})
		v := ""
		for _, line := range strings.Split(got, "\n") {
			if rest, ok := strings.CutPrefix(line, "project "); ok {
				v = rest
			}
		}
		if v != c.want {
			t.Errorf("%s: project = %q, want %q（%q）", c.cwd, v, c.want, got)
		}
	}
}
