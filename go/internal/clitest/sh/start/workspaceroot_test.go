package start

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// ccnavi_workspace が、プロジェクトのコピーではなくワークスペースルートを選ぶこと。
//
// プロジェクト（projects/<名前>/）が `.ccnavi/scripts/` のコピーを持っていても、cwd がその中なら、cwd から登って最初にヒットするのは
// プロジェクトになる。そのまま根にすると logs/ や state がプロジェクトの中にできるので、`projects/` の親にもワークスペースがあれば、
// そちらを根にする。

// putScripts は root の `.ccnavi/scripts/` に共通部と、ワークスペースルートを出す probe.sh を置いて、probe.sh のパスを返す。
func putScripts(t testing.TB, root string) string {
	t.Helper()
	dest := copyScripts(t, root)
	return write(t, filepath.Join(dest, "probe.sh"), ". \"$(dirname \"$0\")/ccnavi-common.sh\"\nccnavi_workspace || ccnavi_no_workspace probe\n")
}

// rootFrom は script を cwd から起こして、出したルートの実パス。
func rootFrom(t testing.TB, script, cwd string) string {
	t.Helper()
	r := run.Sh(t, slash(script), nil, run.Opts{Dir: cwd})
	got := strings.TrimSpace(r.Stdout)
	if real, err := filepath.EvalSymlinks(got); err == nil {
		return real
	}
	return got
}

func TestWorkspaceRootIsNotAProjectCopy(t *testing.T) {
	t.Parallel()
	need(t, "sh")
	type env struct{ ws, project, script string }
	build := func(t *testing.T) env {
		ws := base.TempDir(t)
		project := filepath.Join(ws, "projects", "p")
		mkdir(t, filepath.Join(project, "src"))
		return env{ws, project, putScripts(t, ws)}
	}
	cases := []struct {
		name string
		// run は選ばれたルートと、期待するルートを返す。
		run func(t *testing.T, e env) (got, want string)
	}{
		{"a project without a copy", func(t *testing.T, e env) (string, string) {
			return rootFrom(t, e.script, e.project), e.ws
		}},
		{"a project with its own copy", func(t *testing.T, e env) (string, string) {
			putScripts(t, e.project)
			return rootFrom(t, e.script, e.project), e.ws
		}},
		{"a subdirectory of a project with its own copy", func(t *testing.T, e env) (string, string) {
			putScripts(t, e.project)
			return rootFrom(t, e.script, filepath.Join(e.project, "src")), e.ws
		}},
		{"a project copy run from inside the project", func(t *testing.T, e env) (string, string) {
			return rootFrom(t, putScripts(t, e.project), e.project), e.ws
		}},
		{"a project outside any workspace is its own root", func(t *testing.T, e env) (string, string) {
			lone := filepath.Join(base.TempDir(t), "projects", "p")
			mkdir(t, lone)
			return rootFrom(t, putScripts(t, lone), lone), lone
		}},
		{"a copy in a worktree is not a root", func(t *testing.T, e env) (string, string) {
			tree := filepath.Join(e.ws, ".claude", "worktrees", "x")
			mkdir(t, tree)
			return rootFrom(t, putScripts(t, tree), tree), e.ws
		}},
		// `projects/` の下でも、親にワークスペースが無ければ、最初にヒットしたコピーが根のまま。
		{"a directory that merely has projects in its path", func(t *testing.T, e env) (string, string) {
			other := filepath.Join(e.ws, "projects", "p", "projects", "q")
			mkdir(t, other)
			putScripts(t, other)
			return rootFrom(t, e.script, other), other
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			got, want := c.run(t, build(t))
			eq(t, got, want, "ルート")
		})
	}
}

// ready が親のワークツリーを消したあと、ターミナルが消えたディレクトリに残る形。根は返さず、スクリプトの置き場から見つけた根へ cd する
// 1 行を出して止まる。
func TestWorkspaceRootARemovedCwdStopsWithTheCdToTheRoot(t *testing.T) {
	t.Parallel()
	need(t, "sh")
	ws := base.TempDir(t)
	script := putScripts(t, ws)
	gone := filepath.Join(ws, "gone")
	mkdir(t, gone)
	probe := "cd '" + slash(gone) + "' && rmdir '" + slash(gone) + "' && exec sh '" + slash(script) + "'"
	r := run.Command(t, "sh", []string{"-c", probe})
	truth(t, r.Code != 0, "終了コードが 0: "+r.Stdout)
	eq(t, r.Stdout, "", "標準出力")
	has(t, r.Stderr, "消えています", "cd '"+slash(ws)+"'")
}
