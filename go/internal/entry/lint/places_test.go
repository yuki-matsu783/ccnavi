package lint

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/testkit"
)

// projects/ の置き場の苦情の場面の表。ワークスペースの git の索引・無視の有無・置き場の形（無い・空・リンク）で、
// 何を言うかが決まる。見本（projects*.json）は索引に通常のファイルが載ったときと、無視されていないときを持つ。

// placesWorkspace は main に空のコミット 1 つのワークスペース。.claude は無視する。
func placesWorkspace(t *testing.T, ignore string) (string, *config.Settings) {
	t.Helper()
	root := testkit.Workspace(t)
	put(t, root, ".gitignore", "/.claude/\n"+ignore)
	testkit.Git(t, root, "add", ".gitignore")
	testkit.Git(t, root, "commit", "-q", "-m", "ignore")
	return root, &config.Settings{Projects: filepath.Join(root, "projects")}
}

// nested は projects/<名前> に自分の git を持つリポジトリを置く（ワークスペースの git には載せない）。
func nested(t *testing.T, root, name string) {
	t.Helper()
	dir := filepath.Join(root, "projects", name)
	put(t, root, "projects/"+name+"/src/keep.py", "print(1)\n")
	testkit.Git(t, dir, "init", "-q", "-b", "main")
	testkit.Commit(t, dir, "init")
}

func aboutProjects(conf *config.Settings, root string) []rules.Problem {
	var out []rules.Problem
	for _, p := range projectPlaces(conf, root) {
		if p.Rule == whereProjects {
			out = append(out, p)
		}
	}
	return out
}

const forgotNext = "（入れ子のリポジトリとして"

func TestGitlinksAreNamedAsAddedByMistake(t *testing.T) {
	root, conf := placesWorkspace(t, "")
	nested(t, root, "my lib")
	testkit.Git(t, root, "add", "-A") // .gitignore に入れ忘れたまま add -A した形（コミット前でも言う）
	found := aboutProjects(conf, root)
	if len(found) != 1 || found[0].Severity != rules.Warn || found[0].Kind != kindProjectsTracked {
		t.Fatalf("%+v", found)
	}
	detail := found[0].Detail
	if !strings.HasPrefix(detail, "`projects/` はワークスペースの git が追跡している"+forgotNext) {
		t.Errorf("先頭の句: %s", detail)
	}
	// 名指しは囲まない。名前に空白があっても `git rm -r --cached projects` はディレクトリ名を囲まない。
	for _, w := range []string{"`projects/my lib`", "`git rm -r --cached projects`", "git reset -- projects", "/projects/"} {
		if !strings.Contains(detail, w) {
			t.Errorf("%q が無い: %s", w, detail)
		}
	}
	for _, w := range []string{"git mv", "ほか"} {
		if strings.Contains(detail, w) {
			t.Errorf("%q が出た: %s", w, detail)
		}
	}
	// 2 つめの gitlink は、最初のものを名指しして残りを数える。
	nested(t, root, "app")
	testkit.Git(t, root, "add", "-A")
	if found := aboutProjects(conf, root); len(found) != 1 || !strings.Contains(found[0].Detail, "`projects/app` ほか 1 件") {
		t.Errorf("%+v", found)
	}
	// 後から .gitignore に足しても、入れ子のリポジトリが消えても、索引に載っている限り言う。
	testkit.Commit(t, root, "載せた")
	put(t, root, ".gitignore", "/.claude/\n/projects/\n")
	for _, name := range []string{"my lib", "app"} {
		if err := os.Rename(filepath.Join(root, "projects", name), filepath.Join(root, "moved-"+name)); err != nil {
			t.Fatal(err)
		}
		if found := aboutProjects(conf, root); len(found) != 1 || !strings.HasPrefix(found[0].Detail, "`projects/` はワークスペースの git が追跡している"+forgotNext) {
			t.Errorf("%s を動かした後: %+v", name, found)
		}
	}
}

// 通常のファイルと gitlink の両方が載っているときは、ぶつかりとして改名を案内し、gitlink を名指しする。
func TestAFileAndGitlinksAreNamedOnceAsACollision(t *testing.T) {
	root, conf := placesWorkspace(t, "")
	put(t, root, "projects/foo.txt", "ワークスペース自身のソース\n")
	nested(t, root, "my lib")
	nested(t, root, "app")
	testkit.Git(t, root, "add", "-A")
	found := aboutProjects(conf, root)
	if len(found) != 1 {
		t.Fatalf("%+v", found)
	}
	detail := found[0].Detail
	if !strings.HasPrefix(detail, "`projects/` はワークスペースの git が追跡している") || strings.Contains(detail, forgotNext) {
		t.Errorf("載せ忘れの文になった: %s", detail)
	}
	// git rm --cached に載せるパスは、空白を含むときだけ囲む。名指しのほうは囲まない。
	for _, w := range []string{"git mv projects apps", "`projects/app`、`projects/my lib`", "`git rm --cached projects/app 'projects/my lib'`"} {
		if !strings.Contains(detail, w) {
			t.Errorf("%q が無い: %s", w, detail)
		}
	}
	if strings.Contains(detail, "git rm -r --cached projects") {
		t.Errorf("載せ忘れの直し方が混ざった: %s", detail)
	}
}

// 索引にファイルがあれば、プロジェクトが 0 件でも、.gitignore に入っていても言う（追跡が先に決まる）。
func TestATrackedFileIsNamedEvenWhenIgnored(t *testing.T) {
	root, conf := placesWorkspace(t, "/projects/\n")
	put(t, root, "projects/foo.txt", "ワークスペース自身のソース\n")
	testkit.Git(t, root, "add", "-f", "projects/foo.txt")
	found := aboutProjects(conf, root)
	if len(found) != 1 || found[0].Kind != kindProjectsTracked || strings.Contains(found[0].Detail, forgotNext) {
		t.Errorf("%+v", found)
	}
}

// 追跡が無いとき、置き場の有無・空・リンクと無視の有無で、言うかが決まる。
func TestWhatIsSaidAboutAPlaceNothingTracks(t *testing.T) {
	root, conf := placesWorkspace(t, "")
	if got := aboutProjects(conf, root); len(got) != 0 {
		t.Errorf("置き場が無い: %+v", got)
	}
	expectUnignored := func(label string) {
		t.Helper()
		got := aboutProjects(conf, root)
		if len(got) != 1 || got[0].Severity != rules.Warn || got[0].Kind != kindProjectsUnignored {
			t.Errorf("%s: %+v", label, got)
		}
	}
	// 在るが空で、無視されていなければ言う。
	if err := os.MkdirAll(conf.Projects, 0o755); err != nil {
		t.Fatal(err)
	}
	expectUnignored("空")
	// シンボリックリンクでも言う（`projects/` で問うと git が断る）。
	if err := os.Remove(conf.Projects); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(t.TempDir(), conf.Projects); err == nil {
		expectUnignored("リンク")
		os.Remove(conf.Projects)
		if err := os.MkdirAll(conf.Projects, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	// 無視されていれば、空でもプロジェクトがあっても何も言わない。
	put(t, root, ".gitignore", "/.claude/\n/projects/\n")
	if got := aboutProjects(conf, root); len(got) != 0 {
		t.Errorf("無視されている空の置き場: %+v", got)
	}
	nested(t, root, "lib")
	if got := aboutProjects(conf, root); len(got) != 0 {
		t.Errorf("無視されている置き場のプロジェクト: %+v", got)
	}
}

// 変な --projects（空・ワークスペースの外）でも置き場の苦情は出さず、落ちない。
func TestOddProjectsFlagsSayNothing(t *testing.T) {
	root, _ := placesWorkspace(t, "")
	for _, projects := range []string{"", t.TempDir()} {
		if got := aboutProjects(&config.Settings{Projects: projects}, root); len(got) != 0 {
			t.Errorf("%q: %+v", projects, got)
		}
	}
}

// プロジェクトの .claude ディレクトリは言わず、その中のワークツリーは言う。
func TestAProjectsClaudeDirIsQuietButAWorktreeInsideIsNot(t *testing.T) {
	root, conf := placesWorkspace(t, "/projects/\n")
	nested(t, root, "lib")
	where := "(projects/lib)"
	about := func() []rules.Problem {
		var out []rules.Problem
		for _, p := range projectPlaces(conf, root) {
			if p.Rule == where {
				out = append(out, p)
			}
		}
		return out
	}
	claude := filepath.Join(root, "projects", "lib", ".claude")
	if err := os.MkdirAll(claude, 0o755); err != nil {
		t.Fatal(err)
	}
	if got := about(); len(got) != 0 {
		t.Errorf("%+v", got)
	}
	if err := os.MkdirAll(filepath.Join(claude, "worktrees", "feature-1-memo"), 0o755); err != nil {
		t.Fatal(err)
	}
	if got := about(); len(got) != 1 || !strings.Contains(got[0].Detail, "プロジェクトの中にワークツリーがある（feature-1-memo）") {
		t.Errorf("%+v", got)
	}
}
