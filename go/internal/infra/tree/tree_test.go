package tree

import (
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"unicode"
)

func realTemp(t *testing.T) string {
	t.Helper()
	d, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return d
}

func swapcase(s string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsUpper(r) {
			return unicode.ToLower(r)
		}
		return unicode.ToUpper(r)
	}, s)
}

func git(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_NOSYSTEM=1")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

func initRepo(t *testing.T, dir string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	git(t, dir, "init", "--quiet", "-b", "main")
	git(t, dir, "commit", "--quiet", "--allow-empty", "-m", "init")
}

// ツリーの判定はどの機械でも大文字小文字を区別しない（Windows で clone したときだけ壊れないよう、Linux でも同じにする）。
func TestCaseFolding(t *testing.T) {
	root := realTemp(t)
	main := MainTree(root)
	swapped := swapcase(root)

	if found, ok := TreeOf(root, filepath.Join(swapped, "src", "a.py"), ""); !ok || found.Root != main.Root {
		t.Errorf("TreeOf(表記違い) = %+v %v", found, ok)
	}
	if got := Relative(main, filepath.Join(swapped, "README.md")); got != "README.md" {
		t.Errorf("Relative(表記違い) = %q", got)
	}
	if got := Relative(main, swapped); got != "" {
		t.Errorf("Relative(ルートの表記違い) = %q", got)
	}

	base := filepath.Join(root, WorktreesDir)
	if err := os.MkdirAll(filepath.Join(base, "I0001-01-01"), 0o755); err != nil {
		t.Fatal(err)
	}
	if got := WorktreePath(root, "i0001-01-01"); got != filepath.Join(base, "I0001-01-01") {
		t.Errorf("WorktreePath(在る) = %q", got)
	}
	if got := WorktreePath(root, "i0002"); got != filepath.Join(base, "i0002") {
		t.Errorf("WorktreePath(無い) = %q", got)
	}
}

func TestLookup(t *testing.T) {
	index := map[string]string{"I0001-01-01": "a", "i0002-01-01": "b"}
	for name, want := range map[string]string{"I0001-01-01": "a", "i0001-01-01": "a", "I0002-01-01": "b", "i0003-01-01": ""} {
		if got, _ := Lookup(index, name); got != want {
			t.Errorf("Lookup(%q) = %q, want %q", name, got, want)
		}
	}
	// 綴りまで同じキーを先に返す。
	if got, _ := Lookup(map[string]string{"I0001-01-01": "A", "i0001-01-01": "B"}, "i0001-01-01"); got != "B" {
		t.Errorf("Lookup(綴りまで同じ) = %q", got)
	}
}

func TestPartsUnderFilesystemRoot(t *testing.T) {
	// 無いパスを使い、ルート直下の分割だけを見る（macOS の /etc は /private/etc へのリンク）。
	name := "ccnavi-no-such-top-level-dir"
	sep := string(filepath.Separator)
	if _, err := os.Lstat(sep + name); err == nil {
		t.Skip("同じ名前が在る")
	}
	got, ok := PartsUnder(sep, sep+name+sep+"x")
	if !ok || !reflect.DeepEqual(got, []string{name, "x"}) {
		t.Errorf("PartsUnder = %q %v", got, ok)
	}
}

func TestOriginHeadFromLinkedWorktree(t *testing.T) {
	base := realTemp(t)
	repo := filepath.Join(base, "repo")
	initRepo(t, repo)
	git(t, repo, "update-ref", "refs/remotes/origin/trunk", "HEAD")
	git(t, repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
	linked := filepath.Join(base, "linked")
	git(t, repo, "worktree", "add", "--quiet", "-b", "work", linked)

	// リンクされたワークツリーの git ディレクトリには refs/remotes が無いので、共通の側を読む。
	for _, root := range []string{repo, linked} {
		if got := OriginHead(root); got != "trunk" {
			t.Errorf("OriginHead(%s) = %q", root, got)
		}
	}
	if got, ok := BranchOf(linked); !ok || got != "work" {
		t.Errorf("BranchOf = %q %v", got, ok)
	}
	git(t, repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
	for _, root := range []string{repo, linked} {
		if got := OriginHead(root); got != "" {
			t.Errorf("OriginHead(%s) = %q, want 空", root, got)
		}
	}
}

func TestOriginRepo(t *testing.T) {
	cases := map[string]string{
		"git@gitlab.example.com:group/sub/proj.git": "sub/proj",
		"https://gitlab.example.com/group/proj.git": "group/proj",
		"https://gitlab.example.com/group/proj/":    "group/proj",
		"ssh://git@host:2222/a/b.git":               "a/b",
		"/srv/git/a/b.git":                          "a/b",
		"host/only":                                 "",
	}
	for url, want := range cases {
		if got := repoOfURL(url); got != want {
			t.Errorf("repoOfURL(%q) = %q, want %q", url, got, want)
		}
	}
	repo := realTemp(t)
	initRepo(t, repo)
	git(t, repo, "remote", "add", "origin", "git@host:team/app.git")
	if got := OriginRepo(repo); got != "team/app" {
		t.Errorf("OriginRepo = %q", got)
	}
}

// 元のプロジェクトを失ったワークツリーは数えず、中のパスはワークスペースルートとして判定される。
func TestWorktreesWithoutProject(t *testing.T) {
	base := realTemp(t)
	root := filepath.Join(base, "ws")
	projects := filepath.Join(root, "projects")
	initRepo(t, root)
	wt := filepath.Join(root, ".claude", "worktrees")

	gone := filepath.Join(projects, "gone")
	initRepo(t, gone)
	gonePath := filepath.Join(wt, "gone-target")
	git(t, gone, "worktree", "add", "--quiet", gonePath, "-b", "gone-target")
	goneGitdir, _ := writtenGitdir(gonePath)
	if err := os.RemoveAll(gone); err != nil {
		t.Fatal(err)
	}

	broken := filepath.Join(projects, "broken")
	initRepo(t, broken)
	brokenPath := filepath.Join(wt, "broken-backref")
	git(t, broken, "worktree", "add", "--quiet", brokenPath, "-b", "broken-backref")
	brokenGitdir, _ := writtenGitdir(brokenPath)
	if err := os.WriteFile(filepath.Join(brokenGitdir, "gitdir"), []byte(filepath.Join(base, "elsewhere", ".git")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	normal := filepath.Join(wt, "normal-tree")
	git(t, root, "worktree", "add", "--quiet", normal, "-b", "normal-tree")
	if err := os.MkdirAll(filepath.Join(wt, "plain-dir", "src"), 0o755); err != nil {
		t.Fatal(err)
	}

	owners := append([]Tree{MainTree(root)}, Projects(projects)...)
	for path, want := range map[string]bool{gonePath: false, brokenPath: false, normal: true} {
		if _, ok := OwnerOf(path, owners); ok != want {
			t.Errorf("OwnerOf(%s) = %v, want %v", path, ok, want)
		}
	}
	for _, path := range []string{gonePath, brokenPath} {
		found, ok := TreeOf(root, filepath.Join(path, "src", "a.py"), projects)
		if !ok || found.Name != Main || found.Kind != kindMain {
			t.Errorf("TreeOf(%s) = %+v %v", path, found, ok)
		}
	}
	var names []string
	for _, w := range Worktrees(root, projects) {
		names = append(names, w.Name)
	}
	if !reflect.DeepEqual(names, []string{"normal-tree"}) {
		t.Errorf("Worktrees = %q", names)
	}

	got := map[string]ProjectlessWorktree{}
	for _, w := range WorktreesWithoutProject(root, projects) {
		got[w.Name] = w
	}
	if len(got) != 2 || got["gone-target"].gitdirExists || !got["broken-backref"].gitdirExists ||
		got["gone-target"].gitdir != goneGitdir {
		t.Errorf("WorktreesWithoutProject = %+v", got)
	}
}
