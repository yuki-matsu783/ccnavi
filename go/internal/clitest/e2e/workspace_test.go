package e2e

import (
	"bytes"
	"context"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// このパッケージは、配る形の実行ファイル（dist/ccnavi）と配る sh を、使い捨てのワークスペースの上で外から呼ぶ（tests/e2e の写し）。
// dist/ccnavi が無ければ飛ばす。試すのはソースではなくその実行ファイルなので、go/ を直したら `sh scripts/ccnavi-build.sh` で組み直す。
// CCNAVI_TEST_SH_DIR で測る sh の置き場を、CCNAVI_TEST_DIST で組み立て済みの dist/ccnavi を差し替えられる。

// sources は測るものの出どころ。
type sources struct{ shDir, hookDir, dist string }

var (
	sourcesOnce sync.Once
	src         sources
	srcSkip     string
)

// sourcesFor は出どころを 1 度だけ探し、無ければ飛ばす。
func sourcesFor(t *testing.T) sources {
	t.Helper()
	sourcesOnce.Do(loadSources)
	if srcSkip != "" {
		t.Skip(srcSkip)
	}
	t.Logf("sh = %s\ndist = %s", src.shDir, src.dist)
	return src
}

func loadSources() {
	if !hasProgram("sh") {
		srcSkip = "sh が見つからない"
		return
	}
	if !hasProgram("git") {
		srcSkip = "git が見つからない"
		return
	}
	src.shDir = pathFromEnv(env.TestShDir, walkUp(filepath.Join(".ccnavi", "scripts"), true))
	src.hookDir = walkUp(filepath.Join(".claude", "hooks"), true)
	// 組み立ての置き場はワークツリーの中にもありうるので、ワークツリーの中も候補に入れる。
	src.dist = pathFromEnv(env.TestDist, walkUp(filepath.Join("dist", "ccnavi"), false))
	switch {
	case !isDirPath(src.shDir):
		srcSkip = fmt.Sprintf("sh の出どころが無い (%s)", src.shDir)
	case !isDirPath(src.dist):
		srcSkip = fmt.Sprintf("実行ファイルが組み立てられていない (%s)。sh scripts/ccnavi-build.sh で作る", src.dist)
	}
}

func hasProgram(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}

// walkUp は基準の根から上へ歩いて、rel を持つ所を返す。skipWorktrees なら `.claude/worktrees/` の下は候補にしない
// （ワークツリーから回したとき、ワークツリー自身の古いコピーを測らないため）。見つからなければ根の直下のパスを返す。
func walkUp(rel string, skipWorktrees bool) string {
	root := base.Root()
	here := root
	for {
		candidate := filepath.Join(here, rel)
		inside := skipWorktrees && inWorktree(here)
		if isDirPath(candidate) && !inside {
			return candidate
		}
		parent := filepath.Dir(here)
		if parent == here {
			return filepath.Join(root, rel)
		}
		here = parent
	}
}

// inWorktree は dir が `<ワークスペースルート>/.claude/worktrees/<名前>` か、その下か。
func inWorktree(dir string) bool {
	for d := dir; ; {
		if run.IsWorktreeDir(d) {
			return true
		}
		parent := filepath.Dir(d)
		if parent == d {
			return false
		}
		d = parent
	}
}

func pathFromEnv(name, fallback string) string {
	v := base.TestVar(name)
	if v == "" {
		return fallback
	}
	if filepath.IsAbs(v) {
		return v
	}
	return filepath.Join(base.Root(), v)
}

func isDirPath(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func isFilePath(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// writeFile は改行を変えずに書く（Windows でも LF のまま）。
func writeFile(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func copyFile(t *testing.T, from, to string) {
	t.Helper()
	info, err := os.Stat(from)
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(from)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(to, data, info.Mode().Perm()); err != nil {
		t.Fatal(err)
	}
}

func copyTree(t *testing.T, from, to string) {
	t.Helper()
	err := filepath.WalkDir(from, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(from, path)
		if err != nil {
			return err
		}
		dest := filepath.Join(to, rel)
		if d.IsDir() {
			return os.MkdirAll(dest, 0o755)
		}
		copyFile(t, path, dest)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

// filesUnder はそのディレクトリの下にあるファイルの相対パス（ソート済み）。無ければ空。
func filesUnder(dir string) []string {
	if !isDirPath(dir) {
		return nil
	}
	var out []string
	_ = filepath.WalkDir(dir, func(path string, d fs.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			rel, _ := filepath.Rel(dir, path)
			out = append(out, rel)
		}
		return nil
	})
	sort.Strings(out)
	return out
}

// logsUnder は、そのディレクトリの下にある記録（.log）の相対パス。
func logsUnder(dir string) []string {
	var out []string
	for _, name := range filesUnder(dir) {
		if strings.HasSuffix(name, ".log") {
			out = append(out, name)
		}
	}
	return out
}

// execute は子のプロセスを 1 回起こす。環境は run.Environment（走った機械の git の締め出し・CCNAVI_* の除去を含む）を明示して渡す。
// run.Sh と同じく、子の標準出力・標準エラーは LF に揃える。
func execute(t *testing.T, dir string, o run.Opts, program string, args ...string) run.Result {
	t.Helper()
	base.Uncache()
	ctx, cancel := context.WithTimeout(context.Background(), base.ExecTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, program, args...)
	cmd.Dir = dir
	cmd.Env = run.Environment(o)
	cmd.Stdin = strings.NewReader(o.Input)
	cmd.WaitDelay = 5 * time.Second
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if _, ok := err.(*exec.ExitError); err != nil && !ok {
		t.Fatalf("%s を起こせない: %v", program, err)
	}
	if ctx.Err() != nil {
		t.Fatalf("%s が %v で終わらない", program, base.ExecTimeout)
	}
	return run.Result{
		Code:   cmd.ProcessState.ExitCode(),
		Stdout: lfReplacer.Replace(stdout.String()),
		Stderr: lfReplacer.Replace(stderr.String()),
	}
}

var lfReplacer = strings.NewReplacer("\r\n", "\n", "\r", "\n")

// gitRun は素の git を打つ（見本を組むためだけ）。失敗は止める。標準出力を返す。
func gitRun(t *testing.T, dir string, args ...string) string {
	t.Helper()
	r := execute(t, dir, run.Opts{}, "git", args...)
	if r.Code != 0 {
		t.Fatalf("git %v が失敗した: stdout=%q stderr=%q", args, r.Stdout, r.Stderr)
	}
	return r.Stdout
}

// gitOut は git の標準出力を、末尾の空白だけ落として返す（行頭の空白は残す）。失敗すれば空。読み返すためだけに使う。
func gitOut(t *testing.T, dir string, args ...string) string {
	t.Helper()
	r := execute(t, dir, run.Opts{}, "git", args...)
	if r.Code != 0 {
		return ""
	}
	return strings.TrimRight(r.Stdout, " \t\r\n")
}

// makeRepo はコミットが 1 件ある使い捨てのリポジトリを作る。
func makeRepo(t *testing.T, dir string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	gitRun(t, dir, "init", "-q", "-b", "main")
	gitRun(t, dir, "config", "user.email", "t@example.invalid")
	gitRun(t, dir, "config", "user.name", "t")
	writeFile(t, filepath.Join(dir, "seed.txt"), "seed\n")
	gitRun(t, dir, "add", "seed.txt")
	gitRun(t, dir, "commit", "-q", "-m", "seed")
}

// buildWorkspace は道具を持つワークスペースを root に組む。実行ファイルと sh を実物で置き、projects の下に別々のリポジトリを置く。
func buildWorkspace(t *testing.T, s sources, root string, projects ...string) {
	t.Helper()
	makeRepo(t, root)
	scripts := filepath.Join(root, ".ccnavi", "scripts")
	entries, err := os.ReadDir(s.shDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".sh") {
			copyFile(t, filepath.Join(s.shDir, e.Name()), filepath.Join(scripts, e.Name()))
		}
	}
	// 守る場所の hook（`.claude/hooks/` の sh）は、コピーする版（CCNAVI_TEST_SH_DIR）を優先する。hook だけ別の版を測らないため。
	hooks, err := os.ReadDir(s.hookDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, h := range hooks {
		if h.IsDir() || !strings.HasSuffix(h.Name(), ".sh") {
			continue
		}
		hookSrc := filepath.Join(s.shDir, h.Name())
		if !isFilePath(hookSrc) {
			hookSrc = filepath.Join(s.hookDir, h.Name())
		}
		copyFile(t, hookSrc, filepath.Join(root, ".claude", "hooks", h.Name()))
	}
	copyTree(t, s.dist, filepath.Join(root, "dist", "ccnavi"))
	if err := os.MkdirAll(filepath.Join(root, ".ccnavi", "approved", "doing"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(root, ".gitignore"), "/logs/\n/projects/\n/dist/\n/.claude/worktrees/\n")
	gitRun(t, root, "add", ".gitignore", ".ccnavi/scripts", ".claude/hooks")
	gitRun(t, root, "commit", "-q", "-m", "tools")
	for _, p := range projects {
		makeRepo(t, filepath.Join(root, "projects", p))
	}
}

// fixture はワークスペース 1 つ。プロジェクト p1・p2、p1 から切った wp1、ワークスペースから切った w0。
// テストごとに組み直す（記録と木の状態を取り違えないため）。
type fixture struct {
	t               *testing.T
	src             sources
	tmp, root       string
	p1, p2, wp1, w0 string
}

// newFixture はプロジェクトのあるワークスペースを組む（tests/e2e の WorkspaceTest）。
func newFixture(t *testing.T) *fixture {
	t.Helper()
	f := buildFixture(t, "p1", "p2")
	gitRun(t, f.p1, "worktree", "add", "-q", f.wp1, "-b", "wp1")
	gitRun(t, f.root, "worktree", "add", "-q", f.w0, "-b", "w0")
	return f
}

// newModeAFixture は projects/ の無いワークスペースを組む（モード A）。w0 だけ切る。
func newModeAFixture(t *testing.T) *fixture {
	t.Helper()
	f := buildFixture(t)
	gitRun(t, f.root, "worktree", "add", "-q", f.w0, "-b", "w0")
	return f
}

func buildFixture(t *testing.T, projects ...string) *fixture {
	t.Helper()
	s := sourcesFor(t)
	tmp := base.TempDir(t)
	root := filepath.Join(tmp, "ws")
	buildWorkspace(t, s, root, projects...)
	return &fixture{
		t:    t,
		src:  s,
		tmp:  tmp,
		root: root,
		p1:   filepath.Join(root, "projects", "p1"),
		p2:   filepath.Join(root, "projects", "p2"),
		wp1:  filepath.Join(root, ".claude", "worktrees", "wp1"),
		w0:   filepath.Join(root, ".claude", "worktrees", "w0"),
	}
}

// sh は ワークスペースの .ccnavi/scripts/<name> を sh で起こす。cwd が空ならワークスペースルート。
func (f *fixture) sh(o run.Opts, name string, args ...string) run.Result {
	f.t.Helper()
	if o.Dir == "" {
		o.Dir = f.root
	}
	return run.Sh(f.t, filepath.Join(f.root, ".ccnavi", "scripts", name), args, o)
}

// clearLogs は記録を消す。テストの前に呼び、どの起こしが何を残したかを取り違えないようにする。
func (f *fixture) clearLogs() {
	for _, place := range []string{f.root, f.p1, f.p2, f.wp1, f.w0} {
		_ = os.RemoveAll(filepath.Join(place, "logs"))
	}
}

// assertLogsIn は、記録がワークスペースの下の狙った場所（rel）に出たこと。
func (f *fixture) assertLogsIn(t *testing.T, rel string, r run.Result) {
	t.Helper()
	place := filepath.Join(f.root, filepath.FromSlash(rel))
	if len(logsUnder(place)) == 0 {
		t.Errorf("%s に記録が無い。stdout=%q stderr=%q", rel, r.Stdout, r.Stderr)
	}
}

// assertNoLogsIn は、dir/logs に記録が出ていないこと（ワークスペースの外に出ていないこと）。
func assertNoLogsIn(t *testing.T, dir string) {
	t.Helper()
	if found := logsUnder(filepath.Join(dir, "logs")); len(found) != 0 {
		t.Errorf("%s/logs に記録が出ている（ワークスペースの外に出ている）: %v", dir, found)
	}
}

// assertGotPastTheRoot は根の導出より先へ進んだことを確かめる。根の導出が誤ると、スクリプトは実行ファイルを
// プロジェクトの中に探して失敗する。その手前で終わった実行を「外に出なかった」と数えると、検査の漏れが残ったまま通る。
func assertGotPastTheRoot(t *testing.T, r run.Result) {
	t.Helper()
	if strings.Contains(r.Stdout+r.Stderr, "実行ファイルが無い") {
		t.Fatal("根の導出に失敗している。この実行は中身を確かめていない")
	}
}
