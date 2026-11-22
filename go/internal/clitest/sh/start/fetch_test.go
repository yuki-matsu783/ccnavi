package start

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// ccnavi-fetch.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。
//
// 見るのは 2 つ。
//  1. そのツリーがチェックアウトしているブランチを upstream まで ff で進める（承認済みチケットとマーカーが届く経路）
//  2. ワークツリーの起点になる統合先ブランチを、チェックアウトされていなくても ff で進める（古い起点から枝が伸びないようにする）
//
// ワークスペースは一時ディレクトリに git と bare のリモートで作る。リモートを進めるのは別に clone した押し手で、ワークスペース側からは
// 「他の機械が push した」ように見える。読み返すのは終了コード・標準出力と、git に残った ref だけ。

type fetchEnv struct {
	t       testing.TB
	dir     string
	ws      string
	remote  string
	scripts string
}

func newFetch(t testing.TB) *fetchEnv {
	t.Helper()
	shkit.Need(t, "sh", "git")
	f := &fetchEnv{t: t, dir: base.TempDir(t)}
	f.ws = filepath.Join(f.dir, "ws")
	f.scripts = shkit.CopyScripts(t, f.ws, "ccnavi-fetch.sh")
	// 承認済みチケットの置き場。第 1 周はこれがあるツリーだけを見る。
	shkit.Mkdir(t, filepath.Join(f.ws, ".ccnavi", "approved", "doing"))
	f.remote = f.repository(f.ws)
	return f
}

// repository は git のリポジトリと、その bare のリモートを作る。リモートのパスを返す。
func (f *fetchEnv) repository(path string) string {
	f.t.Helper()
	git(f.t, f.dir, "init", "-q", "-b", "main", path)
	shkit.Identity(f.t, path)
	shkit.Write(f.t, filepath.Join(path, ".gitignore"), ".claude/worktrees/\n")
	shkit.Write(f.t, filepath.Join(path, "README.md"), "seed\n")
	git(f.t, path, "add", "--", ".gitignore", "README.md")
	git(f.t, path, "commit", "-q", "-m", "seed")
	remote := filepath.Join(f.dir, filepath.Base(path)+".git")
	git(f.t, f.dir, "init", "-q", "--bare", "-b", "main", remote)
	git(f.t, path, "remote", "add", "origin", remote)
	git(f.t, path, "push", "-q", "-u", "origin", "main")
	// clone のときに置かれる origin/HEAD（普通の fetch では更新されない）。
	git(f.t, path, "remote", "set-head", "origin", "main")
	return remote
}

// advance は他の機械の代わり。bare のリモートの <branch> を 1 件進め、その sha を返す。
func (f *fetchEnv) advance(remote, branch string) string {
	f.t.Helper()
	pusher := filepath.Join(f.dir, "push-"+filepath.Base(remote))
	if !shkit.IsDir(pusher) {
		git(f.t, f.dir, "clone", "-q", remote, pusher)
		shkit.Identity(f.t, pusher)
	}
	git(f.t, pusher, "checkout", "-q", branch)
	git(f.t, pusher, "pull", "-q", "--ff-only", "origin", branch)
	entries, err := os.ReadDir(pusher)
	if err != nil {
		f.t.Fatal(err)
	}
	n := strconv.Itoa(len(entries))
	shkit.Write(f.t, filepath.Join(pusher, "r"+n+".txt"), "remote\n")
	git(f.t, pusher, "add", "-A")
	git(f.t, pusher, "commit", "-q", "-m", "remote "+n)
	git(f.t, pusher, "push", "-q", "origin", branch)
	return sha(f.t, pusher, "HEAD")
}

// advanceMain は main を 1 件進める。
func (f *fetchEnv) advanceMain() string { return f.advance(f.remote, "main") }

func (f *fetchEnv) sourceExe() string {
	path := filepath.Join(f.dir, "source-bin", "ccnavi")
	if !shkit.Exists(path) {
		shkit.ExeWrapper(f.t, filepath.Dir(path))
	}
	return path
}

func (f *fetchEnv) fetch(extra map[string]string) run.Result { return f.fetchIn(f.ws, extra) }

func (f *fetchEnv) fetchIn(cwd string, extra map[string]string) run.Result {
	f.t.Helper()
	// 親のワークツリーかは実行ファイルに聞く（sh はチケットを読まない）。ソースの実行ファイルを渡す。
	return runScript(f.t, filepath.Join(f.scripts, "ccnavi-fetch.sh"), cwd, merge(map[string]string{"CCNAVI_ENTRY_PATH": f.sourceExe()}, extra))
}

// hostDefault はホストの既定ブランチ（bare のリモートの HEAD）を別のブランチへ変える。
func (f *fetchEnv) hostDefault(branch string) {
	f.t.Helper()
	git(f.t, f.dir, "--git-dir", f.remote, "symbolic-ref", "HEAD", "refs/heads/"+branch)
}

func (f *fetchEnv) originHead(repo string) string {
	f.t.Helper()
	return git(f.t, repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
}

// spy は spyOpts の指定の git のスタブを置く。ls-remote と fetch の打たれた行は記録に残る。
type spyOpts struct {
	fail      bool   // ls-remote だけ失敗させる
	answer    string // あれば、ls-remote の出力をその 1 行（`ref: refs/heads/<名前>\tHEAD`）に差し替える
	hang      bool   // ls-remote が返事をしない
	failFetch bool   // fetch だけ失敗させる
}

// spyOnLsRemote は `git ls-remote` が打たれた回数を残す git のスタブを置く。PATH に足す環境と、ls-remote の記録のパスを返す。
// fetch の回数は返す記録の隣の `.fetch` に残る。
func (f *fetchEnv) spyOnLsRemote(o spyOpts) (map[string]string, string) {
	f.t.Helper()
	bindir := filepath.Join(f.dir, "spy-bin")
	log := filepath.Join(f.dir, "ls-remote.log")
	script := "#!/bin/sh\nfor a in \"$@\"; do\n  if [ \"$a\" = ls-remote ]; then\n"
	script += "    echo \"$*\" >>'" + shkit.Slash(log) + "'\n"
	if o.hang {
		script += "    exec sleep 30\n"
	}
	if o.fail {
		script += "    echo 'fatal: unable to access' >&2; exit 128\n"
	}
	if o.answer != "" {
		script += "    printf 'ref: refs/heads/%s\\tHEAD\\n' '" + o.answer + "'; exit 0\n"
	}
	script += "  fi\n  if [ \"$a\" = fetch ]; then\n"
	script += "    echo \"$*\" >>'" + shkit.Slash(log) + ".fetch'\n"
	if o.failFetch {
		script += "    echo 'fatal: could not read from remote repository' >&2; exit 128\n"
	}
	script += "  fi\ndone\nexec '" + lookGit(f.t) + "' \"$@\"\n"
	shkit.WriteExec(f.t, filepath.Join(bindir, "git"), script)
	return map[string]string{"PATH": bindir + string(os.PathListSeparator) + os.Getenv("PATH")}, log
}

// fetchCalls は spyOnLsRemote のスタブが受けた fetch の行。
func fetchCalls(t testing.TB, log string) []string {
	t.Helper()
	data, err := os.ReadFile(log + ".fetch")
	if err != nil {
		return nil
	}
	return strings.Split(strings.TrimRight(string(data), "\n"), "\n")
}

// notice は報せの本文の行。先頭の見出しは除く。
func notice(r run.Result) []string {
	var lines []string
	for _, line := range strings.Split(r.Stdout, "\n") {
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	if len(lines) > 0 && strings.HasPrefix(lines[0], "[ccnavi]") {
		return lines[1:]
	}
	return lines
}

// leaveMain はルートを `main` 以外に移す。issue #56 のワークスペースと同じ形。
func (f *fetchEnv) leaveMain(tree, branch string) {
	f.t.Helper()
	if tree == "" {
		tree = f.ws
	}
	git(f.t, tree, "checkout", "-q", "-b", branch)
}

// ---- 起点になる統合先ブランチ

// ルートが main に居なくても、ワークツリーの起点になる main は新しくなる。
func TestFetchDefaultBranchAdvancesWhileRootIsElsewhere(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	f.leaveMain("", "claude/x")
	head := f.advanceMain()
	done := code(t, f.fetch(nil), 0)
	eq(t, sha(t, f.ws, "refs/heads/main"), head, "main")
	lines := notice(done)
	eq(t, len(lines), 1, "報せの行の数")
	has(t, strings.Join(lines, "\n"), "統合先ブランチ")
	has(t, done.Stdout, "main")
}

// プロジェクトも同じ。起点はそのプロジェクトのデフォルトブランチ。
func TestFetchProjectDefaultBranchAdvances(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	project := filepath.Join(f.ws, "projects", "p")
	remote := f.repository(project)
	shkit.Mkdir(t, filepath.Join(project, ".ccnavi", "approved", "doing"))
	f.leaveMain(project, "claude/p")
	head := f.advance(remote, "main")
	done := f.fetch(nil)
	eq(t, sha(t, project, "refs/heads/main"), head, "main")
	has(t, done.Stdout, "p: ")
}

// ---- 承認済みチケットとマーカー（第 1 周）

func TestFetchCheckedOutBranchAdvancesForApprovedTickets(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	f.leaveMain("", "claude/x")
	git(t, f.ws, "push", "-q", "-u", "origin", "claude/x")
	head := f.advance(f.remote, "claude/x")
	done := f.fetch(nil)
	eq(t, sha(t, f.ws, "HEAD"), head, "HEAD")
	has(t, strings.Join(notice(done), "\n"), "親ブランチ")
}

// ルートが main に居るなら第 1 周で済む。第 2 周は同じことを言わない。
func TestFetchDefaultBranchIsNotReportedTwice(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	head := f.advanceMain()
	done := f.fetch(nil)
	eq(t, sha(t, f.ws, "refs/heads/main"), head, "main")
	lines := notice(done)
	eq(t, len(lines), 1, "報せの行の数")
	has(t, strings.Join(lines, "\n"), "親ブランチ")
	lacks(t, lines[0], "統合先ブランチ")
}

// ---- 届かないとき

// git が届かなくても止めない。同じ origin には 1 度しか取りに行かず、1 行だけ言う。
func TestFetchUnreachableOriginIsTriedOnce(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	f.leaveMain("", "claude/x")
	git(t, f.ws, "branch", "-q", "--set-upstream-to", "origin/main")
	missing := shkit.Slash(filepath.Join(f.dir, "nowhere.git"))
	git(t, f.ws, "remote", "set-url", "origin", "file://user:secret@"+strings.TrimLeft(missing, "/"))
	env, log := f.spyOnLsRemote(spyOpts{})
	done := code(t, f.fetch(env), 0)
	eq(t, strings.Count(done.Stdout, "取ってこられなかった"), 1, "取ってこられなかったの数")
	lacks(t, done.Stdout, "secret",
		// 届かないだけで、認証の話ではない。
		"認証")
	// 第 1 周で届かなかった origin には、向け直すための ls-remote を打たない。
	truth(t, !shkit.Exists(log), "第 1 周で失敗した origin に ls-remote を打った")
}

// 401 を返すリモート。尋ねずに失敗し、認証に失敗したことと、ユーザがすることを言う。
func TestFetchAnAuthenticationFailureSaysSo(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("WWW-Authenticate", `Basic realm="ccnavi"`)
		w.WriteHeader(http.StatusUnauthorized)
	}))
	t.Cleanup(server.Close)
	git(t, f.ws, "remote", "set-url", "origin", server.URL+"/o/r.git")
	f.leaveMain("", "claude/x")
	done := code(t, f.fetch(map[string]string{"LANG": "ja_JP.UTF-8", "LC_ALL": "ja_JP.UTF-8"}), 0)
	eq(t, strings.Count(done.Stdout, "取ってこられなかった"), 1, "取ってこられなかったの数")
	has(t, done.Stdout, "認証に失敗した", "git fetch origin",
		// 手元のサーバは GitLab として扱われる（未知のホスト）。glab を案内し、gh は言わない。
		"glab auth login --hostname "+server.Listener.Addr().String())
	lacks(t, done.Stdout, "gh auth login")
}

// 応答しないリモートは CCNAVI_FETCH_TIMEOUT 秒で切る。hook の上限まで待たない。
func TestFetchAHangingRemoteIsCutOff(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	helpers := filepath.Join(f.dir, "bin")
	shkit.WriteExec(t, filepath.Join(helpers, "git-remote-slow"), "#!/bin/sh\nsleep 60\n")
	git(t, f.ws, "remote", "set-url", "origin", "slow::x")
	path := helpers + string(os.PathListSeparator) + os.Getenv("PATH")
	started := time.Now()
	done := code(t, f.fetch(map[string]string{"PATH": path, "CCNAVI_FETCH_TIMEOUT": "2"}), 0)
	// 切っていなければ、helper の 60 秒を待つ。
	truth(t, time.Since(started) < 45*time.Second, "タイムアウト監視が切っていない")
	has(t, done.Stdout, "取ってこられなかった")
	eq(t, strings.TrimSpace(done.Stderr), "", "標準エラー")
}

// ---- 取り込み済みの親子のチケット
// SessionStart は親のワークツリーを早送りする。

// family は親のワークツリー .claude/worktrees/<name>（親チケットを送ってある）。
func (f *fetchEnv) family(name string) string {
	f.t.Helper()
	f.leaveMain("", "claude/x")
	tree := filepath.Join(f.ws, ".claude", "worktrees", name)
	git(f.t, f.ws, "worktree", "add", "-q", tree, "-b", name, "main")
	copyRel := ".ccnavi/approved/doing/" + name + ".md"
	shkit.Write(f.t, filepath.Join(tree, copyRel), "---\nversion: 1\nticket: "+name+"\n---\n")
	git(f.t, tree, "add", "--", copyRel)
	git(f.t, tree, "commit", "-q", "-m", "copy")
	git(f.t, tree, "push", "-q", "-u", "origin", name)
	return tree
}

func TestFetchAFamilyUnderALockIsSkipped(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	tree := f.family("i0001")
	lock := filepath.Join(f.ws, "logs", "state", "locks", "self", "i0001")
	shkit.Mkdir(t, lock)
	now := time.Now().Unix()
	pid := base.LiveShPid(t)
	host, err := os.Hostname()
	if err != nil {
		t.Fatal(err)
	}
	stamp := strconv.FormatInt(now, 10)
	shkit.Write(t, filepath.Join(lock, "owner"), host+" "+strconv.Itoa(pid)+" "+stamp+" "+strconv.Itoa(pid)+"-"+stamp+"\n")
	before := sha(t, tree, "HEAD")
	f.advance(f.remote, "i0001")
	done := f.fetch(nil)
	eq(t, sha(t, tree, "HEAD"), before, "HEAD")
	has(t, done.Stdout, "他の操作の最中")
	truth(t, shkit.IsDir(lock), "ロックを外した")
}

// P がリモートに無くても、その origin を失敗したものに数えない（統合先を飛ばさない）。
func TestFetchAMissingFamilyBranchDoesNotStopTheDefaultBranch(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	f.family("i0001")
	pusher := filepath.Join(f.dir, "push-"+filepath.Base(f.remote))
	head := f.advanceMain()
	git(t, pusher, "push", "-q", "origin", "--delete", "i0001")
	done := code(t, f.fetch(nil), 0)
	has(t, done.Stdout, "リモートにありません", "ccnavi-sync.sh i0001")
	eq(t, sha(t, f.ws, "refs/heads/main"), head, "main")
}

// 枠を過ぎたら fetch ごと飛ばして名指しする。起点も進めない。
func TestFetchTheBudgetIsCheckedBeforeFetching(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	tree := f.family("i0001")
	before := sha(t, tree, "HEAD")
	mainBefore := sha(t, f.ws, "refs/heads/main")
	f.advance(f.remote, "i0001")
	f.advanceMain()
	done := f.fetch(map[string]string{"CCNAVI_FETCH_BUDGET": "0"})
	eq(t, sha(t, tree, "HEAD"), before, "親の HEAD")
	eq(t, sha(t, f.ws, "refs/heads/main"), mainBefore, "main")
	has(t, done.Stdout, "時間の枠", "取得しませんでした")
}

// origin の fetch の refspec が main だけでも origin/P を進める（fetch に行き先を書く）。
func TestFetchAFamilyInASingleBranchCloneIsForwarded(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	tree := f.family("i0001")
	git(t, f.ws, "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
	head := f.advance(f.remote, "i0001")
	f.fetch(nil)
	eq(t, sha(t, tree, "HEAD"), head, "親の HEAD")
}

// ---- 手元の origin/HEAD をホストの既定ブランチに合わせる

// ホストの既定が main から trunk に変わった。手元の origin/HEAD は clone のときのまま。trunk を取り、origin/HEAD を向け直し、1 行出す。
// 第 2 周は trunk で進む。
func TestFetchAChangedHostDefaultMovesOriginHeadAndTheBaseFollows(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	git(t, f.ws, "push", "-q", "origin", "main:trunk")
	f.leaveMain("", "claude/x")
	f.hostDefault("trunk")
	head := f.advance(f.remote, "trunk")
	mainBefore := sha(t, f.ws, "refs/heads/main")
	env, log := f.spyOnLsRemote(spyOpts{})
	done := code(t, f.fetch(env), 0)
	eq(t, f.originHead(f.ws), "refs/remotes/origin/trunk", "origin/HEAD")
	eq(t, strings.Count(done.Stdout, "ws: 統合先を main から trunk に合わせた（ホストの既定ブランチ）"), 1, "合わせたの数")
	eq(t, sha(t, f.ws, "refs/remotes/origin/trunk"), head, "origin/trunk")
	// 第 2 周は新しい名前で進む（手元の trunk は無かったので origin/trunk から作る）。
	eq(t, sha(t, f.ws, "refs/heads/trunk"), head, "trunk")
	eq(t, sha(t, f.ws, "refs/heads/main"), mainBefore, "main")
	eq(t, len(strings.Split(strings.TrimRight(shkit.Read(t, log), "\n"), "\n")), 1, "ls-remote の回数")
}

// 手元に origin/trunk が無い（set-head は失敗する状態）。1 回のセッションで追いつく。
func TestFetchAHostDefaultBranchNotYetKnownLocallyIsCaughtUpInOneSession(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	git(t, f.dir, "--git-dir", f.remote, "branch", "trunk", "main")
	f.hostDefault("trunk")
	f.leaveMain("", "claude/x")
	eq(t, sha(t, f.ws, "refs/remotes/origin/trunk"), "", "前提の origin/trunk")
	done := code(t, f.fetch(nil), 0)
	eq(t, f.originHead(f.ws), "refs/remotes/origin/trunk", "origin/HEAD")
	truth(t, sha(t, f.ws, "refs/remotes/origin/trunk") != "", "origin/trunk を取っていない")
	truth(t, sha(t, f.ws, "refs/heads/trunk") != "", "trunk を作っていない")
	has(t, done.Stdout, "統合先を main から trunk に合わせた")
}

// ワークスペースの既定は main のまま、プロジェクトのホストだけ trunk に変わる。
func TestFetchAProjectOriginHeadFollowsItsOwnHost(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	project := filepath.Join(f.ws, "projects", "p")
	remote := f.repository(project)
	git(t, f.dir, "--git-dir", remote, "branch", "trunk", "main")
	git(t, f.dir, "--git-dir", remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
	f.leaveMain("", "claude/x")
	f.leaveMain(project, "claude/p")
	done := f.fetch(nil)
	eq(t, f.originHead(f.ws), "refs/remotes/origin/main", "ワークスペースの origin/HEAD")
	eq(t, f.originHead(project), "refs/remotes/origin/trunk", "プロジェクトの origin/HEAD")
	has(t, done.Stdout, "p: 統合先を main から trunk に合わせた")
	lacks(t, done.Stdout, "ws: 統合先を")
}

// ls-remote が失敗する（オフライン・時間切れ）。何も変えず何も出さない。
func TestFetchAnUnreachableHostLeavesOriginHeadAndSaysNothing(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	f.leaveMain("", "claude/x")
	f.hostDefault("trunk")
	before := f.originHead(f.ws)
	env, log := f.spyOnLsRemote(spyOpts{fail: true})
	done := code(t, f.fetch(env), 0)
	eq(t, strings.TrimSpace(done.Stdout), "", "標準出力")
	eq(t, f.originHead(f.ws), before, "origin/HEAD")
	truth(t, shkit.Exists(log), "ls-remote を打っていない")
}

// ホストは trunk と答えたが、trunk を取れない（fetch が失敗する）。向け直さない。ls-remote の答えはスタブが返し、fetch はスタブが失敗させる。
// 向け直す手前の経路を通すため。
func TestFetchAFailedFetchOfTheNewDefaultDoesNotMoveOriginHead(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	f.leaveMain("", "claude/x")
	before := f.originHead(f.ws)
	env, log := f.spyOnLsRemote(spyOpts{answer: "trunk", failFetch: true})
	done := code(t, f.fetch(env), 0)
	eq(t, f.originHead(f.ws), before, "origin/HEAD")
	lacks(t, done.Stdout, "合わせた")
	asked := false
	for _, call := range fetchCalls(t, log) {
		asked = asked || strings.Contains(call, "refs/heads/trunk")
	}
	truth(t, asked, "trunk を取りに行っていない（向け直す手前の経路を通っていない）")
}

// 向け直しの ls-remote が返事をしない origin は、第 2 周で同じ origin をもう一度待たない。
func TestFetchAHungOriginIsNotWaitedForTwice(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	f.leaveMain("", "claude/x")
	head := f.advanceMain()
	env, log := f.spyOnLsRemote(spyOpts{hang: true})
	done := code(t, f.fetch(merge(env, map[string]string{"CCNAVI_FETCH_TIMEOUT": "2", "CCNAVI_FETCH_BUDGET": "30"})), 0)
	// 向け直しの待ちは 1 回だけ。第 2 周の fetch は打たず、2 回目の待ちが入らない。
	eq(t, len(fetchCalls(t, log)), 0, "fetch の回数")
	truth(t, sha(t, f.ws, "refs/heads/main") != head, "main が進んだ")
	eq(t, strings.Count(done.Stdout, "取ってこられなかった"), 1, "取ってこられなかったの数")
}

// ls-remote が失敗しても、その origin を「失敗した origin」に数えない。第 2 周は取りに行く。
func TestFetchARetargetFailureIsNotRecordedAsAFailedOrigin(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	f.leaveMain("", "claude/x")
	head := f.advanceMain()
	env, _ := f.spyOnLsRemote(spyOpts{fail: true})
	done := code(t, f.fetch(env), 0)
	eq(t, sha(t, f.ws, "refs/heads/main"), head, "main")
	has(t, done.Stdout, "統合先ブランチ main を")
}

// 親子ごとの取り込み状態は持たない。HEAD のブランチ名を変換した識別子がフォルダ名と同じ親のワークツリーは、すべて早送りする。
func TestFetchAWorktreeIsFastForwardedWithoutAnyState(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	tree := f.family("i0001")
	before := sha(t, tree, "HEAD")
	head := f.advance(f.remote, "i0001")
	// 重ならない書きかけ（staged と未追跡）は、進めるのを止めない（重なりは git に任せる）。
	shkit.Write(t, filepath.Join(tree, "note.txt"), "書きかけ\n")
	git(t, tree, "add", "--", "note.txt")
	shkit.Write(t, filepath.Join(tree, "memo.txt"), "書きかけ\n")
	done := code(t, f.fetch(nil), 0)
	truth(t, sha(t, tree, "HEAD") != before, "HEAD が動いていない")
	eq(t, sha(t, tree, "HEAD"), head, "HEAD")
	has(t, done.Stdout, "i0001: 親ブランチ")
	// 書きかけは残る
	truth(t, shkit.Exists(filepath.Join(tree, "note.txt")) && shkit.Exists(filepath.Join(tree, "memo.txt")), "書きかけが消えた")
}

// ---- 何も出さないとき

func TestFetchQuietWhenNothingMoves(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	done := code(t, f.fetch(nil), 0)
	eq(t, strings.TrimSpace(done.Stdout), "", "標準出力")
}

// 実行ファイルが無い（入れ替え中など）ときは取ってくるだけ。早送りも、origin/HEAD の向け直し（ホストに聞く ls-remote ごと）もせず、
// 何も言わない（毎回「合わせた」と出さない）。
func TestFetchWithoutTheExecutableItOnlyFetches(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	head := f.advanceMain()
	before := sha(t, f.ws, "HEAD")
	git(t, f.ws, "push", "-q", "origin", "main:trunk")
	f.hostDefault("trunk")
	env, log := f.spyOnLsRemote(spyOpts{})
	done := code(t, f.fetch(merge(env, map[string]string{"CCNAVI_ENTRY_PATH": filepath.Join(f.dir, "nowhere")})), 0)
	eq(t, strings.TrimSpace(done.Stdout), "", "標準出力")
	eq(t, sha(t, f.ws, "refs/remotes/origin/main"), head, "origin/main")
	eq(t, sha(t, f.ws, "HEAD"), before, "HEAD")
	eq(t, f.originHead(f.ws), "refs/remotes/origin/main", "origin/HEAD")
	truth(t, !shkit.Exists(log), "ls-remote を打った")
	// ブランチを名指しせず、リポジトリごとに `fetch --quiet origin` を 1 回だけ打つ。
	calls := fetchCalls(t, log)
	eq(t, len(calls), 1, "fetch の回数")
	truth(t, strings.HasSuffix(calls[0], " fetch --quiet origin"), calls[0])
}

// projects/ のリポジトリも取りに行く。届かない origin は 1 行だけ言い、2 件目は黙る。
func TestFetchWithoutTheExecutableItReportsAnUnreachableOrigin(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	broken := filepath.Join(f.dir, "nowhere.git")
	for _, name := range []string{"p", "p2"} {
		project := filepath.Join(f.ws, "projects", name)
		f.repository(project)
		git(t, project, "remote", "set-url", "origin", broken)
	}
	env, log := f.spyOnLsRemote(spyOpts{})
	done := code(t, f.fetch(merge(env, map[string]string{"CCNAVI_ENTRY_PATH": filepath.Join(f.dir, "nowhere")})), 0)
	eq(t, strings.Count(done.Stdout, "取ってこられなかった"), 1, "取ってこられなかったの数")
	has(t, done.Stdout, "p: リモートを取ってこられなかった")
	lacks(t, done.Stdout, "ws:")
	calls := fetchCalls(t, log)
	eq(t, len(calls), 2, "fetch の回数")
	for _, c := range calls {
		truth(t, strings.HasSuffix(c, " fetch --quiet origin"), c)
	}
}

func TestFetchQuietWithoutARemote(t *testing.T) {
	t.Parallel()
	f := newFetch(t)
	git(t, f.ws, "remote", "remove", "origin")
	f.leaveMain("", "claude/x")
	done := code(t, f.fetch(nil), 0)
	eq(t, strings.TrimSpace(done.Stdout), "", "標準出力")
}
