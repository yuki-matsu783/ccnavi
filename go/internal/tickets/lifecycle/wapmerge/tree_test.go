package wapmerge

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// ミラーのずれ（共通レイヤーのコピー・skills/）の案内は、プロジェクトの親の finish だけに出す（#707）。
// ワークスペース自身の親はふつうの変更として言い、混在のときはずれ以外の件数も出す。

const (
	mirrorFile = ".ccnavi/config/common/rules.yml"
	skillFile  = "skills/process-test/SKILL.md"
)

func dirtyTree(t *testing.T, rels ...string) string {
	t.Helper()
	root := testkit.ShapedWorkspace(t)
	for _, rel := range rels {
		testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), "old\n")
	}
	testkit.Git(t, root, "add", "-A", "-f")
	testkit.Git(t, root, "commit", "-q", "-m", "x")
	for _, rel := range rels {
		if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(rel)), []byte("new\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestTreeProblemMirrorGuidance(t *testing.T) {
	for _, c := range []struct {
		name    string
		rels    []string
		project bool
		hints   int
		has     []string
		hasNot  []string
	}{
		{"プロジェクトの親はミラーの案内", []string{mirrorFile, skillFile}, true, 1,
			[]string{mirrorFile, "コミットも取り消しもしない", "ccnavi-user-mirror.sh"},
			[]string{"サブエージェントの作業がコミットされていません", "ずれ以外", "restore"}},
		{"ワークスペース自身の親はふつうの変更", []string{mirrorFile}, false, -1,
			[]string{"サブエージェントの作業がコミットされていません", "restore"}, []string{"ccnavi-user-mirror.sh"}},
		{"プロジェクトの親のふつうの変更", []string{"src/keep.py"}, true, -1,
			[]string{"サブエージェントの作業がコミットされていません"}, []string{"ccnavi-user-mirror.sh"}},
		{"混在はずれ以外の件数", []string{mirrorFile, "src/keep.py"}, true, -1,
			[]string{"ずれ以外の変更が 1 件", "src/keep.py", "restore", "ccnavi-user-mirror.sh"}, nil},
	} {
		t.Run(c.name, func(t *testing.T) {
			why, hints, bad := treeProblem(&config.Settings{}, dirtyTree(t, c.rels...), "", "i0001-01-01", c.project)
			if !bad {
				t.Fatal("止まらない")
			}
			all := why + "\n" + strings.Join(hints, "\n")
			for _, s := range c.has {
				if !strings.Contains(all, s) {
					t.Errorf("%q が無い: %s", s, all)
				}
			}
			for _, s := range c.hasNot {
				if strings.Contains(all, s) {
					t.Errorf("%q がある: %s", s, all)
				}
			}
			if c.hints >= 0 && len(hints) != c.hints {
				t.Errorf("案内の数: %v", hints)
			}
		})
	}
}

func TestUnrestorableMatchesPython(t *testing.T) {
	t.Setenv("GIT_CONFIG_GLOBAL", os.DevNull)
	root := testkit.ShapedWorkspace(t)
	base := testkit.Head(t, root)
	blob := strings.TrimSpace(testkit.Git(t, root, "hash-object", "-w", "--", ".gitignore"))
	for _, entry := range []string{"100644," + blob + ",src/A.py", "100644," + blob + ",src/a.py",
		"100644," + blob + ",src/x\ny.py", "160000," + base + ",sub"} {
		testkit.Git(t, root, "update-index", "--add", "--cacheinfo", entry)
	}
	testkit.Git(t, root, "commit", "-q", "-m", "unsafe")
	head := testkit.Head(t, root)
	want := golden.Load[map[string]any](t, "unrestorable")
	reasons, why := unrestorable(root, base, head)
	got := []any{}
	for _, r := range reasons {
		got = append(got, r)
	}
	if w, _ := want["reasons"].([]any); len(w) != 3 || strings.Join(toStrings(w), "|") != strings.Join(reasons, "|") || why != want["why"] {
		t.Errorf("Go: %q %q\nPython: %v", got, why, want)
	}
}

func toStrings(xs []any) []string {
	out := make([]string, len(xs))
	for i, x := range xs {
		out[i], _ = x.(string)
	}
	return out
}
