package start

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// ccnavi-sync.sh の受入テスト。
//
// ccnavi-sync.sh は親のブランチを取り込み（早送りか merge）、リモートから消えた親のブランチは MR がマージ済みかで「クローズした」か「消えた」かを言う。
// 親子ごとの取り込み状態や統合先のコピーは書かない。親をクローズした後の片付けは、`ready` が書く `retire/<P>.json`（実行ファイルの
// `sync retired` が列挙する）が元になる。
//
// 使い捨てのワークスペースと bare のリモートを組み、sh を外から呼ぶ。リモートを動かすのは別に clone した押し手で、ワークスペースからは
// 「他の機械が push した」「ホストがブランチを消した」ように見える。読み返すのは終了コード・標準出力と、git に残った ref、片付けの記録
// （logs/state/retire/）の有無だけ。MR がマージ済みかの問い合わせ（ccnavi-review.sh merged）は、答えを決めたスタブの sh に差し替える。
//
// 親のワークツリーかどうか・クローズしたかは sh がチケットの YAML を読まず、実行ファイル（ccnavi tree parent）と ccnavi-review.sh merged に聞く。

const (
	syncParent    = "i0001"
	syncCopy      = ".ccnavi/approved/doing/" + syncParent + ".md"
	syncBaseSHA   = "1111111111111111111111111111111111111111"
	syncNoMR      = "mr 欄に MR の番号が無いので、クローズしたか（MR がマージ済みか）判断できない"
	syncStartedAt = "2026-09-02T00:00:00+09:00"
	// syncAllow は読める承認済みチケットにするための範囲（取り込みの後の検査は読めないチケットで親子のチケットを止める）。
	syncAllow = "allow:\n  - match: Write\n    glob: \"wip/*\"\n"
)

// syncCopyText は着手済みの親の承認済みチケット（承認で欄を書かない形。着手の欄はスクリプトが書く）。mr は親のブランチの MR の番号
// （空なら欄を書かない）。ccnavi-sync.sh が MR がマージ済みかを聞くときの番号。
func syncCopyText(body, mr string) string {
	head := "---\nversion: 1\nticket: " + syncParent + "\n"
	if mr != "" {
		head += "mr: " + mr + "\nissue: 1\n"
	}
	head += syncAllow
	return head + "base_sha: " + syncBaseSHA + "\nstarted_at: '" + syncStartedAt + "'\n---\n" + body
}

func syncChildText() string {
	return "---\nversion: 1\nticket: " + syncParent + "-01-01\nparent: " + syncParent + "\nphase: 1\n" + syncAllow + "---\n"
}

// TestSyncDoesNotParseFrontmatter は、sh がチケットの YAML を自分で読まないことを見る。読み方を 2 か所に持つと、ずれに気づけない。
func TestSyncAndCommonStateDoNotParseFrontmatter(t *testing.T) {
	t.Parallel()
	for _, name := range []string{"ccnavi-sync.sh", "ccnavi-common-state.sh"} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			var code []string
			for _, line := range strings.Split(shkit.Read(t, filepath.Join(base.ScriptsDir(), name)), "\n") {
				if !strings.HasPrefix(strings.TrimLeft(line, " \t"), "#") {
					code = append(code, line)
				}
			}
			joined := strings.Join(code, "\n")
			for _, word := range []string{"frontmatter_of", "front_field", "approved_at_of", "^ticket:", "^parent:"} {
				lacks(t, joined, word)
			}
		})
	}
}

// syncEnv はワークスペース 1 つ（main）、bare のリモート、親のワークツリー .claude/worktrees/i0001。
type syncEnv struct {
	t         testing.TB
	dir       string
	ws        string
	scripts   string
	remote    string
	tree      string
	state     string
	retireDir string
}

func newSync(t testing.TB) *syncEnv {
	t.Helper()
	shkit.Need(t, "sh", "git")
	s := &syncEnv{t: t, dir: base.TempDir(t)}
	s.ws = filepath.Join(s.dir, "ws")
	s.scripts = shkit.CopyScripts(t, s.ws, "ccnavi-sync.sh", "ccnavi-git.sh")
	git(t, s.dir, "init", "-q", "-b", "main", s.ws)
	shkit.Identity(t, s.ws)
	shkit.Write(t, filepath.Join(s.ws, ".gitignore"), ".claude/worktrees/\nlogs/\n")
	shkit.Write(t, filepath.Join(s.ws, "README.md"), "seed\n")
	git(t, s.ws, "add", "--", ".gitignore", "README.md")
	git(t, s.ws, "commit", "-q", "-m", "seed")
	s.remote = filepath.Join(s.dir, "origin.git")
	git(t, s.dir, "init", "-q", "--bare", "-b", "main", s.remote)
	git(t, s.ws, "remote", "add", "origin", s.remote)
	git(t, s.ws, "push", "-q", "-u", "origin", "main")
	git(t, s.ws, "remote", "set-head", "origin", "main")
	// 親のワークツリー。承認済みの親チケット（mr: 42）をコミットして送ってある。
	s.tree = s.parentTree(syncParent, true, true)
	s.state = filepath.Join(s.ws, "logs", "state")
	// ready が書く片付けの記録の置き場。
	s.retireDir = filepath.Join(s.state, "retire")
	return s
}

// parentTree は親のワークツリー。承認済みの親チケットを置く（approved が偽なら提案だけ）。
func (s *syncEnv) parentTree(name string, push, approved bool) string {
	s.t.Helper()
	tree := filepath.Join(s.ws, ".claude", "worktrees", name)
	git(s.t, s.ws, "worktree", "add", "-q", tree, "-b", name, "main")
	var rel string
	if approved {
		rel = ".ccnavi/approved/doing/" + name + ".md"
		shkit.Write(s.t, filepath.Join(tree, rel), strings.Replace(syncCopyText("", "42"), "ticket: "+syncParent, "ticket: "+name, 1))
	} else {
		rel = "wip/proposals/todo/" + name + ".md"
		shkit.Write(s.t, filepath.Join(tree, rel), "---\nversion: 1\nticket: "+name+"\nmr: 42\n---\n")
	}
	git(s.t, tree, "add", "--", rel)
	git(s.t, tree, "commit", "-q", "-m", "copy")
	if push {
		git(s.t, tree, "push", "-q", "-u", "origin", name)
	}
	return tree
}

// addRefDifferingOnlyInCase は bare のリモートに、existing と綴りの大文字小文字だけが違うブランチ added を足す。
// loose ref にすると、大文字小文字を区別しない FS（macOS の既定）では existing と同じファイルになって足せない。
// packed-refs に 2 行を並べて置けば、FS に関わらず別の ref として読まれる。existing も packed-refs に移す。
func (s *syncEnv) addRefDifferingOnlyInCase(existing, added string) {
	s.t.Helper()
	git(s.t, s.dir, "--git-dir", s.remote, "pack-refs", "--all", "--prune")
	packed := filepath.Join(s.remote, "packed-refs")
	lines := strings.Split(strings.TrimRight(shkit.Read(s.t, packed), "\n"), "\n")
	var shaOf string
	var header, refs []string
	for _, ln := range lines {
		switch {
		case strings.HasSuffix(ln, " refs/heads/"+existing):
			shaOf = strings.Fields(ln)[0]
			refs = append(refs, ln)
		case strings.HasPrefix(ln, "#"):
			header = append(header, ln)
		case strings.HasPrefix(ln, "^"):
		default:
			refs = append(refs, ln)
		}
	}
	if shaOf == "" {
		s.t.Fatalf("packed-refs に %s が無い", existing)
	}
	refs = append(refs, shaOf+" refs/heads/"+added)
	sort.Slice(refs, func(i, j int) bool { // packed-refs は ref 名のバイト順に並ぶ
		return strings.SplitN(refs[i], " ", 2)[1] < strings.SplitN(refs[j], " ", 2)[1]
	})
	shkit.Write(s.t, packed, strings.Join(append(header, refs...), "\n")+"\n")
	names := strings.Fields(git(s.t, s.dir, "--git-dir", s.remote, "for-each-ref", "--format=%(refname)"))
	truth(s.t, contains(names, "refs/heads/"+existing) && contains(names, "refs/heads/"+added), "綴り違いの ref を並べられなかった")
}

// pusher は他の機械の代わり。bare のリモートの clone。
func (s *syncEnv) pusher() string {
	s.t.Helper()
	path := filepath.Join(s.dir, "pusher")
	if !shkit.IsDir(path) {
		git(s.t, s.dir, "clone", "-q", s.remote, path)
		shkit.Identity(s.t, path)
	}
	git(s.t, path, "fetch", "-q", "--prune", "origin")
	return path
}

// remoteCommit はリモートの <branch> に 1 件積み、その sha を返す。
func (s *syncEnv) remoteCommit(branch, rel, text string) string {
	s.t.Helper()
	path := s.pusher()
	git(s.t, path, "checkout", "-q", "-B", branch, "origin/"+branch)
	shkit.Write(s.t, filepath.Join(path, rel), text)
	git(s.t, path, "add", "--", rel)
	git(s.t, path, "commit", "-q", "-m", "remote "+rel)
	git(s.t, path, "push", "-q", "origin", branch)
	return sha(s.t, path, "HEAD")
}

func (s *syncEnv) deleteRemoteBranch(branch string) {
	s.t.Helper()
	git(s.t, s.pusher(), "push", "-q", "origin", "--delete", branch)
}

func (s *syncEnv) localCommit(rel, text string) string {
	s.t.Helper()
	return s.localCommitIn(s.tree, rel, text)
}

func (s *syncEnv) localCommitIn(tree, rel, text string) string {
	s.t.Helper()
	shkit.Write(s.t, filepath.Join(tree, rel), text)
	git(s.t, tree, "add", "--", rel)
	git(s.t, tree, "commit", "-q", "-m", "local "+rel)
	return sha(s.t, tree, "HEAD")
}

// reviewSays は ccnavi-review.sh merged のスタブ。答えと終了コードを決める。text が空なら何も答えない。
func (s *syncEnv) reviewSays(text string, code int) {
	s.t.Helper()
	body := "#!/bin/sh\nexit " + strconv.Itoa(code) + "\n"
	if text != "" {
		body = "#!/bin/sh\n[ \"$1\" = merged ] || exit 2\nprintf \"%s\\n\" \"" + text + "\"\nexit " + strconv.Itoa(code) + "\n"
	}
	shkit.Write(s.t, filepath.Join(s.scripts, "ccnavi-review.sh"), body)
}

// reviewLog は答えを決めた ccnavi-review.sh のスタブ。呼ばれた引数を 1 行ずつ残す。残す先を返す。
func (s *syncEnv) reviewLog(answer string) string {
	s.t.Helper()
	log := filepath.Join(s.dir, "review-args")
	shkit.Write(s.t, filepath.Join(s.scripts, "ccnavi-review.sh"),
		"#!/bin/sh\nprintf \"%s\\n\" \"$*\" >>'"+shkit.Slash(log)+"'\nprintf \"%s\\n\" \""+answer+"\"\n")
	return log
}

// sourceExe はソースの実行ファイルを起こすもの。launcher とは別の置き場（上書きし合わない）。
func (s *syncEnv) sourceExe() string {
	path := filepath.Join(s.dir, "source-bin", "ccnavi")
	if !shkit.Exists(path) {
		shkit.ExeWrapper(s.t, filepath.Dir(path))
	}
	return path
}

func (s *syncEnv) launcher(body string) string {
	s.t.Helper()
	if body == "" {
		return shkit.ExeWrapper(s.t, filepath.Join(s.dir, "bin"))
	}
	return shkit.WriteExec(s.t, filepath.Join(s.dir, "bin", "ccnavi"), body)
}

func (s *syncEnv) env(extra map[string]string) map[string]string {
	// sh はチケットを読まず実行ファイルに聞く（親のワークツリーか、クローズしたか）ので、ソースの実行ファイルを既定で渡す。
	// 実行ファイルの無い形を見るテストは自分で上書きする。
	return merge(map[string]string{"CCNAVI_ENTRY_PATH": s.sourceExe()}, extra)
}

// sync は ccnavi-sync.sh を ws で起こす。
func (s *syncEnv) sync(args ...string) run.Result { return s.syncEnvRun(nil, args...) }

func (s *syncEnv) syncEnvRun(extra map[string]string, args ...string) run.Result {
	s.t.Helper()
	return runScript(s.t, filepath.Join(s.scripts, "ccnavi-sync.sh"), s.ws, s.env(extra), args...)
}

// retireRecord は ready の後の片付けの記録（`retire/<P>.json`）を置く。パスを返す。mr が 0 なら欄を書かない。
func (s *syncEnv) retireRecord(mr int, branch string) string {
	s.t.Helper()
	data := map[string]any{"parent": syncParent, "project": "", "branch": branch, "children": []any{}}
	if mr != 0 {
		data["mr"] = mr
	}
	text, err := json.MarshalIndent(data, "", " ")
	if err != nil {
		s.t.Fatal(err)
	}
	return shkit.Write(s.t, filepath.Join(s.retireDir, syncParent+".json"), string(text)+"\n")
}

func (s *syncEnv) lockDir() string { return filepath.Join(s.state, "locks", "self", syncParent) }

// ownLock はロックの持ち主を書いたロックを置く。host が空なら、この機械の名前。
func (s *syncEnv) ownLock(pid int, started int64, host string) string {
	s.t.Helper()
	if host == "" {
		h, err := os.Hostname()
		if err != nil {
			s.t.Fatal(err)
		}
		host = h
	}
	lock := s.lockDir()
	if err := os.MkdirAll(filepath.Dir(lock), 0o755); err != nil {
		s.t.Fatal(err)
	}
	if err := os.Mkdir(lock, 0o755); err != nil {
		s.t.Fatal(err)
	}
	shkit.Write(s.t, filepath.Join(lock, "owner"), host+" "+strconv.Itoa(pid)+" "+strconv.FormatInt(started, 10)+" "+strconv.Itoa(pid)+"-"+strconv.FormatInt(started, 10)+"\n")
	return lock
}

// deadPid は終わったプロセスの pid（sh から見た `$$`）。
func (s *syncEnv) deadPid() int {
	s.t.Helper()
	r := run.Command(s.t, "sh", []string{"-c", "echo $$"})
	n, err := strconv.Atoi(strings.TrimSpace(r.Stdout))
	if err != nil {
		s.t.Fatalf("pid を読めない: %q", r.Stdout)
	}
	return n
}

func (s *syncEnv) branches() []string {
	return strings.Fields(git(s.t, s.ws, "branch", "--format=%(refname:short)"))
}

func (s *syncEnv) ignoreLocally(rel string) {
	s.t.Helper()
	appendTo(s.t, filepath.Join(s.ws, ".git", "info", "exclude"), rel+"\n")
}

func (s *syncEnv) archiveLocally(text string) {
	s.t.Helper()
	shkit.Write(s.t, filepath.Join(s.ws, "logs", "archive", "self", "done", syncParent+".md"), text)
}

// ---- 取り込み（P がリモートにある）

func TestSyncFastForwardAndLeavesNoState(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	head := s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
	done := code(t, s.sync(syncParent), 0)
	eq(t, sha(t, s.tree, "HEAD"), head, "HEAD")
	has(t, done.Stdout, "早送り", "統合先: main")
	// 親子ごとの取り込み状態も、統合先のコピーも、もう書かない。
	truth(t, !shkit.Exists(filepath.Join(s.state, "sync")), "取り込み状態を書いた")
	truth(t, !shkit.Exists(s.retireDir), "片付けの記録を書いた")
	truth(t, !shkit.Exists(s.lockDir()), "ロックが残っている")
}

func TestSyncDivergedBranchesAreMerged(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	theirs := s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
	mine := s.localCommit("mine.txt", "mine\n")
	done := code(t, s.sync(syncParent), 0)
	has(t, done.Stdout, "merge で取り込んだ")
	parents := strings.Fields(git(t, s.tree, "rev-list", "--parents", "-n", "1", "HEAD"))[1:]
	eq(t, strings.Join(sortedCopy(parents), ","), strings.Join(sortedCopy([]string{theirs, mine}), ","), "merge の親")
}

func sortedCopy(in []string) []string {
	out := append([]string{}, in...)
	sort.Strings(out)
	return out
}

func TestSyncAFastForwardDoesNotOverwriteAnIgnoredFile(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
	s.ignoreLocally("theirs.txt")
	shkit.Write(t, filepath.Join(s.tree, "theirs.txt"), "手元の ignored\n")
	before := sha(t, s.tree, "HEAD")
	done := s.sync(syncParent)
	eq(t, sha(t, s.tree, "HEAD"), before, "HEAD ("+done.Stdout+")")
	has(t, done.Stdout, "早送りできなかった")
	eq(t, shkit.Read(t, filepath.Join(s.tree, "theirs.txt")), "手元の ignored\n", "ignored のファイル")
}

func TestSyncAConflictIsAbortedAndLeftToAPerson(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.remoteCommit(syncParent, syncCopy, syncCopyText("theirs\n", "42"))
	mine := s.localCommit(syncCopy, syncCopyText("mine\n", "42"))
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, "衝突", syncCopy)
	eq(t, sha(t, s.tree, "HEAD"), mine, "HEAD")
	truth(t, !shkit.Exists(gitPath(t, s.tree, "MERGE_HEAD")), "MERGE_HEAD が残った")
	eq(t, git(t, s.tree, "status", "--porcelain"), "", "作業ツリー")
}

// ユーザ自身の途中の merge を取りやめない。
func TestSyncAMergeInProgressIsLeftAlone(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	git(t, s.tree, "checkout", "-q", "-b", "side")
	s.localCommit(syncCopy, syncCopyText("side\n", "42"))
	git(t, s.tree, "checkout", "-q", syncParent)
	s.localCommit(syncCopy, syncCopyText("mine\n", "42"))
	gitRes(t, s.tree, "merge", "side")
	mergeHead := gitPath(t, s.tree, "MERGE_HEAD")
	truth(t, shkit.Exists(mergeHead), "前提: merge が途中")
	s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, "途中の操作（MERGE_HEAD）")
	truth(t, shkit.Exists(mergeHead), "ユーザの merge を取りやめた")
}

func TestSyncStagedChangesStopAMerge(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
	mine := s.localCommit("mine.txt", "mine\n")
	shkit.Write(t, filepath.Join(s.tree, "staged.txt"), "x\n")
	git(t, s.tree, "add", "--", "staged.txt")
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, "ステージ済み")
	eq(t, sha(t, s.tree, "HEAD"), mine, "HEAD")
}

func TestSyncFastForwardThatOverlapsWorkInProgressStops(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.remoteCommit(syncParent, "README.md", "theirs\n")
	before := sha(t, s.tree, "HEAD")
	shkit.Write(t, filepath.Join(s.tree, "README.md"), "書きかけ\n")
	// 書きかけとの重なりは終了コード 3（write-and-push が文を読まずに言い分ける）
	done := code(t, s.sync(syncParent), 3)
	has(t, done.Stdout, "書きかけの README.md と重なる")
	eq(t, sha(t, s.tree, "HEAD"), before, "HEAD")
	eq(t, shkit.Read(t, filepath.Join(s.tree, "README.md")), "書きかけ\n", "書きかけ")
}

// 重なっていないのに「（空）と重なる」と言わない。
func TestSyncARefusalThatIsNotAnOverlapSaysWhy(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
	shkit.Write(t, gitPath(t, s.tree, "index.lock"), "")
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, "index.lock")
	lacks(t, done.Stdout, "と重なる")
}

func TestSyncNoArgumentsTakesEveryParentWorktree(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	git(t, s.ws, "worktree", "add", "-q", filepath.Join(s.ws, ".claude", "worktrees", "free"), "-b", "free")
	head := s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
	done := code(t, s.sync(), 0)
	eq(t, sha(t, s.tree, "HEAD"), head, "HEAD")
	lacks(t, done.Stdout, "free")
}

// 引数の選び方（同じ親を 1 回だけ取る）は Go の `TestSyncPlanPicksTheFamilies` が見る。実行ファイルが無ければ、判定を持つ側がいないので、
// 組むか置くかを言って止める。
func TestSyncWithoutTheExecutableItSaysToBuildOrPlaceIt(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	done := code(t, s.syncEnvRun(map[string]string{"CCNAVI_ENTRY_PATH": filepath.Join(s.dir, "nowhere")}, syncParent), 2)
	has(t, done.Stderr, "ccnavi-build.sh")
}

// ---- 取り込みの後の検査（`--lint` の該当分と判定し直し）

func TestSyncTheCheckReportsAnErrorAndALaterRunClearsIt(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	launcher := map[string]string{"CCNAVI_ENTRY_PATH": s.launcher("")}
	code(t, s.syncEnvRun(launcher, syncParent), 0)
	// 元ツリーに未コミットで残った子チケット（親のワークツリーの外）。検査が error を言い、終了コードが 1 になる。状態は何も書かない。
	stray := shkit.Write(t, filepath.Join(s.ws, ".ccnavi", "approved", "doing", syncParent+"-01-01.md"), syncChildText())
	done := code(t, s.syncEnvRun(launcher, syncParent), 1)
	has(t, done.Stdout, "取り込みの後の検査で止める理由がある", "ワークツリーの外", "再実行してください")
	truth(t, !shkit.Exists(filepath.Join(s.state, "sync")), "取り込み状態を書いた")
	// 理由を片付けて打ち直せば、検査し直して通る。
	shkit.Remove(t, stray)
	again := code(t, s.syncEnvRun(launcher, syncParent), 0)
	lacks(t, again.Stdout, "検査で止める")
}

// 検査が error を言っても、ロックの再取得も書き込みも無い。終了コードは 1 だけ。
func TestSyncTheCheckNeverEndsWith3(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	launcher := s.launcher("")
	holder := shkit.WriteExec(t, filepath.Join(s.dir, "bin", "holder"), "#!/bin/sh\n"+
		"case \"$*\" in *\"sync recheck\"*)\n"+
		"  mkdir -p \""+shkit.Slash(s.state)+"/locks/self/"+syncParent+"\" && "+
		"printf \"elsewhere 1 9999999999 1-1\\n\" "+
		"> \""+shkit.Slash(s.state)+"/locks/self/"+syncParent+"/owner\" ;; esac\n"+
		"exec \""+shkit.Slash(launcher)+"\" \"$@\"\n")
	shkit.Write(t, filepath.Join(s.ws, ".ccnavi", "approved", "doing", syncParent+"-01-01.md"), syncChildText())
	done := code(t, s.syncEnvRun(map[string]string{"CCNAVI_ENTRY_PATH": holder, "CCNAVI_LOCK_WAIT": "0"}, syncParent), 1)
	lacks(t, done.Stdout, "まだ止まっていない")
}

// リモートに親のブランチが無い（消えた）親子のチケットは検査しない。
func TestSyncTheCheckIsSkippedForABranchGoneFromTheRemote(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	shkit.Write(t, filepath.Join(s.ws, ".ccnavi", "approved", "doing", syncParent+"-01-01.md"), syncChildText())
	s.reviewSays("none", 0)
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.syncEnvRun(map[string]string{"CCNAVI_ENTRY_PATH": s.launcher("")}, syncParent), 1)
	has(t, done.Stdout, "戻し方 1")
	lacks(t, done.Stdout, "検査")
}

// origin の fetch の refspec が main だけでも、origin/P を進めて取り込む。sh の中の fetch は行き先を書く。
func TestSyncASingleBranchCloneStillSeesTheParentBranch(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	git(t, s.ws, "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
	head := s.remoteCommit(syncParent, "theirs.txt", "theirs\n")
	code(t, s.sync(syncParent), 0)
	eq(t, sha(t, s.tree, "HEAD"), head, "HEAD")
}

// ---- P がリモートに無い（マージ後に消えたか、改名・消し間違いか）

// 一度も送っていない親（origin/P の参照も、承認済みの親チケットも、依頼のマーカーも無い）。
func TestSyncAFamilyNeverPushedIsLeftAsItIs(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	tree := s.parentTree("i0002", false, false)
	done := code(t, s.sync("i0002"), 0)
	has(t, done.Stdout, "まだ送っていない")
	truth(t, shkit.IsDir(tree), "ワークツリーが消えた")
}

// 参照（origin/P）が fetch.prune で消えても、承認済みの親チケットがあれば送った親にする。
func TestSyncAFamilyWithAnApprovedTicketIsNotTakenForNeverPushed(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.reviewSays("none", 0)
	git(t, s.ws, "update-ref", "-d", "refs/remotes/origin/"+syncParent)
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.sync(syncParent), 1)
	lacks(t, done.Stdout, "まだ送っていない")
	has(t, done.Stdout, "戻し方 1")
}

func (s *syncEnv) closeOnMain() {
	s.t.Helper()
	s.remoteCommit("main", ".ccnavi/approved/done/"+syncParent+".md", syncCopyText("", "42"))
}

// mine は親のワークツリーの承認済みの親チケットを置き換える。
func (s *syncEnv) mine(text string) {
	s.t.Helper()
	s.localCommit(syncCopy, text)
	git(s.t, s.tree, "push", "-q", "origin", syncParent)
}

// closedAfterDelete はリモートの親のブランチを消して sync を打つ。MR の答えは「マージされていない」。
func (s *syncEnv) closedAfterDelete() run.Result {
	s.t.Helper()
	s.reviewSays("none", 0)
	s.deleteRemoteBranch(syncParent)
	return s.sync(syncParent)
}

func assertGone(t testing.TB, done run.Result) {
	t.Helper()
	code(t, done, 1)
	has(t, done.Stdout, "戻し方 1")
}

// クローズしたかは MR がマージ済みかだけで決める（マージされていなければ、消えた親として止める）。統合先の done/ にある同じ識別子の親チケット・
// 手元の退避（ready が移した先）の親・前方一致するブランチ（i0001-x）は、どれもクローズした根拠にも、在る根拠にもしない。
func TestSyncADoneCopyOnTheIntegrationBranchIsNotConsulted(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.closeOnMain()
	s.archiveLocally(syncCopyText("", "42"))
	pusher := s.pusher()
	git(t, pusher, "push", "-q", "origin", "origin/"+syncParent+":refs/heads/"+syncParent+"-x")
	done := s.closedAfterDelete()
	assertGone(t, done)
	lacks(t, done.Stdout, "クローズ済みの親子のチケット")
	truth(t, !shkit.Exists(filepath.Join(s.state, "sync")), "取り込み状態を書いた")
}

// 承認待ち（todo/ の提案）の親は、承認済みのチケットだけを読む決まりで mr 欄を読めない。提案に mr: があっても「mr 欄に番号が無い」とは
// 言わない（言えば事実と合わない警告になる）。
func TestSyncAParentWaitingForApprovalDoesNotWarnAboutTheMRField(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.backToTodo()
	log := s.reviewLog("none")
	done := code(t, s.sync(syncParent), 0)
	lacks(t, out(done), "mr 欄に MR の番号が無い")
	truth(t, !shkit.Exists(log), "MR を聞きに行った")
}

// backToTodo は親チケットを承認前の提案に戻して push する。
func (s *syncEnv) backToTodo() {
	s.t.Helper()
	shkit.Mkdir(s.t, filepath.Join(s.tree, "wip", "proposals", "todo"))
	git(s.t, s.tree, "mv", "--", syncCopy, "wip/proposals/todo/"+syncParent+".md")
	git(s.t, s.tree, "commit", "-q", "-m", "back to todo")
	git(s.t, s.tree, "push", "-q", "origin", syncParent)
}

// 承認前の提案だけが残る親のワークツリー（この親子のチケットはクローズしようがない）。
func TestSyncAParentMissingInTheParentTreeIsNotClosed(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.backToTodo()
	s.closeOnMain()
	done := code(t, s.closedAfterDelete(), 1)
	lacks(t, done.Stdout, "クローズ済みの親子のチケット")
}

func TestSyncABranchGoneWithoutAClosedRecordStopsTheFamily(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.reviewSays("none", 0)
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, "決められない", "push のゲート", "戻し方 1", "git push origin "+syncParent, "戻し方 2", "worktree remove", "retire --abandon i0001")
	lacks(t, done.Stdout, "--forget")
	// 状態は書かない。ワークツリーが在る間は、何度打っても 1。
	truth(t, !shkit.Exists(filepath.Join(s.state, "sync")), "取り込み状態を書いた")
	code(t, s.sync(syncParent), 1)
	code(t, s.sync(), 1)
	// 消えた親のブランチをユーザが戻せば、次の sync は普通に取り込む。
	git(t, s.tree, "push", "-q", "origin", syncParent)
	restored := code(t, s.sync(syncParent), 0)
	has(t, restored.Stdout, "リモートと同じ")
}

// MR の番号（親チケットの mr 欄）が無いので、ブランチから MR を探す推定はせずに止める。
func TestSyncAPushedBranchWithoutAnMRNumberStopsWithoutGuessing(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	log := filepath.Join(s.dir, "review-args")
	shkit.Write(t, filepath.Join(s.scripts, "ccnavi-review.sh"), "#!/bin/sh\necho \"$*\" >>'"+shkit.Slash(log)+"'\n")
	s.mine(syncCopyText("", ""))
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, syncNoMR)
	truth(t, !shkit.Exists(log), "MR を聞きに行った")
}

// API が失敗した・道具が無い。「消えた」とは言わず、確かめられなかったと言って止まる。
// 空の答え・形の違う答えの読みは catchup.TestMergedAnswersWithoutTidying が見る。
func TestSyncAnUnknownMergeAnswerStopsWithoutCallingItGone(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.reviewSays("unknown", 3)
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, "確かめられなかった")
	lacks(t, done.Stdout, "戻し方")
}

// 統合先にはクローズしたチケットを残さない（ready が退避して消し、squash でマージする）ので、MR がマージ済みと分かればクローズしたと読み、
// 手元の親のブランチを片付ける。MR の番号は親チケットの mr 欄から引く。
func TestSyncAMergedRequestCleansUpWithoutADoneCopy(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	head := sha(t, s.tree, "HEAD")
	log := s.reviewLog("merged 42 " + head)
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.sync(syncParent), 0)
	has(t, done.Stdout, "マージ済み", "42", "ブランチ "+syncParent+" を削除しました")
	lacks(t, done.Stdout, "戻し方")
	eq(t, strings.TrimSpace(shkit.Read(t, log)), "merged 42", "MR を聞いた引数")
	truth(t, !shkit.Exists(s.tree), "親のワークツリーが残った")
	eq(t, strings.Join(s.branches(), ","), "main", "手元のブランチ")
	truth(t, !shkit.Exists(s.lockDir()), "ロックが残っている")
}

func TestSyncAnUnpushedCommitKeepsTheBranch(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	mergedHead := sha(t, s.tree, "HEAD")
	s.localCommit("notes.txt", "未 push\n")
	s.reviewSays("merged 42 "+mergedHead, 0)
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.sync(syncParent), 0)
	has(t, done.Stdout, "未 push のコミットがある", "branch -D "+syncParent, "消さなかったものがある")
	truth(t, contains(s.branches(), syncParent), "ブランチが消えた")
}

func TestSyncUncommittedChangesKeepTheWorktree(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.reviewSays("merged 42 "+sha(t, s.tree, "HEAD"), 0)
	shkit.Write(t, filepath.Join(s.tree, "memo.txt"), "書きかけ\n")
	s.deleteRemoteBranch(syncParent)
	done := s.sync(syncParent)
	has(t, done.Stdout, "未コミットの変更があるので、何も削除しませんでした")
	truth(t, shkit.IsDir(s.tree), "ワークツリーが消えた")
	truth(t, contains(s.branches(), syncParent), "ブランチが消えた")
}

func TestSyncWithoutTheHeadOfTheRequestNothingIsDeleted(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.reviewSays("merged 42", 0)
	s.deleteRemoteBranch(syncParent)
	done := s.sync(syncParent)
	has(t, done.Stdout, "先頭の sha をホストから取れなかった")
	truth(t, shkit.IsDir(s.tree), "ワークツリーが消えた")
	truth(t, contains(s.branches(), syncParent), "ブランチが消えた")
}

// ---- 親のワークツリーを片付けた後（ready の後）。片付けの記録と手元のブランチが残る

// retireTree は ready の後の状態にする。片付けの記録は、ワークツリーを消す前に置いておく。
func (s *syncEnv) retireTree() { git(s.t, s.ws, "worktree", "remove", s.tree) }

func TestSyncARetiredFamilyIsCleanedWhenItsRequestIsMerged(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	head := sha(t, s.tree, "HEAD")
	record := s.retireRecord(42, syncParent)
	s.retireTree()
	log := s.reviewLog("merged 42 " + head + " " + syncParent)
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.sync(), 0)
	has(t, done.Stdout, "ワークツリーは片付け済み", "親のブランチ "+syncParent+" はリモートから消えた", "ブランチ "+syncParent+" を削除しました")
	// 片付けの記録は実行ファイル（sync tidy）が消す。sh は消さない。
	truth(t, !shkit.Exists(record), "片付けの記録が残った")
	eq(t, strings.Join(s.branches(), ","), "main", "手元のブランチ")
	// ホストには記録の MR の番号で聞く。
	eq(t, strings.TrimSpace(shkit.Read(t, log)), "merged 42", "MR を聞いた引数")
	// 打ち直しても何も起きない
	code(t, s.sync(), 0)
}

// ---- リモートにブランチが残っていても、MR がマージ済みならクローズしている

func TestSyncAMergedRequestWhoseBranchIsLeftOnTheRemoteIsCleaned(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	head := sha(t, s.tree, "HEAD")
	s.reviewSays("merged 42 "+head+" "+syncParent, 0)
	done := code(t, s.sync(syncParent), 0)
	has(t, done.Stdout, "親のブランチ "+syncParent+" はリモートに残っているが", "片付けた")
	truth(t, !shkit.Exists(s.tree), "親のワークツリーが残った")
	eq(t, strings.Join(s.branches(), ","), "main", "手元のブランチ")
}

func TestSyncAPresentFamilyWithoutAnMRNumberIsNotGuessed(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.mine(syncCopyText("", ""))
	log := s.reviewLog("merged 7 " + sha(t, s.tree, "HEAD") + " " + syncParent)
	// 取り込みの後の検査も、mr の無い親チケットを error と言う（終了コード 1）。
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, syncNoMR, "mr: が記録されていない")
	truth(t, !shkit.Exists(log), "MR を聞きに行った")
	truth(t, shkit.IsDir(s.tree), "ワークツリーが消えた")
}

// ---- ready の後に残った記録（present 以外も）

// 記録にブランチが無ければ、ブランチ名は MR の元ブランチから取る。
func TestSyncARetiredFamilyTakesTheBranchFromTheRequest(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	head := sha(t, s.tree, "HEAD")
	s.retireRecord(42, "")
	s.retireTree()
	s.reviewSays("merged 42 "+head+" "+syncParent, 0)
	done := code(t, s.sync(), 0)
	// ブランチはリモートに残っている。消えたとは言わない。
	has(t, done.Stdout, "親のブランチ "+syncParent+" はリモートに残っている", "ブランチ "+syncParent+" を削除しました")
	lacks(t, done.Stdout, "リモートから消え")
	eq(t, strings.Join(s.branches(), ","), "main", "手元のブランチ")
}

// 入れ子（外側が持つロック）は、片付けの段階で消さない。外すのは持ち主だけ。
func TestSyncARetiredFamilyKeepsTheLockHeldByTheCaller(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	record := s.retireRecord(42, syncParent)
	s.retireTree()
	s.reviewSays("none", 0)
	started := time.Now().Unix()
	pid := base.LiveShPid(t)
	lock := s.ownLock(pid, started, "")
	done := s.syncEnvRun(map[string]string{"CCNAVI_LOCK_HELD": "self/" + syncParent + ":" + strconv.Itoa(pid) + "-" + strconv.FormatInt(started, 10)})
	code(t, done, 0)
	truth(t, shkit.IsDir(lock), "外側のロックを外した")
	truth(t, shkit.Exists(record), "片付けの記録を消した")
}

// マージ済みかを確かめられないときは、手元の退避（ready が移した先）の親があっても推定せず、何も変えずに止める。
func TestSyncTheLocalArchiveDoesNotCloseItWhenMergeIsUnknown(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.reviewSays("unknown", 3)
	s.archiveLocally(syncCopyText("", "42"))
	s.deleteRemoteBranch(syncParent)
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, "確かめられなかった")
	lacks(t, done.Stdout, "クローズ済みの親子のチケット")
}

// archiveTheParent は ready の後の状態。親チケットをツリーから消して push し、手元の退避に置く。
func (s *syncEnv) archiveTheParent() {
	s.t.Helper()
	s.archiveLocally(syncCopyText("", "42"))
	git(s.t, s.tree, "rm", "-q", "--", syncCopy)
	git(s.t, s.tree, "commit", "-q", "-m", "退避")
	git(s.t, s.tree, "push", "-q", "origin", syncParent)
}

func TestSyncAParentTreeWhoseCopyWasArchivedIsStillAParentTree(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.archiveTheParent()
	code(t, s.sync(syncParent), 0)
	listed := code(t, s.sync(), 0)
	has(t, listed.Stdout, syncParent)
}

// ready の後（承認済みの親チケットはツリーに無く、退避に在る）で、参照も消えていても、リモートに無い親は「まだ送っていない」とは言わない。
func TestSyncAnArchivedParentCountsAsHavingBeenPushed(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.archiveTheParent()
	git(t, s.ws, "update-ref", "-d", "refs/remotes/origin/"+syncParent)
	s.reviewSays("none", 0)
	s.deleteRemoteBranch(syncParent)
	done := s.sync(syncParent)
	lacks(t, out(done), "まだ送っていない")
}

// ---- 廃止した `--forget`（放棄した親の記録は `worktree retire --abandon` で消す）

func TestSyncForgetIsGoneAndSaysWhatToDoInstead(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	done := code(t, s.sync("--forget", syncParent), 2)
	has(t, done.Stderr, "廃止", "retire --abandon")
}

// 放棄した親（リモートのブランチが消え、MR はマージされていない）は、ワークツリーが在る間は引数なしでも名指しでも 1 を返す。
// ワークツリーを片付ければ、列挙されなくなり 1 が止まる。
func TestSyncAnAbandonedParentStopsBeingListedWhenItsWorktreeIsRemoved(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.reviewSays("none", 0)
	s.deleteRemoteBranch(syncParent)
	code(t, s.sync(syncParent), 1)
	code(t, s.sync(), 1)
	git(t, s.ws, "worktree", "remove", "--force", s.tree)
	done := code(t, s.sync(), 0)
	lacks(t, done.Stdout, syncParent)
	// 名指しすると、ワークツリーが無いと言う（片付けの記録も無い）。
	named := code(t, s.sync(syncParent), 1)
	has(t, named.Stdout, "親のワークツリー（.claude/worktrees/i0001）がありません")
}

// ---- 統合先（設定か、無ければホストのデフォルトブランチ）と、統合先への取り込み（最後に取り込んだ先頭）

// 実行ファイルが在るのに答えなければ、統合先を何も言わずに既定に戻さない。
func TestSyncAFailingExecutableStopsInsteadOfUsingTheDefault(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	launcher := s.launcher("#!/bin/sh\necho broken >&2\nexit 1\n")
	done := code(t, s.syncEnvRun(map[string]string{"CCNAVI_ENTRY_PATH": launcher}, syncParent), 2)
	has(t, done.Stderr, "返さなかった")
}

// 手元の origin/HEAD が無くても、ls-remote --symref でホストの既定を読む。fetch が成功した後に、手元の origin/HEAD をそれに向け直して 1 行出す。
func TestSyncTheHostDefaultBranchIsReadFromTheRemote(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	git(t, s.pusher(), "push", "-q", "origin", "origin/main:refs/heads/trunk")
	git(t, s.dir, "--git-dir", s.remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
	git(t, s.ws, "remote", "set-head", "origin", "--delete")
	done := code(t, s.sync(syncParent), 0)
	has(t, done.Stdout, "統合先: trunk", "統合先を 未設定 から trunk に合わせた（ホストの既定ブランチ）")
	eq(t, git(t, s.ws, "symbolic-ref", "refs/remotes/origin/HEAD"), "refs/remotes/origin/trunk", "origin/HEAD")
}

func TestSyncAChangedHostDefaultBranchMovesTheLocalOriginHead(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	git(t, s.pusher(), "push", "-q", "origin", "origin/main:refs/heads/trunk")
	git(t, s.dir, "--git-dir", s.remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
	eq(t, git(t, s.ws, "symbolic-ref", "refs/remotes/origin/HEAD"), "refs/remotes/origin/main", "前提の origin/HEAD")
	done := code(t, s.sync(syncParent), 0)
	has(t, done.Stdout, "統合先を main から trunk に合わせた（ホストの既定ブランチ）")
	eq(t, git(t, s.ws, "symbolic-ref", "refs/remotes/origin/HEAD"), "refs/remotes/origin/trunk", "origin/HEAD")
	// 合っていれば、2 回目は出さない。
	lacks(t, s.sync(syncParent).Stdout, "合わせた")
}

// ホストが Main と答え、手元は main。綴り違いだけなので同じ名前として、向け直さない。
func TestSyncAHostDefaultThatDiffersOnlyInCaseDoesNotMoveTheOriginHead(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	s.addRefDifferingOnlyInCase("main", "Main")
	git(t, s.dir, "--git-dir", s.remote, "symbolic-ref", "HEAD", "refs/heads/Main")
	done := code(t, s.sync(syncParent), 0)
	lacks(t, done.Stdout, "合わせた")
	has(t, done.Stdout, "統合先: main")
	eq(t, git(t, s.ws, "symbolic-ref", "refs/remotes/origin/HEAD"), "refs/remotes/origin/main", "origin/HEAD")
	// 綴り違いの ref は取らない（大文字小文字を区別しない FS で origin/main と衝突する）。
	refs := strings.Fields(git(t, s.ws, "for-each-ref", "--format=%(refname)", "refs/remotes/origin"))
	truth(t, !contains(refs, "refs/remotes/origin/Main"), "綴り違いの ref を取った")
}

// ---- 止まる・対象外

func TestSyncAnUnreachableRemoteStops(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	git(t, s.ws, "remote", "set-url", "origin", filepath.Join(s.dir, "nowhere.git"))
	done := code(t, s.sync(syncParent), 1)
	has(t, done.Stdout, "取ってこられなかった")
}

// 引数を省いた回で、親子のチケットの無いプロジェクトの ls-remote が失敗しても 1 にしない。
func TestSyncAProjectWithoutFamiliesDoesNotFailTheRun(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	project := filepath.Join(s.ws, "projects", "p")
	git(t, s.dir, "init", "-q", "-b", "main", project)
	git(t, project, "remote", "add", "origin", filepath.Join(s.dir, "nowhere.git"))
	done := code(t, s.sync(), 0)
	has(t, done.Stdout, "p: リモートのブランチの一覧を取ってこられなかった")
}

func TestSyncWithoutOriginNothingHappens(t *testing.T) {
	t.Parallel()
	s := newSync(t)
	git(t, s.ws, "remote", "remove", "origin")
	done := code(t, s.sync(), 0)
	has(t, done.Stdout, "origin がありません。")
	truth(t, !shkit.Exists(filepath.Join(s.state, "sync")), "取り込み状態を書いた")
}
