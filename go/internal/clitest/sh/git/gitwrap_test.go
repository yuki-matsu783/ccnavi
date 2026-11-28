package git

import (
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/ospath"
)

// git のラッパースクリプト（ccnavi-git.sh）の受入テスト。外からスクリプトを動かす。読み返すのは標準出力・標準エラー・終了コードと、
// logs/ に残った記録だけ。中の関数も変数も見ないので、書き方が変わってもテストは真であり続ける。
// 使い捨ての git リポジトリを毎回作る（このリポジトリ自身で走らせると、走った機械のワークツリーのことを報告するため）。
//
// 引数だけで決まる拒否と、その文が言うことは、実行ファイルの表が見る（go/internal/tickets/lifecycle/gitwrap の
// TestCheckMatchesTheWrapper・TestJudgeByTheArguments）。ここで見るのは、拒否の通し（終了コード 2 で git を動かさない）、
// git の状態で決まる形、通した git の結果と記録。

type wrapFix struct {
	t   *testing.T
	dir string
}

// newWrap はコミットが 1 件あり、追跡外のファイルが 1 件ある使い捨てのリポジトリ。ワークスペースルートにもする。
// ラッパーは .ccnavi/scripts/ccnavi-common.sh を持つディレクトリを cwd から上へ探して根を決める（ccnavi_workspace）ので、
// それが無いと「ワークスペースの外」として断られ、判定まで届かない。
func newWrap(t *testing.T) *wrapFix {
	t.Helper()
	shkit.Need(t, "sh", "git")
	dir := base.TempDir(t)
	shkit.Write(t, filepath.Join(dir, ".ccnavi", "scripts", "ccnavi-common.sh"), "")
	initRepo(t, dir, "")
	var lines []string
	for n := 0; n < 200; n++ {
		lines = append(lines, "line "+strconv.Itoa(n))
	}
	shkit.Write(t, filepath.Join(dir, "tracked.txt"), strings.Join(lines, "\n")+"\n")
	git(t, dir, "add", "tracked.txt")
	git(t, dir, "commit", "-q", "-m", "seed")
	shkit.Write(t, filepath.Join(dir, "untracked.txt"), "x\n")
	return &wrapFix{t: t, dir: dir}
}

func (w *wrapFix) join(parts ...string) string {
	return filepath.Join(append([]string{w.dir}, parts...)...)
}

// runIn はラッパーを cwd で動かす。
func (w *wrapFix) runIn(cwd string, env map[string]string, args ...string) run.Result {
	w.t.Helper()
	merged := map[string]string{"CCNAVI_ENTRY_PATH": sharedWrapper(w.t)}
	for k, v := range env {
		merged[k] = v
	}
	return run.Sh(w.t, filepath.Join(base.ScriptsDir(), "ccnavi-git.sh"), args, run.Opts{Dir: cwd, Env: merged})
}

func (w *wrapFix) run(args ...string) run.Result { w.t.Helper(); return w.runIn(w.dir, nil, args...) }

func (w *wrapFix) runEnv(env map[string]string, args ...string) run.Result {
	w.t.Helper()
	return w.runIn(w.dir, env, args...)
}

// makeBare は送り先の空のリポジトリを、その回だけの場所に作る。
func (w *wrapFix) makeBare() string {
	w.t.Helper()
	bare := filepath.Join(base.TempDir(w.t), "origin.git")
	git(w.t, w.dir, "init", "-q", "--bare", bare)
	return bare
}

func (w *wrapFix) addWorktree(name string) string {
	w.t.Helper()
	path := w.join(".claude", "worktrees", name)
	git(w.t, w.dir, "worktree", "add", "-q", path, "-b", name)
	return path
}

// logs はログの中身を新しい順に返す（診断ログの置き場 logs/diag/ はディレクトリなので数えない）。
func (w *wrapFix) logs() []string {
	w.t.Helper()
	dir := w.join("logs")
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var files []string
	for _, e := range entries {
		if e.Type().IsRegular() {
			files = append(files, e.Name())
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(files)))
	var out []string
	for _, name := range files {
		out = append(out, shkit.Read(w.t, filepath.Join(dir, name)))
	}
	return out
}

func (w *wrapFix) assertNoLogs() {
	w.t.Helper()
	if logs := w.logs(); len(logs) != 0 {
		w.t.Errorf("拒否したのに git が走って記録が残っている: %q", logs)
	}
}

// reject は、拒否が終了コード 2 で、git を 1 度も動かさないことを確かめて結果を返す。
func (w *wrapFix) reject(args ...string) run.Result {
	w.t.Helper()
	r := w.run(args...)
	if r.Code != 2 || !strings.Contains(r.Stderr, "ccnavi-git:") || r.Stdout != "" {
		w.t.Errorf("%v: code = %d, stdout = %q, stderr = %q", args, r.Code, r.Stdout, r.Stderr)
	}
	w.assertNoLogs()
	return r
}

func (w *wrapFix) succeed(r run.Result, what string) {
	w.t.Helper()
	if r.Code != 0 {
		w.t.Fatalf("%s: code = %d\n%s%s", what, r.Code, r.Stdout, r.Stderr)
	}
}

func (w *wrapFix) refused(r run.Result, what string) run.Result {
	w.t.Helper()
	if r.Code != 2 {
		w.t.Fatalf("%s: code = %d (2 のはず)\n%s%s", what, r.Code, r.Stdout, r.Stderr)
	}
	return r
}

func (w *wrapFix) branch(dir string) string { return gitT(w.t, dir, "branch", "--show-current") }

// ---- 拒否

// 統合先へ直接は送らない。統合はユーザがマージリクエストで行う。取り返しのつかない形の push は通さない。
func TestGitWrapPushRejections(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	if r := w.reject("push", "origin", "main"); !strings.Contains(r.Stderr, "統合") {
		t.Errorf("統合先の案内が無い: %q", r.Stderr)
	}
	git(t, w.dir, "checkout", "--quiet", "-b", "i0001")
	for _, args := range [][]string{
		{"push", "--force"}, {"push", "-f", "origin"}, {"push", "--force-with-lease"},
		{"push", "--delete", "origin", "i0001"}, {"push", "--all"}, {"push", "--mirror"}, {"push", "--tags"},
		{"push", "--no-verify"}, {"push", "origin", "HEAD:main"}, {"push", "origin", "other"},
	} {
		w.reject(args...)
	}
}

func TestGitWrapTheMissingDestinationRejectionNamesTheWorktreeForm(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	r := w.reject("worktree", "add")
	if !strings.Contains(r.Stderr, "ccnavi-git.sh worktree add .claude/worktrees/<名前> -b <名前>") {
		t.Errorf("stderr = %q", r.Stderr)
	}
}

// ---- 通す

// rules.yml が rm -rf の代わりに名指しで勧める経路。勧めた先が通らないと、案内どおりに進めなくなる。
func TestGitWrapRmRemovesATrackedFile(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	w.succeed(w.run("rm", "tracked.txt"), "rm")
	if shkit.Exists(w.join("tracked.txt")) {
		t.Error("tracked.txt が残っている")
	}
}

func TestGitWrapCommitGoesThrough(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	w.succeed(w.run("add", "untracked.txt"), "add")
	r := w.run("commit", "-m", "追加した")
	w.succeed(r, "commit")
	if !strings.HasPrefix(r.Stdout, "ok  git commit") {
		t.Errorf("stdout = %q", r.Stdout)
	}
}

// worktree の手順は、main が先に進んだ状態から取り込む形を必ず通る。ここを止めると、枝分かれしたブランチが永久に統合されない。
func TestGitWrapAMergeThatIsNotAFastForwardGoesThrough(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	w.succeed(w.run("checkout", "-b", "topic"), "checkout")
	shkit.Write(t, w.join("topic.txt"), "topic\n")
	w.run("add", "topic.txt")
	w.run("commit", "-m", "topic")
	w.run("checkout", "-")
	shkit.Write(t, w.join("main.txt"), "main\n")
	w.run("add", "main.txt")
	w.run("commit", "-m", "main")
	w.succeed(w.run("merge", "topic", "--no-edit"), "merge")
	if !shkit.Exists(w.join("topic.txt")) {
		t.Error("topic.txt が取り込まれていない")
	}
}

// SessionStart と start.sh の早送りが打つ形。ignored のファイルを上書きしないための旗。
func TestGitWrapAFastForwardMergeWithNoOverwriteIgnoreGoesThrough(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	w.succeed(w.run("checkout", "-b", "topic"), "checkout")
	shkit.Write(t, w.join("topic.txt"), "topic\n")
	w.run("add", "topic.txt")
	w.run("commit", "-m", "topic")
	w.run("checkout", "-")
	w.succeed(w.run("merge", "--ff-only", "--no-overwrite-ignore", "topic"), "merge")
	if !shkit.Exists(w.join("topic.txt")) {
		t.Error("topic.txt が取り込まれていない")
	}
}

// 作業用のブランチは、そのままの名前で送れる（レビューはマージリクエストの実物に結ぶので、そこまではエージェントが進められる）。
func TestGitWrapPushSendsTheCurrentBranch(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	bare := w.makeBare()
	git(t, w.dir, "remote", "add", "origin", bare)
	git(t, w.dir, "checkout", "--quiet", "-b", "i0001")
	w.succeed(w.run("push", "-u", "origin", "i0001"), "push")
	if landed, here := gitT(t, w.dir, "--git-dir", bare, "rev-parse", "i0001"), gitT(t, w.dir, "rev-parse", "HEAD"); landed != here {
		t.Errorf("landed = %s, HEAD = %s", landed, here)
	}
}

// 子チケットのワークツリーからは送れない。親が合流してから親のツリーで送る。見分けるのは承認済みチケットに parent: があるかだけ。
// 承認済みチケットの無いツリーと親の承認済みチケットを持つツリーは通す。
func TestGitWrapPushFromAChildTicketWorktreeIsRejected(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	bare := w.makeBare()
	git(t, w.dir, "remote", "add", "origin", bare)
	shkit.Write(t, w.join(".ccnavi", "approved", "doing", "i0001.md"), "---\nversion: 1\nticket: i0001\n---\n")
	shkit.Write(t, w.join(".ccnavi", "approved", "doing", "i0001-01-01.md"), "---\nversion: 1\nticket: i0001-01-01\nparent: i0001\nphase: 1\n---\n")
	// クローズした子。承認済みチケットは done/ に動いているが、ツリーはまだ子のもの。
	shkit.Write(t, w.join(".ccnavi", "approved", "done", "i0001-01-02.md"), "---\nversion: 1\nticket: i0001-01-02\nparent: i0001\nphase: 1\n---\n")
	trees := map[string]string{}
	for _, name := range []string{"i0001", "i0001-01-01", "i0001-01-02", "free"} {
		trees[name] = w.addWorktree(name)
	}
	push := func(name string) run.Result { return w.runIn(trees[name], nil, "push", "-u", "origin", name) }
	for _, name := range []string{"i0001-01-01", "i0001-01-02"} {
		r := w.refused(push(name), name)
		if !strings.Contains(r.Stderr, "子チケット") || !strings.Contains(r.Stderr, "i0001") {
			t.Errorf("%s: stderr = %q", name, r.Stderr)
		}
	}
	w.assertNoLogs()
	for _, name := range []string{"i0001", "free"} {
		w.succeed(push(name), name)
		if _, code := gitTry(w.dir, "--git-dir", bare, "rev-parse", "--verify", name); code != 0 {
			t.Errorf("%s が届いていない", name)
		}
	}
}

// 子の承認済みチケットは親のワークツリーに置かれる（approval.home_dir）。そこも見る。
// ワークスペースルートには何も無く、親のツリーにだけ子の承認済みチケットがある形。
func TestGitWrapPushIsRejectedWhenTheChildTicketLivesInTheParentTree(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	git(t, w.dir, "remote", "add", "origin", w.makeBare())
	trees := map[string]string{}
	for _, name := range []string{"i0001", "i0001-01-01"} {
		trees[name] = w.addWorktree(name)
	}
	doing := filepath.Join(trees["i0001"], ".ccnavi", "approved", "doing")
	shkit.Write(t, filepath.Join(doing, "i0001.md"), "---\nversion: 1\nticket: i0001\n---\n")
	shkit.Write(t, filepath.Join(doing, "i0001-01-01.md"), "---\nversion: 1\nticket: i0001-01-01\nparent: i0001\nphase: 1\n---\n")
	push := func(name string) run.Result { return w.runIn(trees[name], nil, "push", "-u", "origin", name) }
	if r := w.refused(push("i0001-01-01"), "child"); !strings.Contains(r.Stderr, "子チケット") {
		t.Errorf("stderr = %q", r.Stderr)
	}
	w.succeed(push("i0001"), "parent")
}

// オプションは許可リストで読む。git の parse-options は長いオプションの略（--force-c → --force-create）を受けるので、
// 止める名前を並べるやり方では止められずに通る。まとめた短いオプション（-qbnew）は 1 字ずつ読み、値を取る字の後ろは値として扱う。
// ここでは通す形（ブランチを移る・作る・戻す）が通ることを見る。
func TestGitWrapPlainForms(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	home := w.branch(w.dir)
	git(t, w.dir, "branch", "topic")
	for _, args := range [][]string{
		{"checkout", "-b", "new1"}, {"checkout", "-qbnew2", home}, {"switch", "-cnew3"},
		{"switch", "--create", "new4", home}, {"checkout", home}, {"restore", "--staged", "tracked.txt"},
	} {
		w.succeed(w.run(args...), strings.Join(args, " "))
	}
	if got := w.branch(w.dir); got != home {
		t.Errorf("branch = %q, want %q", got, home)
	}
}

// ---- merge-file

// merge-file は結果を標準出力に出す形 (-p / --stdout) だけ通す。付けないと git は 1 つめのファイルを直に書き換える。
// ファイルを書くのは Edit / Write に任せ、hook が行き先を見られるようにする。
func (w *wrapFix) conflict() string {
	w.t.Helper()
	home := w.branch(w.dir)
	git(w.t, w.dir, "checkout", "-q", "-b", "other")
	shkit.Write(w.t, w.join("tracked.txt"), "other\n")
	git(w.t, w.dir, "commit", "-q", "-am", "other")
	git(w.t, w.dir, "checkout", "-q", home)
	shkit.Write(w.t, w.join("tracked.txt"), "mine\n")
	git(w.t, w.dir, "commit", "-q", "-am", "mine")
	gitTry(w.dir, "merge", "other")
	if status := git(w.t, w.dir, "status", "--porcelain"); !strings.Contains(status, "UU tracked.txt") {
		w.t.Fatalf("衝突していない: %q", status)
	}
	return shkit.Read(w.t, w.join("tracked.txt"))
}

func TestGitWrapMergeFileUnionToStdoutWritesNothing(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	shkit.Write(t, w.join("current.txt"), "a\nmine\nc\n")
	shkit.Write(t, w.join("base.txt"), "a\nb\nc\n")
	shkit.Write(t, w.join("other.txt"), "a\ntheirs\nc\n")
	r := w.run("merge-file", "-p", "--union", "-L", "ours", "-L", "base", "-L", "theirs", "current.txt", "base.txt", "other.txt")
	w.succeed(r, "merge-file")
	if !strings.HasPrefix(r.Stdout, "ok  git merge-file") || !strings.Contains(r.Stdout, "mine\ntheirs\n") {
		t.Errorf("stdout = %q", r.Stdout)
	}
	if got := shkit.Read(t, w.join("current.txt")); got != "a\nmine\nc\n" {
		t.Errorf("current.txt が書き換わった: %q", got)
	}
}

// 使い方が勧める 1 回で済む形。一時ファイルを作らずに両方を取り込んだ結果を読む。
func TestGitWrapMergeFileUnionOfTheThreeStagesWritesNothing(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	help, _ := exec.Command("git", "merge-file", "-h").CombinedOutput()
	if !strings.Contains(string(help), "object-id") {
		t.Skip("この git の merge-file は --object-id を知らない")
	}
	conflicted := w.conflict()
	before := git(t, w.dir, "count-objects")
	r := w.run("merge-file", "-p", "--union", "--object-id", ":2:tracked.txt", ":1:tracked.txt", ":3:tracked.txt")
	w.succeed(r, "merge-file")
	if !strings.HasPrefix(r.Stdout, "ok  git merge-file") || !strings.Contains(r.Stdout, "mine\nother\n") || strings.Contains(r.Stdout, "<<<<<<<") {
		t.Errorf("stdout = %q", r.Stdout)
	}
	if shkit.Read(t, w.join("tracked.txt")) != conflicted || git(t, w.dir, "count-objects") != before {
		t.Error("ファイルかオブジェクトが書き換わった")
	}
}

func TestGitWrapTheThreeStagesOfAConflictCanBeRead(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	w.conflict()
	for stage, want := range map[string]string{"1": "line 0", "2": "mine", "3": "other"} {
		r := w.run("show", ":"+stage+":tracked.txt")
		w.succeed(r, "show stage "+stage)
		if !strings.Contains(r.Stdout, want) {
			t.Errorf("stage %s: %q が無い", stage, want)
		}
	}
}

// ---- 出力

func TestGitWrapSuccessReturnsASummaryLineAndTheBody(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	r := w.run("status", "--porcelain")
	w.succeed(r, "status")
	first := strings.SplitN(r.Stdout, "\n", 2)[0]
	if !strings.HasPrefix(first, "ok  git status") || !strings.Contains(first, "log=logs/git-") || !strings.Contains(r.Stdout, "untracked.txt") {
		t.Errorf("stdout = %q", r.Stdout)
	}
}

func TestGitWrapLongOutputIsCappedAndTheRestStaysInTheLog(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	r := w.runEnv(map[string]string{"CCNAVI_GIT_MAX_LINES": "5"}, "show", "HEAD")
	w.succeed(r, "show")
	body := strings.Split(strings.TrimRight(r.Stdout, "\n"), "\n")
	if len(body) != 7 { // 要約 + 本文 5 行 + 残りの案内
		t.Errorf("%d 行: %q", len(body), r.Stdout)
	} else if !strings.HasPrefix(body[len(body)-1], "... 残り ") {
		t.Errorf("最後の行 = %q", body[len(body)-1])
	}
	logs := w.logs()
	if len(logs) == 0 || !strings.Contains(logs[0], "line 199") || !strings.Contains(logs[0], "$ git show --no-ext-diff HEAD") {
		t.Errorf("全量か実行した引数が記録に残っていない: %q", logs)
	}
	if strings.Contains(r.Stdout, "line 199") {
		t.Error("抑えたはずの本文が出ている")
	}
}

func TestGitWrapFailureReturnsExit1AndTheTail(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	r := w.run("show", "no-such-ref")
	if r.Code != 1 || !strings.HasPrefix(r.Stdout, "fail  git show") || !strings.Contains(strings.SplitN(r.Stdout, "\n", 2)[0], "log=logs/git-") {
		t.Errorf("code = %d, stdout = %q", r.Code, r.Stdout)
	}
	if logs := w.logs(); len(logs) == 0 || !strings.Contains(logs[0], "no-such-ref") {
		t.Errorf("logs = %q", logs)
	}
}

// 環境変数が渡ることだけを見る（切り捨ては gitlog の TestReport が見る）。
func TestGitWrapOldLogsAreDroppedByGeneration(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	for i := 0; i < 2; i++ {
		w.runEnv(map[string]string{"CCNAVI_GIT_KEEP_LOGS": "1"}, "rev-parse", "HEAD")
	}
	if n := len(w.logs()); n != 1 {
		t.Errorf("記録が %d 件", n)
	}
}

// 引数で -c を拒んでも、環境変数から同じ設定を差し込めるなら穴は開いたまま。
func TestGitWrapConfigInjectedThroughTheEnvironmentIsDropped(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	inject := map[string]string{"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "status.showUntrackedFiles", "GIT_CONFIG_VALUE_0": "no"}
	// 見本が有効なことを先に確かめる。有効でない見本では何も確かめられない。
	if out, _ := gitWith(w.dir, inject, "status", "--porcelain"); strings.Contains(out, "untracked.txt") {
		t.Fatal("見本の注入が効いていない")
	}
	r := w.runEnv(inject, "status", "--porcelain")
	w.succeed(r, "status")
	if !strings.Contains(r.Stdout, "untracked.txt") {
		t.Error("環境からの設定注入が通っている")
	}
}

// 走った機械の git の設定の締め出し（GIT_CONFIG_GLOBAL など）を、ラッパーが外さない。
func TestGitWrapKeepsTheHostGitConfigLockout(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	home := base.TempDir(t)
	shkit.Write(t, filepath.Join(home, ".gitconfig"), "[status]\n\tshowUntrackedFiles = no\n")
	r := w.runEnv(map[string]string{"HOME": home, "USERPROFILE": home}, "status", "--porcelain")
	w.succeed(r, "status")
	if !strings.Contains(r.Stdout, "untracked.txt") {
		t.Errorf("締め出しが外れて HOME の設定を読んだ: %q", r.Stdout)
	}
}

// ---- worktree remove の案内（Windows）

// worktree remove が Permission denied で止まったときだけ、立て直し方を案内する。Windows では、プロセスの cwd がそのディレクトリを
// 使用中にする。Bash ツールの cwd は呼び出しをまたいで残る親のシェルのものなので、ワークツリーの中へ cd したまま remove すると、
// 最後のディレクトリで Permission denied になり、空のディレクトリが残る。
func TestGitWrapPermissionDeniedOnRemoveTellsHowToRecover(t *testing.T) {
	t.Parallel()
	if !ospath.Windows {
		t.Skip("cwd がディレクトリを掴んで消せなくなるのは Windows だけ")
	}
	w := newWrap(t)
	path := w.addWorktree("held")
	holder := exec.Command("sh", "-c", "sleep 120")
	holder.Dir = path
	if err := holder.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = holder.Process.Kill(); _ = holder.Wait() })
	r := w.run("worktree", "remove", path)
	if r.Code != 1 || !strings.Contains(r.Stdout, "Permission denied") {
		t.Fatalf("code = %d\n%s", r.Code, r.Stdout)
	}
	var hints []string
	for _, line := range strings.Split(r.Stdout, "\n") {
		if strings.HasPrefix(line, "案内:") {
			hints = append(hints, line)
		}
	}
	if len(hints) != 1 {
		t.Fatalf("案内 %d 本: %s", len(hints), r.Stdout)
	}
	hint := hints[0]
	for _, word := range []string{"cwd", "サブシェル", "worktree list", "rmdir", "ユーザ", "ccnavi-git.sh worktree list", "rmdir " + path} {
		if !strings.Contains(hint, word) {
			t.Errorf("案内に %q が無い: %s", word, hint)
		}
	}
	if strings.Contains(strings.ReplaceAll(hint, "ccnavi-git.sh worktree", ""), "git worktree") {
		t.Errorf("生の git を勧めている: %s", hint)
	}
}

// ---- reset の案内

// reset は通さず、リモートに合わせたいときは ccnavi-sync.sh を案内する。使い方の文が、checkout -B の代わり・branch で通さない形・
// tag は一覧だけを言う（判定そのものは実行ファイル（ccnavi git check）が行い、gitwrap の見本が見る）。
func TestGitWrapUsageNamesCcnaviSyncAndWhatItRejects(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	r := w.run("--help")
	for _, want := range []string{"ccnavi-sync.sh <P>", "-D -m -M -C -f -u は不可"} {
		if !strings.Contains(r.Stdout, want) {
			t.Errorf("使い方に %q が無い", want)
		}
	}
	if strings.Contains(r.Stdout, "checkout -B <ブランチ>") {
		t.Error("checkout -B を案内している")
	}
	if !strings.Contains(r.Stdout+r.Stderr, "作成はユーザに依頼") {
		t.Error("tag の作成の案内が無い")
	}
}

// ---- 承認済みチケットの置き場

const (
	storeCopy   = ".ccnavi/approved/doing/i0001.md"
	storeReview = "wip/proposals/review/i0001-01-01.md"
)

// 承認済みチケットの置き場を過去の中身に戻す形を止める。checkout <ref> <パス> と restore --source <ref> は置き場を別のコミットの中身に戻し、
// restore --ours / --theirs は置き場の衝突を片側にそろえる。どれも承認が無かったことにも、消した承認が戻ったことにもなる。
func newStoreFix(t *testing.T) *wrapFix {
	w := newWrap(t)
	for _, rel := range []string{storeCopy, storeReview} {
		shkit.Write(t, w.join(filepath.FromSlash(rel)), "old\n")
	}
	git(t, w.dir, "add", storeCopy, storeReview)
	git(t, w.dir, "commit", "-q", "-m", "old")
	for _, rel := range []string{storeCopy, storeReview, "tracked.txt"} {
		shkit.Write(t, w.join(filepath.FromSlash(rel)), "new\n")
	}
	git(t, w.dir, "commit", "-q", "-am", "new")
	return w
}

func (w *wrapFix) assertStoreUntouched() {
	w.t.Helper()
	for _, rel := range []string{storeCopy, storeReview} {
		if got := shkit.Read(w.t, w.join(filepath.FromSlash(rel))); got != "new\n" {
			w.t.Errorf("%s が書き換わった: %q", rel, got)
		}
	}
}

func TestGitWrapStorePathsRelativeToASubdirectoryAreResolved(t *testing.T) {
	t.Parallel()
	w := newStoreFix(t)
	r := w.refused(w.runIn(w.join(".ccnavi"), nil, "checkout", "HEAD~1", "approved/doing/i0001.md"), "checkout")
	if !strings.Contains(r.Stderr, "置き場") {
		t.Errorf("stderr = %q", r.Stderr)
	}
	w.assertStoreUntouched()
}

// 置き場の env を入れても、既定の置き場を戻す形を止める（置き場は固定。A9）。env で保護を外せないことを見る。
// 承認済みとレビュー待ちは別々に打つ（一緒に打つと、片方の保護だけで止まってもう片方が env で動くのを見逃すため）。
func TestGitWrapTheStoreDoesNotMoveWithTheEnvironment(t *testing.T) {
	t.Parallel()
	w := newStoreFix(t)
	env := map[string]string{"CCNAVI_TICKETS_APPROVED": "tickets/approved", "CCNAVI_TICKETS_PROPOSAL": "tickets/proposals"}
	for _, rel := range []string{storeCopy, storeReview} {
		w.refused(w.runEnv(env, "restore", "--source", "HEAD~1", rel), rel)
	}
	w.assertStoreUntouched()
}

// ---- worktree の名前

// worktree add は行き先の名前とブランチ名を揃える形だけ通す。親のブランチ名は親の識別子で、ワークツリーの名前も同じ。
// -B（既存のブランチの移動）・--detach・-f はその結び付きを崩すか、親のブランチを別のコミットへ向け直す。
func TestGitWrapWorktreeAddRejectsAMismatchedName(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	git(t, w.dir, "branch", "other")
	w.reject("worktree", "add", ".claude/worktrees/a", "other") // 引数だけの形は Go の表が見る
	if shkit.Exists(w.join(".claude", "worktrees", "a")) {
		t.Error("断ったのに作った")
	}
}

func TestGitWrapWorktreeAddPassesMatchingForms(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	git(t, w.dir, "branch", "c")
	for name, args := range map[string][]string{
		"a": {"worktree", "add", ".claude/worktrees/a", "-b", "a", "HEAD"},
		"b": {"worktree", "add", ".claude/worktrees/b"},
		"c": {"worktree", "add", ".claude/worktrees/c", "c"},
	} {
		w.succeed(w.run(args...), strings.Join(args, " "))
		if got := w.branch(w.join(".claude", "worktrees", name)); got != name {
			t.Errorf("%s のブランチ = %q", name, got)
		}
	}
}

// 親のワークツリーでは別のブランチへ移らない。別のブランチに移ると、ccnavi-sync.sh とセッション開始時の ccnavi-fetch.sh が
// リモートでの承認をこのツリーへ取り込まなくなる。移れない形の表は Go の TestParentWorktreeDoesNotSwitchAway が見る。ここは通しの確認。
func TestGitWrapMovingAwayFromTheParentBranchIsRejected(t *testing.T) {
	t.Parallel()
	w := newWrap(t)
	home := w.branch(w.dir)
	parent := w.addWorktree("i0001")
	shkit.Write(t, filepath.Join(parent, "wip", "proposals", "todo", "i0001.md"), "---\nversion: 1\nticket: i0001\n---\n")
	w.addWorktree("free")
	for _, args := range [][]string{{"checkout", home}, {"checkout", "-b", "other"}} {
		r := w.refused(w.runIn(parent, nil, args...), strings.Join(args, " "))
		for _, want := range []string{"親のワークツリー", "ccnavi-sync.sh", "一度でも push したか ccnavi-sync.sh で取り込んだことがある"} {
			if !strings.Contains(r.Stderr, want) {
				t.Errorf("%v: stderr に %q が無い", args, want)
			}
		}
		if strings.Contains(r.Stderr, "ADR") {
			t.Errorf("拒否文に ADR が出ている: %q", r.Stderr)
		}
	}
	if got := w.branch(parent); got != "i0001" {
		t.Errorf("HEAD が動いた: %q", got)
	}
	w.assertNoLogs()
}

// ---- 親の push のゲート

// 親のワークツリーのブランチは、リモートから消えていないことを確かめてから送る（push のゲート）。「一度は送った形跡」は、
// refs/remotes/origin/<ブランチ>と、実行ファイルが答える着手済み（承認済みの親チケットの started_at）・依頼のマーカー・手元の退避で見る。
// 形跡があるのにリモートに無ければ拒否し（改名・消し間違い・破棄した親子のチケットを、確かめずに作り直さない）、形跡が無ければ
// 初回の push として通す。承認済みというだけでは形跡にしない（承認して最初に打つ push が通る）。確かめられないとき（ls-remote の失敗）は
// 拒否する。親のワークツリーでないツリーと、origin 以外への push には掛けない。
type gateFix struct {
	*wrapFix
	bare string
	tree string
}

func newGate(t *testing.T) *gateFix {
	w := newWrap(t)
	g := &gateFix{wrapFix: w, bare: w.makeBare()}
	git(t, w.dir, "remote", "add", "origin", g.bare)
	g.tree = w.addWorktree("i0001")
	// 提案だけの親。
	shkit.Write(t, filepath.Join(g.tree, "wip", "proposals", "todo", "i0001.md"), "---\nversion: 1\nticket: i0001\n---\n")
	git(t, g.tree, "add", "wip/proposals/todo/i0001.md")
	git(t, g.tree, "commit", "-q", "-m", "proposal")
	return g
}

// approve は承認済みの親チケットを置く。着手済み（started_at あり）のときだけ形跡になる。
func (g *gateFix) approve(started bool) {
	g.t.Helper()
	mark := ""
	if started {
		mark = "started_at: 2026-10-18T00:00:00Z\n"
	}
	shkit.Write(g.t, filepath.Join(g.tree, ".ccnavi", "approved", "doing", "i0001.md"), "---\nversion: 1\nticket: i0001\n"+mark+"---\n")
	git(g.t, g.tree, "add", ".ccnavi/approved/doing/i0001.md")
	git(g.t, g.tree, "commit", "-q", "-m", "copy")
}

func (g *gateFix) push() run.Result {
	g.t.Helper()
	return g.runIn(g.tree, nil, "push", "-u", "origin", "i0001")
}

func (g *gateFix) remoteSha() string {
	out, _ := gitTry(g.dir, "--git-dir", g.bare, "rev-parse", "--verify", "-q", "i0001")
	return strings.TrimSpace(out)
}

func (g *gateFix) deleteOnRemote() {
	g.t.Helper()
	git(g.t, g.dir, "--git-dir", g.bare, "update-ref", "-d", "refs/heads/i0001")
}

func (g *gateFix) commitFile(name string) {
	g.t.Helper()
	shkit.Write(g.t, filepath.Join(g.tree, name), "x\n")
	git(g.t, g.tree, "add", name)
	git(g.t, g.tree, "commit", "-q", "-m", name)
}

func TestGitWrapTheFirstPushOfAParentNeverSentPassesAndWritesNothing(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	r := g.push()
	g.succeed(r, "push")
	if got := gitT(t, g.tree, "rev-parse", "HEAD"); got != g.remoteSha() {
		t.Errorf("HEAD = %s, remote = %s", got, g.remoteSha())
	}
	// 取り込み状態は作らない。案内も出さない。
	if shkit.Exists(g.join("logs", "state", "sync")) || strings.Contains(r.Stdout, "取り込み状態") {
		t.Errorf("取り込み状態を作った: %s", r.Stdout)
	}
}

func TestGitWrapLaterPushesPassWhileTheBranchIsOnTheRemote(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	g.succeed(g.push(), "1 回目")
	g.commitFile("more.txt")
	g.succeed(g.push(), "2 回目")
	if got := gitT(t, g.tree, "rev-parse", "HEAD"); got != g.remoteSha() {
		t.Errorf("HEAD = %s, remote = %s", got, g.remoteSha())
	}
}

// 参照（origin/i0001）が無くても、着手済み（started_at あり）なら一度は送った親として扱う。
func TestGitWrapAStartedParentMissingFromTheRemoteIsRefused(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	g.approve(true)
	r := g.refused(g.push(), "push")
	if !strings.Contains(r.Stderr, "親のブランチ i0001 がリモートに無い") || !strings.Contains(r.Stderr, "ccnavi-sync.sh i0001") || g.remoteSha() != "" {
		t.Errorf("stderr = %q, remote = %q", r.Stderr, g.remoteSha())
	}
}

// origin の URL が : を含まないローカルのパスのとき、引数にそのパスを書いてもゲートを飛ばせない。
func TestGitWrapTheOriginURLWrittenDirectlyIsStillGated(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	g.succeed(g.push(), "push")
	g.deleteOnRemote()
	r := g.refused(g.runIn(g.tree, nil, "push", "-u", g.bare, "i0001"), "push")
	if !strings.Contains(r.Stderr, "リモートに無い") || g.remoteSha() != "" {
		t.Errorf("stderr = %q, remote = %q", r.Stderr, g.remoteSha())
	}
}

// ls-remote が失敗して確かめられないときは拒否し、止まってユーザに伝えるよう言う。
func TestGitWrapAnUnverifiableRemoteTellsTheAgentToStop(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	g.succeed(g.push(), "push")
	git(t, g.dir, "remote", "set-url", "origin", g.join("nowhere.git"))
	r := g.refused(g.push(), "push")
	for _, want := range []string{"リモートにあるかを確かめられない", "ユーザに伝えて止まってください"} {
		if !strings.Contains(r.Stderr, want) {
			t.Errorf("stderr に %q が無い: %q", want, r.Stderr)
		}
	}
	if strings.Contains(r.Stderr, "。。") {
		t.Errorf("句点が重なっている: %q", r.Stderr)
	}
}

func TestGitWrapABranchGoneFromTheRemoteIsRefusedUntilItIsRestored(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	g.succeed(g.push(), "push")
	g.deleteOnRemote()
	r := g.refused(g.push(), "push")
	if !strings.Contains(r.Stderr, "リモートに無い") || !strings.Contains(r.Stderr, "ユーザに元の名前で戻してもらい") || g.remoteSha() != "" {
		t.Errorf("stderr = %q, remote = %q", r.Stderr, g.remoteSha())
	}
	// ユーザがターミナルで、生の git で元の名前に戻す。そのあとは通る。
	git(t, g.tree, "push", "-q", "origin", "i0001")
	g.commitFile("again.txt")
	g.succeed(g.push(), "戻したあとの push")
	if got := gitT(t, g.tree, "rev-parse", "HEAD"); got != g.remoteSha() {
		t.Errorf("HEAD = %s, remote = %s", got, g.remoteSha())
	}
}

// 実行ファイルが無ければ、ラッパーは引数の形を確かめられないので、push の前に止める。
func TestGitWrapNoExecutableStopsTheWrapper(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	g.approve(true)
	r := g.refused(g.runIn(g.tree, map[string]string{"CCNAVI_ENTRY_PATH": g.join("no-such-ccnavi")}, "push", "-u", "origin", "i0001"), "push")
	if !strings.Contains(r.Stderr, "実行ファイルが見つかりません") {
		t.Errorf("stderr = %q", r.Stderr)
	}
}

// 送り先は git と同じ順で解き、origin 以外へ送るときはゲートを掛けない。
func TestGitWrapAPushRemoteOtherThanOriginIsNotGated(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	g.approve(false)
	git(t, g.dir, "remote", "add", "other", g.makeBare())
	git(t, g.tree, "config", "branch.i0001.pushRemote", "other")
	g.succeed(g.runIn(g.tree, nil, "push"), "push")
}

func TestGitWrapPushDefaultToOriginIsGated(t *testing.T) {
	t.Parallel()
	g := newGate(t)
	g.approve(true)
	git(t, g.tree, "config", "remote.pushDefault", "origin")
	git(t, g.tree, "config", "push.default", "current")
	r := g.refused(g.runIn(g.tree, nil, "push"), "push")
	if !strings.Contains(r.Stderr, "リモートに無い") {
		t.Errorf("stderr = %q", r.Stderr)
	}
}

// ---- リンクを経た作業場

// リンクを経た作業場でも組み込みの保護が有効（git のパスとワークスペースのパスを揃える。中 12）。
func newLinkedGate(t *testing.T) (*wrapFix, string) {
	t.Helper()
	if !base.CanSymlink() {
		t.Skip("リンクを作れない")
	}
	w := newWrap(t)
	link := w.dir + "-link"
	if err := os.Symlink(w.dir, link); err != nil {
		t.Skip("リンクを作れない")
	}
	t.Cleanup(func() { _ = os.Remove(link) })
	git(t, w.dir, "remote", "add", "origin", w.makeBare())
	tree := w.addWorktree("i0001")
	shkit.Write(t, filepath.Join(tree, ".ccnavi", "approved", "doing", "i0001.md"), "---\nversion: 1\nticket: i0001\nstarted_at: 2026-10-18T00:00:00Z\n---\n")
	git(t, tree, "add", ".ccnavi/approved/doing/i0001.md")
	git(t, tree, "commit", "-q", "-m", "copy")
	return w, filepath.Join(link, ".claude", "worktrees", "i0001")
}

func TestGitWrapTheParentWorktreeIsSeenThroughTheLink(t *testing.T) {
	t.Parallel()
	w, via := newLinkedGate(t)
	r := w.refused(w.runIn(via, nil, "checkout", "-b", "other"), "checkout")
	if !strings.Contains(r.Stderr, "親のワークツリー") {
		t.Errorf("stderr = %q", r.Stderr)
	}
}

// 承認済みの親チケットがあるのにリモートにブランチが無い親は、リンク越しでもゲートが止める。戻したあとは通る。取り込み状態は作らない。
func TestGitWrapThePushGateIsActiveThroughTheLink(t *testing.T) {
	t.Parallel()
	w, via := newLinkedGate(t)
	r := w.refused(w.runIn(via, nil, "push", "-u", "origin", "i0001"), "push")
	if !strings.Contains(r.Stderr, "リモートに無い") {
		t.Errorf("stderr = %q", r.Stderr)
	}
	git(t, w.join(".claude", "worktrees", "i0001"), "push", "-q", "origin", "i0001")
	w.succeed(w.runIn(via, nil, "push", "-u", "origin", "i0001"), "戻したあとの push")
	if shkit.Exists(w.join("logs", "state", "sync")) {
		t.Error("取り込み状態を作った")
	}
}

// ---- 統合先の判定

// 固定のリスト（main など）に無い名前の統合先へも直接は送らない。統合先の名前は ccnavi-fetch.sh と同じ決め方
// （origin/HEAD → origin/main・master）で、push したツリーが属するリポジトリについて決める。
func TestGitWrapThePushToTheBranchOriginHeadPointsToIsRefused(t *testing.T) {
	t.Parallel()
	const branch = "develop-v1.0.0"
	w := newWrap(t)
	bare := w.makeBare()
	git(t, w.dir, "remote", "add", "origin", bare)
	git(t, w.dir, "checkout", "--quiet", "-b", branch)
	pointOriginHead(t, w.dir, branch)
	r := w.refused(w.run("push", "-u", "origin", branch), "push")
	if !strings.Contains(r.Stderr, branch+" は統合先です") {
		t.Errorf("stderr = %q", r.Stderr)
	}
	if _, code := gitTry(w.dir, "--git-dir", bare, "rev-parse", "--verify", "-q", branch); code == 0 {
		t.Error("統合先へ届いた")
	}
}

// ---- worktree add の基点

// 統合先から切る worktree add は、fetch が新しく、手元の統合先が遅れていないときだけ通す。hook は git もネットワークも起こさないので、
// この 2 つ目の確認は sh の中にある。fetch は打たない。拒否のあと案内どおりに fetch すれば（FETCH_HEAD が新しくなって）通る。
type freshFix struct {
	*wrapFix
	integ, bare string
}

func newFresh(t *testing.T) *freshFix {
	w := newWrap(t)
	f := &freshFix{wrapFix: w, integ: w.branch(w.dir), bare: w.makeBare()}
	git(t, w.dir, "remote", "add", "origin", f.bare)
	git(t, w.dir, "push", "-q", "-u", "origin", f.integ)
	git(t, w.dir, "fetch", "-q", "origin")
	return f
}

func (f *freshFix) tree(name string) string { return f.join(".claude", "worktrees", name) }

// pushFromElsewhere は別の clone から origin の統合先を 1 つ進める。手元はまだ知らない。
func (f *freshFix) pushFromElsewhere() {
	f.t.Helper()
	other := base.TempDir(f.t)
	git(f.t, other, "clone", "-q", f.bare, "c")
	clone := filepath.Join(other, "c")
	shkit.Identity(f.t, clone)
	shkit.Write(f.t, filepath.Join(clone, "new.txt"), "n\n")
	git(f.t, clone, "add", "new.txt")
	git(f.t, clone, "commit", "-q", "-m", "ahead")
	git(f.t, clone, "push", "-q", "origin", "HEAD:"+f.integ)
}

func (f *freshFix) created(name string, args ...string) run.Result {
	f.t.Helper()
	r := f.run(append([]string{"worktree", "add", ".claude/worktrees/" + name}, args...)...)
	f.succeed(r, "worktree add "+name)
	if got := f.branch(f.tree(name)); got != name {
		f.t.Errorf("%s のブランチ = %q", name, got)
	}
	return r
}

func TestGitWrapFreshBaseUpToDatePassesWithOrWithoutTheBase(t *testing.T) {
	t.Parallel()
	f := newFresh(t)
	f.created("a", "-b", "a")
	f.created("b", "-b", "b", f.integ)
	f.created("c", "-b", "c", "origin/"+f.integ)
}

func TestGitWrapFreshBaseBehindIsRejectedAndPassesAfterFastForward(t *testing.T) {
	t.Parallel()
	f := newFresh(t)
	f.pushFromElsewhere()
	git(t, f.dir, "fetch", "-q", "origin")
	for _, args := range [][]string{{"-b", "a"}, {"-b", "a", f.integ}} {
		r := f.reject(append([]string{"worktree", "add", ".claude/worktrees/a"}, args...)...)
		for _, want := range []string{
			"基点 " + f.integ + " が origin/" + f.integ + " より 1 件遅れて",
			// 案内は、基点を origin/<統合先> にして切る完全なコマンド（手元の統合先は進めない）
			"--no-track 'origin/" + f.integ + "'",
			"ccnavi-worktree.sh",
		} {
			if !strings.Contains(r.Stderr, want) {
				t.Errorf("%v: stderr に %q が無い: %q", args, want, r.Stderr)
			}
		}
		if strings.Contains(r.Stderr, "merge --ff-only origin") {
			t.Errorf("手元の統合先を進める案内が出ている: %q", r.Stderr)
		}
	}
	git(t, f.dir, "merge", "-q", "--ff-only", "origin/"+f.integ)
	f.created("a", "-b", "a")
}

// 拒否が案内するコマンドは、新しいシェルからそのまま打って通る。
func TestGitWrapFreshBaseTheSuggestedCommandsRunFromAFreshShell(t *testing.T) {
	t.Parallel()
	f := newFresh(t)
	f.pushFromElsewhere()
	git(t, f.dir, "fetch", "-q", "origin")
	r := f.reject("worktree", "add", ".claude/worktrees/a", "-b", "a")
	var fetch, cut string
	for _, m := range regexp.MustCompile("`([^`]+)`").FindAllStringSubmatch(r.Stderr, -1) {
		if strings.Contains(m[1], " fetch origin ") && fetch == "" {
			fetch = m[1]
		}
		if strings.Contains(m[1], " worktree add ") && cut == "" {
			cut = m[1]
		}
	}
	if fetch == "" || cut == "" {
		t.Fatalf("案内のコマンドが見つからない: %q", r.Stderr)
	}
	for _, command := range []string{fetch, cut} {
		cmd := exec.Command("sh", "-c", command)
		cmd.Dir = f.dir
		cmd.Env = run.Environment(run.Opts{Env: map[string]string{"CCNAVI_ENTRY_PATH": sharedWrapper(t)}})
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("%s: %v\n%s", command, err, out)
		}
	}
	if got := f.branch(f.tree("a")); got != "a" {
		t.Errorf("ブランチ = %q", got)
	}
}

// fetch が失敗した（オフライン）ときは、拒否を警告に下げる。
func TestGitWrapAFailedFetchDowngradesTheRejectionToAWarning(t *testing.T) {
	t.Parallel()
	f := newFresh(t)
	git(t, f.dir, "remote", "set-url", "origin", filepath.Join(filepath.Dir(f.bare), "gone.git"))
	fetchHead := f.join(".git", "FETCH_HEAD")
	old := time.Now().Add(-20 * time.Minute)
	if err := os.Chtimes(fetchHead, old, old); err != nil {
		t.Fatal(err)
	}
	f.reject("worktree", "add", ".claude/worktrees/a", "-b", "a")
	if fetched := f.run("fetch", "origin", f.integ); fetched.Code != 1 {
		t.Fatalf("fetch code = %d\n%s%s", fetched.Code, fetched.Stdout, fetched.Stderr)
	}
	r := f.created("a", "-b", "a")
	wantContains(t, r.Stdout, "警告")
	wantContains(t, r.Stdout, "オフライン")
}

func TestGitWrapALaterGoodFetchClearsTheFailureMark(t *testing.T) {
	t.Parallel()
	f := newFresh(t)
	mark := f.join(".git", "ccnavi-fetch-failed")
	git(t, f.dir, "remote", "set-url", "origin", f.bare+".gone")
	f.run("fetch", "origin", f.integ)
	if !shkit.Exists(mark) {
		t.Fatal("失敗の印が付かない")
	}
	git(t, f.dir, "remote", "set-url", "origin", f.bare)
	f.succeed(f.run("fetch", "origin", f.integ), "fetch")
	if shkit.Exists(mark) {
		t.Error("失敗の印が残った")
	}
}
