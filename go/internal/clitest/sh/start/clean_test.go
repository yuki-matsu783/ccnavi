package start

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// ccnavi-clean.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。
//
// 確かめるのは 3 つ。決まった名前の生成物だけが消えること。`.claude/worktrees/` の直下の名前以外は受け付けないこと。未コミットの変更がある
// ワークツリーでは何も消さないこと。消すのは ccnavi の実行ファイル（`worktree clean`・`worktree drop`）。読み返すのは終了コード・出力と、
// ファイルシステムに残ったものだけ。

var cleanScripts = []string{"ccnavi-clean.sh", "ccnavi-user-clean-branch.sh"}

type cleanEnv struct {
	t         testing.TB
	dir       string
	ws        string
	worktrees string
	bin       string
}

func newClean(t testing.TB) *cleanEnv {
	t.Helper()
	shkit.Need(t, "sh")
	c := &cleanEnv{t: t, dir: base.TempDir(t)}
	c.ws = filepath.Join(c.dir, "ws")
	shkit.CopyScripts(t, c.ws, cleanScripts...)
	c.worktrees = filepath.Join(c.ws, ".claude", "worktrees")
	shkit.Mkdir(t, c.worktrees)
	c.bin = shkit.ExeWrapper(t, filepath.Join(c.dir, "bin"))
	return c
}

// run は script（ccnavi-clean.sh など）を cwd（空なら ws）で起こす。
func (c *cleanEnv) runIn(script, cwd string, env map[string]string, args ...string) run.Result {
	c.t.Helper()
	if cwd == "" {
		cwd = c.ws
	}
	return runScript(c.t, filepath.Join(c.ws, ".ccnavi", "scripts", script), cwd, merge(map[string]string{"CCNAVI_ENTRY_PATH": c.bin}, env), args...)
}

func (c *cleanEnv) clean(args ...string) run.Result {
	return c.runIn("ccnavi-clean.sh", "", nil, args...)
}

// makeShell は git の登録が外れたディレクトリ。生成物と、消えてはいけないものを混ぜて置く。
func (c *cleanEnv) makeShell(name string) string {
	c.t.Helper()
	top := filepath.Join(c.worktrees, name)
	for rel, text := range map[string]string{
		"src/a.txt":                         "keep",
		"node_modules/.pnpm/x/index.js":     "",
		"ext/package.json":                  "{}",
		"ext/out/extension.js":              "",
		"ext/out/node_modules/z/index.js":   "", // out ごと消える
		"ext/out-x/node_modules/w/index.js": "", // 並べると ext/out と ext/out/node_modules の間に来る。
		"ext/node_modules/y/index.js":       "",
		"ext/dist/bundle.js":                "",
		"ext/.cache/c":                      "",
		"docs/out/keep.txt":                 "keep", // package.json の隣ではない
		"docs/dist/keep.txt":                "keep",
		".venv/pyvenv.cfg":                  "",
		"pkg/__pycache__/m.pyc":             "",
		".pytest_cache/v/cache":             "",
		".ruff_cache/0.16.6/123/abc":        "",
	} {
		shkit.Write(c.t, filepath.Join(top, filepath.FromSlash(rel)), text)
	}
	return top
}

// filesUnder はファイルの相対パス（/ 区切り、辞書順）。
func filesUnder(t testing.TB, root string) []string {
	t.Helper()
	var out []string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !d.IsDir() {
			rel, _ := filepath.Rel(root, path)
			out = append(out, filepath.ToSlash(rel))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(out)
	return out
}

func join(list []string) string { return strings.Join(list, "\n") }

// ---- 生成物だけを消す側（`ccnavi-clean.sh <名前>`）の検査

func TestCleanRemovesOnlyGeneratedDirectories(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	top := c.makeShell("shell")
	r := code(t, c.clean("shell"), 0)
	eq(t, join(filesUnder(t, top)), join([]string{"docs/dist/keep.txt", "docs/out/keep.txt", "ext/package.json", "src/a.txt"}), "残ったファイル")
	has(t, r.Stdout, "removed node_modules", "removed ext/out\n", "removed ext/dist\n", "removed ext/.cache\n", "removed ext/out-x/node_modules")
	lacks(t, r.Stdout, "ext/out/node_modules")
}

func TestCleanWorkspaceRootIsFoundFromInsideAWorktree(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	top := c.makeShell("shell")
	code(t, c.runIn("ccnavi-clean.sh", filepath.Join(top, "src"), nil, "shell"), 0)
	truth(t, !shkit.Exists(filepath.Join(top, "node_modules")), "node_modules が残った")
}

func TestCleanDryRunListsAndKeepsEverything(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	top := c.makeShell("shell")
	before := filesUnder(t, top)
	r := code(t, c.clean("shell", "--dry-run"), 0)
	has(t, r.Stdout, "would remove node_modules", "would remove .venv")
	eq(t, join(filesUnder(t, top)), join(before), "ファイル")
}

func TestCleanNothingToRemove(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	shkit.Write(t, filepath.Join(c.worktrees, "empty", "a.txt"), "")
	has(t, code(t, c.clean("empty"), 0).Stdout, "削除するものはありません")
}

func TestCleanRejectsAnythingButAName(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	// 置き場の外に、消えてはいけない生成物を置いておく。
	outside := shkit.Write(t, filepath.Join(c.ws, "node_modules", "keep.js"), "")
	c.makeShell("shell")
	for _, arg := range []string{"..", ".", "../..", "shell/src", ".claude/worktrees/shell", `..\..`, "C:x"} {
		code(t, c.clean(arg), 2)
	}
	truth(t, shkit.Exists(outside), "置き場の外の生成物が消えた")
}

func TestCleanRejectsAMissingNameAndAnUnknownOption(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	c.makeShell("shell")
	for _, args := range [][]string{{}, {"nothing-here"}, {"shell", "--force"}, {"shell", "other"}} {
		code(t, c.clean(args...), 2)
	}
	truth(t, shkit.Exists(filepath.Join(c.worktrees, "shell", "node_modules")), "node_modules が消えた")
}

// linkDir はディレクトリへのリンクを作る。作れなければテストを飛ばす。
// Windows で symlink が作れないときは junction（mklink /J）を試す。
func linkDir(t testing.TB, target, link string) {
	t.Helper()
	err := os.Symlink(target, link)
	if err != nil && runtime.GOOS == "windows" {
		err = exec.Command("cmd", "/c", "mklink", "/J", link, target).Run()
	}
	if err != nil {
		t.Skipf("リンクが作れない: %v", err)
	}
}

func TestCleanDoesNotFollowLinks(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	precious := filepath.Join(c.dir, "precious")
	shkit.Write(t, filepath.Join(precious, "node_modules", "keep.js"), "")
	shkit.Write(t, filepath.Join(precious, "out", "keep.js"), "")
	top := c.makeShell("shell")
	linkDir(t, precious, filepath.Join(top, "linked"))
	// 生成物の名前をしたリンクは、リンクだけが消えて先は残る。
	linkDir(t, filepath.Join(precious, "out"), filepath.Join(top, "ext", "out-link"))
	linkDir(t, filepath.Join(precious, "node_modules"), filepath.Join(top, "src", "node_modules"))
	code(t, c.clean("shell"), 0)
	truth(t, shkit.Exists(filepath.Join(precious, "node_modules", "keep.js")), "リンクの先の node_modules が消えた")
	truth(t, shkit.Exists(filepath.Join(precious, "out", "keep.js")), "リンクの先の out が消えた")
	truth(t, !shkit.Exists(filepath.Join(top, "src", "node_modules")), "リンクが残った")
}

func TestCleanRejectsAWorktreeDirectoryThatIsALink(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	precious := filepath.Join(c.dir, "precious")
	shkit.Write(t, filepath.Join(precious, "node_modules", "keep.js"), "")
	linkDir(t, precious, filepath.Join(c.worktrees, "linked"))
	code(t, c.clean("linked"), 2)
	truth(t, shkit.Exists(filepath.Join(precious, "node_modules", "keep.js")), "リンクの先が消えた")
}

func TestCleanReportsWhatCouldNotBeRemoved(t *testing.T) {
	t.Parallel()
	// Windows は chmod で消せなくならず、root は権限を無視して消す。
	if runtime.GOOS == "windows" || os.Geteuid() == 0 {
		t.Skip("権限で消せない状態が作れない")
	}
	c := newClean(t)
	top := c.makeShell("shell")
	locked := filepath.Join(top, "node_modules", ".pnpm")
	if err := os.Chmod(locked, 0o555); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(locked, 0o755) })
	r := code(t, c.clean("shell"), 1)
	has(t, r.Stderr, "node_modules", "削除しきれませんでした")
	truth(t, !shkit.Exists(filepath.Join(top, ".venv")), ".venv が残った") // 他は消す
}

func TestCleanStopsOnUncommittedChangesThenCleansWhenClean(t *testing.T) {
	t.Parallel()
	shkit.Need(t, "git")
	c := newClean(t)
	git(t, c.ws, "init", "-q", "-b", "main")
	shkit.Identity(t, c.ws)
	shkit.Write(t, filepath.Join(c.ws, ".gitignore"), "node_modules/\n.claude/worktrees/\n")
	git(t, c.ws, "add", ".gitignore")
	git(t, c.ws, "commit", "-q", "-m", "seed")
	git(t, c.ws, "worktree", "add", "-q", ".claude/worktrees/wt", "-b", "wt", "main")
	top := filepath.Join(c.worktrees, "wt")
	shkit.Write(t, filepath.Join(top, "node_modules", "x.js"), "")
	shkit.Write(t, filepath.Join(top, "draft.txt"), "書きかけ")
	r := code(t, c.clean("wt"), 1)
	has(t, r.Stderr, "draft.txt")
	truth(t, shkit.Exists(filepath.Join(top, "node_modules", "x.js")), "node_modules が消えた")
	shkit.Remove(t, filepath.Join(top, "draft.txt"))
	code(t, c.clean("wt"), 0)
	truth(t, !shkit.Exists(filepath.Join(top, "node_modules")), "node_modules が残った")
}

// ---- `.git` があるときの確かめ。読めない・指す先が消えている、の扱いを分ける。

func TestCleanAGitfilePointingNowhereIsTreatedAsALeftover(t *testing.T) {
	t.Parallel()
	shkit.Need(t, "git")
	c := newClean(t)
	top := c.makeShell("shell")
	shkit.Write(t, filepath.Join(top, ".git"), "gitdir: /nonexistent/gitdir\n")
	r := code(t, c.clean("shell"), 0)
	has(t, r.Stderr, "git の登録が残っていない")
	truth(t, !shkit.Exists(filepath.Join(top, "node_modules")), "node_modules が残った")
}

func TestCleanAnUnreadableGitStateStopsWithoutRemovingAnything(t *testing.T) {
	t.Parallel()
	shkit.Need(t, "git")
	c := newClean(t)
	top := c.makeShell("shell")
	shkit.Mkdir(t, filepath.Join(top, ".git")) // 中身の無い .git。git status は失敗する
	before := filesUnder(t, top)
	r := code(t, c.clean("shell"), 1)
	has(t, r.Stderr, "git の状態を読めない")
	eq(t, join(filesUnder(t, top)), join(before), "ファイル")
}

func TestCleanAMissingExecutableIsAnEnvironmentError(t *testing.T) {
	t.Parallel()
	c := newClean(t)
	c.makeShell("shell")
	r := c.runIn("ccnavi-clean.sh", "", map[string]string{"CCNAVI_ENTRY_PATH": filepath.Join(c.dir, "none"), "PATH": "/usr/bin:/bin"}, "shell")
	code(t, r, 2)
	has(t, r.Stderr, "実行ファイルが見つかりません")
}

// ---- `--worktree <名前>`。掃除とワークツリーの削除を実行ファイル（`worktree drop`）に任せる。

// newCleanWorktree は git のワークスペースと、ワークツリー wt（node_modules 入り）を組む。
func newCleanWorktree(t testing.TB) (*cleanEnv, string) {
	t.Helper()
	shkit.Need(t, "git")
	c := newClean(t)
	base.PutMessages(t, c.ws)
	git(t, c.ws, "init", "-q", "-b", "main")
	shkit.Identity(t, c.ws)
	shkit.Write(t, filepath.Join(c.ws, ".gitignore"), "node_modules/\n.claude/worktrees/\nlogs/\n")
	git(t, c.ws, "add", ".gitignore")
	git(t, c.ws, "commit", "-q", "-m", "seed")
	git(t, c.ws, "worktree", "add", "-q", ".claude/worktrees/wt", "-b", "wt", "main")
	top := filepath.Join(c.ws, ".claude", "worktrees", "wt")
	shkit.Write(t, filepath.Join(top, "node_modules", "x.js"), "")
	return c, top
}

func (c *cleanEnv) cleanBranch(args ...string) run.Result {
	return c.runIn("ccnavi-user-clean-branch.sh", "", nil, args...)
}

func branchWt(t testing.TB, ws string) string {
	t.Helper()
	return strings.TrimSpace(strings.TrimLeft(git(t, ws, "branch", "--list", "wt"), "+* "))
}

func TestCleanRemovesTheWorktreeFromOutsideAndKeepsTheBranch(t *testing.T) {
	t.Parallel()
	c, top := newCleanWorktree(t)
	r := code(t, c.clean("--worktree", "wt"), 0)
	has(t, r.Stdout, "ワークツリー wt を削除しました")
	truth(t, !shkit.Exists(top), "ワークツリーが残った")
	eq(t, branchWt(t, c.ws), "wt", "ブランチ")
}

func TestCleanKeepsTheWorktreeWhenTheCwdIsInside(t *testing.T) {
	t.Parallel()
	c, top := newCleanWorktree(t)
	r := code(t, c.runIn("ccnavi-clean.sh", top, nil, "--worktree", "wt"), 1)
	has(t, r.Stdout, "cwd がワークツリー wt の中にあるので削除しませんでした", "ccnavi-clean.sh --worktree wt")
	truth(t, shkit.Exists(filepath.Join(top, "node_modules", "x.js")), "node_modules が消えた")
}

func TestCleanRejectsPathsAndDryRunWithWorktree(t *testing.T) {
	t.Parallel()
	c, top := newCleanWorktree(t)
	for _, args := range [][]string{{"--worktree", "wt", "--dry-run"}, {"--worktree", "../wt"}, {"--worktree"}} {
		code(t, c.clean(args...), 2)
	}
	truth(t, shkit.IsDir(top), "ワークツリーが消えた")
}

// 記録に無いブランチは、確認の有無によらず消さない（git branch -D の代わりに使わせない）。
func TestCleanBranchRefusesABranchCcnaviDidNotRecord(t *testing.T) {
	t.Parallel()
	c, _ := newCleanWorktree(t)
	for _, args := range [][]string{{"wt"}, {"wt", "--confirmed"}, {"--confirmed", "wt"}} {
		r := code(t, c.cleanBranch(args...), 1)
		has(t, r.Stderr, "記録したブランチではない")
	}
	eq(t, branchWt(t, c.ws), "wt", "ブランチ")
}

func TestCleanBranchOptionsAreChecked(t *testing.T) {
	t.Parallel()
	c, _ := newCleanWorktree(t)
	for _, args := range [][]string{{"wt", "--worktree"}, {"wt", "--dry-run"}, {"wt", "other"}, {"../wt"}, {"--confirmed"}, {}} {
		code(t, c.cleanBranch(args...), 2)
	}
}

// hook の止めが ask に下がっても、ccnavi-clean.sh の --branch と --confirmed は未知のオプションとして 2 で拒否し、何も消さない。
func TestCleanTheRetiredBranchModeIsRefusedAsAnUnknownOption(t *testing.T) {
	t.Parallel()
	c, top := newCleanWorktree(t)
	for _, args := range [][]string{{"--branch", "wt"}, {"--branch", "wt", "--confirmed"}, {"--confirmed", "wt"}, {"--confirmed"}, {"wt", "--branch"}} {
		r := code(t, c.clean(args...), 2)
		has(t, r.Stderr, "は指定できません")
	}
	truth(t, shkit.Exists(filepath.Join(top, "node_modules", "x.js")), "node_modules が消えた")
	eq(t, branchWt(t, c.ws), "wt", "ブランチ")
}
