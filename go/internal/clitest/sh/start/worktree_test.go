package start

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// ccnavi-worktree.sh の受入テスト。
//
// 使い捨てのワークスペースと、origin の代わりの bare リポジトリを組み、sh を外から呼ぶ。fetch のときだけ origin の URL を bare リポジトリへ
// 向ける git のスタブを PATH の先頭に置く（startenv_test.go と同じ作り）。
//
// 見るのは次のとおり。
//  1. ずれが無ければ確認なしで、取り込み先（ユーザのチェックアウトのブランチ）の HEAD から切ること
//  2. C4〜C7 のずれがあれば、切らずに終了コード 3 で止まり、報告と確認コードを出すこと。確認コードが今の状態と合わなければ報告からやり直すこと
//  3. 書きかけは --no-wip なら入らず、--copy-wip なら確認した時点のものが S になり、ユーザの index とワークツリーは変わらないこと
//  4. detached・チケットに結び付く・結び付くかを確かめられない・git 2.39 では断ること
//  5. 記録（version 4）が実行後チェックの読み取り（infra/directrec）で有効と読めること
//  6. 既にあるワークツリーは何も切らずにパスと取り込みの記録の有無を出すこと（冪等）、既にあるブランチ・使えない名前は断ること
//  7. --dry-run は何も作らないこと。廃止した --offline / --from-origin は終了コード 2
//  8. --adopt は、記録の無い既存のワークツリーを 2 回の呼び出し（確認コード）で v4 の記録に引き取ること。merge-base が複数なら断ること。
//     記録の無いワークツリーの integrate は --adopt を案内して断ること

// worktreeGit は fetch のときだけ、origin の URL を bare リポジトリへ向ける git のスタブ。
// FAKE_FETCH_FAIL があれば失敗させる。FAKE_LS_REMOTE_FAIL があれば ls-remote だけ失敗させる。
// FAKE_GIT_VERSION があれば `git version` をその版で答える。FAKE_READ_TREE_FAIL があれば read-tree だけ失敗させる。
const worktreeGit = `#!/bin/sh
if [ "${1:-}" = version ] && [ -n "${FAKE_GIT_VERSION:-}" ]; then
  echo "git version $FAKE_GIT_VERSION"
  exit 0
fi
for a in "$@"; do
  case "$a" in
  read-tree)
    if [ -n "${FAKE_READ_TREE_FAIL:-}" ]; then
      echo "fatal: failed to read tree" >&2
      exit 128
    fi ;;
  ls-remote)
    if [ -n "${FAKE_LS_REMOTE_FAIL:-}" ]; then
      echo "fatal: unable to access 'https://example.invalid/': timed out" >&2
      exit 128
    fi ;;
  esac
done
for a in "$@"; do
  case "$a" in
  fetch | push | pull | ls-remote)
    if [ -n "${FAKE_FETCH_FAIL:-}" ]; then
      if [ "$FAKE_FETCH_FAIL" = 1 ]; then
        echo "fatal: unable to access 'https://example.invalid/': Could not resolve host" >&2
      else
        echo "$FAKE_FETCH_FAIL" >&2
      fi
      exit 128
    fi
    exec '%GIT%' -c "url.%BARE%.insteadOf=$FAKE_ORIGIN_URL" "$@" ;;
  esac
done
exec '%GIT%' "$@"
`

const (
	worktreeOrigin = "https://github.com/acme/widgets.git"
	projectsHint   = "projects/ の下のプロジェクトの変更を頼まれたなら"
	parentName     = "p0001"
)

type wtEnv struct {
	t      testing.TB
	dir    string
	wsName string
	ws     string
	bare   string
	exe    string
	bin    string
}

// newWorktree はワークスペース（ディレクトリ名 wsName）と bare の origin を組む。
func newWorktree(t testing.TB, wsName string) *wtEnv {
	t.Helper()
	shkit.Need(t, "sh", "git")
	w := &wtEnv{t: t, dir: base.TempDir(t), wsName: wsName}
	w.bare = filepath.Join(w.dir, "origin.git")
	git(t, w.dir, "init", "-q", "--bare", "-b", "main", w.bare)
	w.ws = filepath.Join(w.dir, wsName)
	shkit.Mkdir(t, w.ws)
	git(t, w.dir, "init", "-q", "-b", "main", w.ws)
	shkit.Identity(t, w.ws)
	shkit.Write(t, filepath.Join(w.ws, ".gitignore"), "logs/\n.ccnavi/\n.claude/\n")
	git(t, w.ws, "add", ".gitignore")
	git(t, w.ws, "commit", "-q", "-m", "seed")
	git(t, w.ws, "push", "-q", w.bare, "main")
	git(t, w.ws, "remote", "add", "origin", worktreeOrigin)
	git(t, w.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
	git(t, w.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	shkit.CopyScripts(t, w.ws, "ccnavi-worktree.sh", "ccnavi-git.sh")
	base.PutMessages(t, w.ws)
	w.exe = shkit.ExeWrapper(t, filepath.Join(w.dir, "exe"))
	w.bin = filepath.Join(w.dir, "bin")
	shkit.WriteExec(t, filepath.Join(w.bin, "git"), strings.NewReplacer("%GIT%", lookGit(t), "%BARE%", shkit.Slash(w.bare)).Replace(worktreeGit))
	return w
}

func (w *wtEnv) env(extra map[string]string) map[string]string {
	return merge(map[string]string{
		"PATH":              w.bin + string(os.PathListSeparator) + os.Getenv("PATH"),
		"CCNAVI_ENTRY_PATH": w.exe,
		"FAKE_ORIGIN_URL":   worktreeOrigin,
	}, extra)
}

// sh は ccnavi-worktree.sh を ws で起こす。
func (w *wtEnv) sh(args ...string) run.Result { return w.shIn(w.ws, nil, args...) }

// shEnv は env を足して起こす。
func (w *wtEnv) shEnv(extra map[string]string, args ...string) run.Result {
	return w.shIn(w.ws, extra, args...)
}

// shIn は cwd で、env を足して起こす。
func (w *wtEnv) shIn(cwd string, extra map[string]string, args ...string) run.Result {
	w.t.Helper()
	return runScript(w.t, filepath.Join(w.ws, ".ccnavi", "scripts", "ccnavi-worktree.sh"), cwd, w.env(extra), args...)
}

// failFetch は fetch が名前を引けずに失敗する環境。
var failFetch = map[string]string{"FAKE_FETCH_FAIL": "1"}

func (w *wtEnv) wt(name string) string { return filepath.Join(w.ws, ".claude", "worktrees", name) }

func (w *wtEnv) headOf(name string) string { return git(w.t, w.wt(name), "rev-parse", "HEAD") }

// snapshotOfRoot はユーザの index とワークツリーの状態（変わらないことを確かめるため）。
func (w *wtEnv) snapshotOfRoot() string {
	return strings.Join([]string{
		git(w.t, w.ws, "status", "--porcelain=v1", "--untracked-files=all"),
		git(w.t, w.ws, "diff", "--cached"),
		git(w.t, w.ws, "diff"),
		git(w.t, w.ws, "rev-parse", "HEAD"),
	}, "\x00")
}

func (w *wtEnv) recordPath(name string) string {
	return filepath.Join(w.ws, ".git", "ccnavi-direct", name)
}

// record は取り込みの記録（`キー 値` の行）。
func (w *wtEnv) record(name string) map[string]string {
	out := map[string]string{}
	for _, line := range strings.Split(shkit.Read(w.t, w.recordPath(name)), "\n") {
		if k, v, found := strings.Cut(line, " "); found {
			out[k] = v
		}
	}
	return out
}

var (
	acceptCode = regexp.MustCompile(`--accept ([0-9a-f]{8})`)
	// 名前とオプションの間に空白がある（つながると別の名前になる）。
	acceptForm = regexp.MustCompile(`ccnavi-worktree\.sh'? '?\w+'? --`)
)

// codeOf は報告の確認コード。
func codeOf(t testing.TB, r run.Result) string {
	t.Helper()
	m := acceptCode.FindStringSubmatch(r.Stdout)
	if m == nil {
		t.Fatalf("--accept の確認コードが無い: %s", r.Stdout)
	}
	if !acceptForm.MatchString(r.Stdout) {
		t.Errorf("名前とオプションの間に空白が無い: %s", r.Stdout)
	}
	return m[1]
}

// makeFeatureWithOrigin は統合先でないブランチへ移し、origin にも同じ位置で置く（C5 だけにヒットする）。
func (w *wtEnv) makeFeatureWithOrigin() {
	w.t.Helper()
	git(w.t, w.ws, "checkout", "-q", "-b", "feature/x")
	git(w.t, w.ws, "push", "-q", w.bare, "feature/x:refs/heads/feature/x")
	git(w.t, w.ws, "update-ref", "refs/remotes/origin/feature/x", "HEAD")
}

// dirtyRoot は追跡済みの変更 1・新規 1・staged 1 の書きかけ。
func (w *wtEnv) dirtyRoot() {
	w.t.Helper()
	shkit.Write(w.t, filepath.Join(w.ws, "tracked.txt"), "v1\n")
	git(w.t, w.ws, "add", "tracked.txt")
	git(w.t, w.ws, "commit", "-q", "-m", "tracked")
	git(w.t, w.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
	git(w.t, w.ws, "push", "-q", w.bare, "main")
	shkit.Write(w.t, filepath.Join(w.ws, "tracked.txt"), "v2 書きかけ\n")
	shkit.Write(w.t, filepath.Join(w.ws, "fresh.txt"), "新規\n")
	shkit.Write(w.t, filepath.Join(w.ws, "staged.txt"), "staged\n")
	git(w.t, w.ws, "add", "staged.txt")
}

// parentTree は実際の親のワークツリー（別ブランチ）を切り、置き場にチケットを置いて、そのパスを返す。
// places は 置き場の相対パス → 本文の frontmatter（ticket 以外の欄）。
func (w *wtEnv) parentTree(places map[string]map[string]string) string {
	w.t.Helper()
	path := filepath.Join(w.ws, ".claude", "worktrees", parentName)
	git(w.t, w.ws, "worktree", "add", "-q", "-b", parentName, path)
	for rel, extra := range places {
		ticket := parentName
		if v, found := extra["ticket"]; found {
			ticket = v
		}
		text := "---\nticket: " + ticket + "\n"
		for k, v := range extra {
			if k != "ticket" {
				text += k + ": " + v + "\n"
			}
		}
		shkit.Write(w.t, filepath.Join(path, filepath.FromSlash(rel)), text+"---\n本文\n")
	}
	return path
}

// addProject はワークスペースの git が無視する projects/<名前> に、独立したリポジトリを置く。
func (w *wtEnv) addProject(name string) string {
	w.t.Helper()
	appendTo(w.t, filepath.Join(w.ws, ".git", "info", "exclude"), "projects/\n")
	project := filepath.Join(w.ws, "projects", name)
	git(w.t, w.dir, "init", "-q", "-b", "work", project)
	shkit.Identity(w.t, project)
	git(w.t, project, "commit", "-q", "--allow-empty", "-m", "seed")
	git(w.t, project, "update-ref", "refs/remotes/origin/work", "HEAD")
	git(w.t, project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/work")
	return project
}

// uncordedWorktree は記録の無い直接作業のワークツリー（手で切ったもの）。
func (w *wtEnv) uncordedWorktree(name string, commits int) {
	w.t.Helper()
	git(w.t, w.ws, "worktree", "add", "-q", w.wt(name), "-b", name)
	for i := 0; i < commits; i++ {
		n := itoa(i)
		shkit.Write(w.t, filepath.Join(w.wt(name), "w"+n+".txt"), "w"+n+"\n")
		git(w.t, w.wt(name), "add", "-A")
		git(w.t, w.wt(name), "commit", "-q", "-m", "work "+n)
	}
}

// commitIn は dir に file を書いてコミットする。
func commitIn(t testing.TB, dir, file, text, message string) {
	t.Helper()
	shkit.Write(t, filepath.Join(dir, file), text)
	git(t, dir, "add", "-A")
	git(t, dir, "commit", "-q", "-m", message)
}

func (w *wtEnv) hasRef(ref string) bool {
	return gitOK(w.t, w.ws, "show-ref", "--verify", "--quiet", ref)
}

func lineCount(s, sub string) int { return strings.Count(s, sub) }

// ---- ずれが無いとき

func TestWorktreeARemovedFirstWorktreeIsNotNamed(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	code(t, w.sh("first"), 0)
	shkit.Remove(t, w.wt("first"))
	code(t, w.sh("second"), 0)
}

// 親のワークツリーの名前（ws）の提案が承認待ちで、MR もある。直接作業へ切り替えても取り下げられないので、手順を 案内: で出す。
func TestWorktreeAParentTreeIsToldHowToWithdrawWhatStaysAfterTheSwitch(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	shkit.Write(t, filepath.Join(w.ws, "wip", "proposals", "todo", "ws.md"), "---\nticket: ws\nissue: 5\nmr: 12\n---\n本文\n")
	git(t, w.ws, "add", "wip")
	git(t, w.ws, "commit", "-q", "-m", "proposal")
	git(t, w.ws, "push", "-q", w.bare, "main")
	git(t, w.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
	r := code(t, w.sh("topic"), 0)
	has(t, r.Stdout, "案内: 親 ws の承認待ちの提案・MR は", "案内: 承認待ちの提案 ws が", "案内: MR #12 が残っている")
	lacks(t, r.Stdout, "着手済みの子が")
	eq(t, lineCount(r.Stdout, "\n次: "), 1, "次: の行の数")
	truth(t, strings.HasPrefix(lastLine(r.Stdout), "次: "), lastLine(r.Stdout))
}

func (w *wtEnv) assertRefused(r run.Result) {
	w.t.Helper()
	code(w.t, r, 1)
	has(w.t, r.Stderr, "直接作業へは切り替えられない")
	eq(w.t, lineCount(r.Stdout, "\n次: ")+lineCount(r.Stderr, "\n次: "), 1, "次: の行の数")
	truth(w.t, strings.HasPrefix(lastLine(r.Stderr), "次: "), lastLine(r.Stderr))
	truth(w.t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
	eq(w.t, git(w.t, w.ws, "for-each-ref", "refs/ccnavi"), "", "refs/ccnavi")
	eq(w.t, git(w.t, w.ws, "branch", "--list", "topic"), "", "ブランチ topic")
}

func TestWorktreeAnApprovedParentTreeMayNotSwitch(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	path := w.parentTree(map[string]map[string]string{".ccnavi/approved/doing/p0001.md": {"mr": "12"}})
	w.assertRefused(w.shIn(path, nil, "topic", "--dry-run"))
	r := w.shIn(path, nil, "topic")
	w.assertRefused(r)
	has(t, r.Stderr, "案内: 親を早めにクローズしたいなら", "ccnavi-user-close-early.sh",
		// 断る文は、報告して待つことを言う。
		"親のワークツリー（p0001）から", "次: 状況をユーザに報告し")
}

// --side-by-side だけでは確認を求めない（確認は ask のルールの仕事）。記録は v4 で、実行後チェックの読み取りが有効と読む。
func TestWorktreeTheRecordIsWrittenAndReadAsValidByThePostCheck(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	r := code(t, w.sh("topic", "--side-by-side"), 0)
	rec := w.record("topic")
	head := git(t, w.ws, "rev-parse", "HEAD")
	eq(t, rec["version"], "4", "version")
	eq(t, rec["base_branch"], "main", "base_branch")
	eq(t, rec["base_sha"], head, "base_sha")
	eq(t, rec["snap_sha"], head, "snap_sha")
	eq(t, rec["snap_wip"], "no", "snap_wip")
	eq(t, rec["side_by_side"], "yes", "side_by_side")
	eq(t, rec["confirm"], "-", "confirm")
	eq(t, git(t, w.ws, "rev-parse", "refs/ccnavi/direct/topic/s"), head, "S の ref")
	truth(t, !w.hasRef("refs/ccnavi/direct/topic/pending"), "pending が残っている")
	has(t, r.Stdout, "横で作業する: はい")
	// 書き手の出力を、実行ファイル（lint の直接作業の検査）の読み取りが有効と読む。
	lint := run.Ccnavi(t, []string{"--root", w.ws, "--lint"}, run.Opts{Dir: w.ws})
	has(t, out(lint), "横で作業する指定が有効です", "/topic)")
	lacks(t, out(lint), "壊れています")
}

// projects/app の取り込み先は、そのユーザのチェックアウトのブランチ（work）。デフォルトブランチ（origin/HEAD の trunk）と違うので、確認が要る。
func TestWorktreeAProjectIsCutFromItsOwnMainCheckoutBranch(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	project := filepath.Join(w.ws, "projects", "app")
	git(t, w.dir, "init", "-q", "-b", "work", project)
	shkit.Identity(t, project)
	git(t, project, "commit", "-q", "--allow-empty", "-m", "seed")
	git(t, project, "update-ref", "refs/remotes/origin/trunk", "HEAD")
	git(t, project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
	r := code(t, w.shIn(project, nil, "topic", "--dry-run"), 0)
	has(t, r.Stdout, "取り込み先: work（", "リモートのデフォルトは trunk", "デフォルトブランチ（trunk）でない")
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
}

// ---- projects/ の変更をワークスペースルートから頼まれたときの案内

func TestWorktreeACutFromTheRootWithProjectsPointsAtCd(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.addProject("app")
	r := code(t, w.sh("topic"), 0)
	has(t, r.Stdout, projectsHint)
	eq(t, lineCount(r.Stdout, "案内: projects/ の下"), 1, "案内の数")
	truth(t, shkit.IsDir(w.wt("topic")), "ワークツリーが無い")
	// 案内は次の一手より前。
	hint, step := -1, -1
	for i, line := range strings.Split(r.Stdout, "\n") {
		if hint < 0 && strings.Contains(line, projectsHint) {
			hint = i
		}
		if step < 0 && strings.HasPrefix(line, "次: ") {
			step = i
		}
	}
	truth(t, hint >= 0 && step >= 0 && hint < step, "案内が次の一手より前に無い")
}

func TestWorktreeADirectoryInProjectsWithoutARepositoryGetsNoHint(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	shkit.Mkdir(t, filepath.Join(w.ws, "projects", "notes"))
	lacks(t, code(t, w.sh("topic"), 0).Stdout, projectsHint)
}

func TestWorktreeACutFromInsideTheProjectGetsNoHint(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	project := w.addProject("app")
	first := code(t, w.shIn(project, nil, "topic"), 3)
	done := code(t, w.shIn(project, nil, "topic", "--accept", codeOf(t, first)), 0)
	lacks(t, done.Stdout, projectsHint)
}

// --dry-run は projects/ の案内を出さず、名前の警告は出し、何も切らない。
func TestWorktreeTheHintIsNotPrintedByDryRun(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.addProject("app")
	r := code(t, w.sh("topic", "--dry-run"), 0)
	lacks(t, r.Stdout, projectsHint)
	has(t, r.Stdout, "warn: 直接作業のワークツリー名 topic が")
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
}

func TestWorktreeANameInTheFormIsNotWarned(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	lacks(t, code(t, w.sh("feature-12-topic"), 0).Stdout, "warn:")
}

// ずれが無いときの通し。確認なしで取り込み先の HEAD から切り、2 回目は切らずに既にあると言う。
// ワークスペースの名前が空白と ' を含んでも、案内のコマンドが打てる。
func TestWorktreeTheLastLineIsTheNextStepWithOneLineCommands(t *testing.T) {
	t.Parallel()
	for name, wsName := range map[string]string{"plain": "ws", "quoted": "a b's"} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			w := newWorktree(t, wsName)
			r := code(t, w.sh("topic"), 0)
			eq(t, git(t, w.ws, "rev-parse", "main"), w.headOf("topic"), "切った HEAD")
			eq(t, git(t, w.wt("topic"), "branch", "--show-current"), "topic", "ブランチ")
			has(t, r.Stdout, "取り込み先: main（", "書きかけは含まない", "横で作業する: いいえ")
			eq(t, w.record("topic")["side_by_side"], "no", "side_by_side")
			truth(t, !gitOK(t, w.ws, "rev-parse", "--abbrev-ref", "topic@{upstream}"), "取り込み先を追跡している")
			// 親が無いので残り物の話は無く、projects/ が無いので projects/ の案内も無い。
			lacks(t, r.Stdout, "取り下げ", "残っている", projectsHint, "他にある")
			// 名前の形でないので警告し、それでも切る。
			has(t, r.Stdout, "warn: 直接作業のワークツリー名 topic が")
			last := lastLine(r.Stdout)
			truth(t, strings.HasPrefix(last, "次: 編集はこのワークツリーの下で行います"), last)
			has(t, last, "Write / Edit に渡すパス: "+shkit.Slash(w.wt("topic"))+"/…", "が済んでから integrate を打ちます")
			// 名前の警告よりも後ろ。
			truth(t, strings.LastIndex(r.Stdout, "次:") > strings.Index(r.Stdout, "warn:"), "警告が次の一手より後ろ")
			// 案内の git は、書いたとおりに新しいシェルで打てる。
			m := regexp.MustCompile("`(cd .+? <サブコマンド>)`").FindStringSubmatch(last)
			if m == nil {
				t.Fatalf("案内の git が無い: %s", last)
			}
			command := strings.Replace(m[1], "<サブコマンド>", "rev-parse --abbrev-ref HEAD", 1)
			// ラッパーは引数の形を実行ファイルに確かめさせる（利用先では settings.json の env が渡す）。
			shell := run.Command(t, "sh", []string{"-c", command}, run.Opts{Dir: "/", Env: map[string]string{"CCNAVI_ENTRY_PATH": w.exe}})
			eq(t, lastLine(shell.Stdout), "topic", "案内の git の答え ("+shell.Stderr+")")
			// 既にあるときは切り直さず（中の物は残る）、記録があると言う。最後の行は同じ。
			shkit.Write(t, filepath.Join(w.wt("topic"), "keep.txt"), "mine")
			again := code(t, w.sh("topic", "--no-wip"), 0)
			has(t, again.Stdout, "ワークツリーは既にある", "取り込みの記録は、ある（ccnavi-direct/topic）")
			lacks(t, again.Stdout, "--adopt")
			truth(t, shkit.Exists(filepath.Join(w.wt("topic"), "keep.txt")), "中のファイルが消えた")
			truth(t, strings.HasPrefix(lastLine(again.Stdout), "次: 編集は"), lastLine(again.Stdout))
			// 2 本目は切れて、1 本目を名指しする。
			second := code(t, w.sh("second"), 0)
			truth(t, shkit.IsDir(w.wt("second")), "2 本目が無い")
			has(t, second.Stdout, "作業中の直接作業のワークツリーが他にある: topic")
		})
	}
}

// ---- C5: 取り込み先が統合先でない

func TestWorktreeABaseOtherThanTheDefaultBranchNeedsTheAcceptCode(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.makeFeatureWithOrigin()
	// 合わないコードは報告からやり直す。
	wrong := code(t, w.sh("topic", "--accept", "deadbeef"), 3)
	has(t, wrong.Stdout, "確認コードが今の状態と合わない")
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
	r := code(t, w.sh("topic"), 3)
	has(t, r.Stdout, "取り込み先 feature/x (", "リモートのデフォルトは main", "取り込み先がデフォルトブランチ（main）でない")
	lacks(t, r.Stdout, "書きかけ:")
	accept := codeOf(t, r)
	truth(t, !shkit.Exists(w.wt("topic")), "確認の前にワークツリーを切った")
	code(t, w.sh("topic", "--accept", accept), 0)
	eq(t, w.headOf("topic"), git(t, w.ws, "rev-parse", "feature/x"), "切った HEAD")
	eq(t, w.record("topic")["confirm"], accept, "confirm")
	eq(t, w.record("topic")["base_branch"], "feature/x", "base_branch")
}

func TestWorktreeTheCodeChangesWhenTheStateMoves(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.makeFeatureWithOrigin()
	accept := codeOf(t, w.sh("topic"))
	commitIn(t, w.ws, "more.txt", "m", "moved")
	git(t, w.ws, "update-ref", "refs/remotes/origin/feature/x", "HEAD")
	git(t, w.ws, "push", "-q", w.bare, "feature/x")
	r := code(t, w.sh("topic", "--accept", accept), 3)
	has(t, r.Stdout, "合わない")
}

// ---- C6: origin/<取り込み先> との関係

func TestWorktreeFetchFailureIsAnUnverifiedRemoteAndAsksForTheAcceptCode(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	r := code(t, w.shEnv(failFetch, "topic"), 3)
	truth(t, !shkit.Exists(w.wt("topic")), "切らずに止まる")
	has(t, r.Stdout, "リモート未確認")
	has(t, r.Stderr, "Could not resolve host") // 原因の出力を見せる
	done := code(t, w.shEnv(failFetch, "topic", "--accept", codeOf(t, r)), 0)
	eq(t, w.headOf("topic"), git(t, w.ws, "rev-parse", "main"), "切った HEAD")
	has(t, done.Stdout, "リモート未確認で切った", "ユーザにも伝える")
}

func TestWorktreeAnUnreadableBranchListOfOriginIsAnUnverifiedRemote(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	lsFail := map[string]string{"FAKE_LS_REMOTE_FAIL": "1"}
	r := code(t, w.shEnv(lsFail, "topic"), 3)
	has(t, r.Stdout, "リモート未確認")
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
	code(t, w.shEnv(lsFail, "topic", "--accept", codeOf(t, r)), 0)
}

// ---- C7: 書きかけ

func TestWorktreeCopyWipTakesTheWipAtTheTimeOfConfirmation(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.dirtyRoot()
	accept := codeOf(t, w.sh("topic"))
	// 候補は確認の時点で pending に置かれる。ユーザの index とワークツリーは変わらない。
	pending := git(t, w.ws, "rev-parse", "refs/ccnavi/direct/topic/pending")
	before := w.snapshotOfRoot()
	done := code(t, w.sh("topic", "--accept", accept, "--copy-wip"), 0)
	eq(t, w.headOf("topic"), pending, "切った HEAD")
	eq(t, git(t, w.ws, "rev-parse", "refs/ccnavi/direct/topic/s"), pending, "S の ref")
	eq(t, git(t, w.ws, "rev-parse", pending+"^"), git(t, w.ws, "rev-parse", "HEAD"), "候補の親")
	for rel, want := range map[string]string{"tracked.txt": "v2 書きかけ\n", "fresh.txt": "新規\n", "staged.txt": "staged\n"} {
		eq(t, shkit.Read(t, filepath.Join(w.wt("topic"), rel)), want, rel)
	}
	eq(t, w.record("topic")["snap_wip"], "yes", "snap_wip")
	eq(t, w.record("topic")["snap_sha"], pending, "snap_sha")
	eq(t, w.snapshotOfRoot(), before, "ユーザの index とワークツリー")
	truth(t, !w.hasRef("refs/ccnavi/direct/topic/pending"), "切ったのに pending が残っている")
	has(t, done.Stdout, "確認した時点の書きかけを含む")
}

// 置き場が .gitignore 済みでも、書きかけを含むスナップショットを作れる。
func (w *wtEnv) leaveWorktreesIgnored() {
	w.t.Helper()
	shkit.Write(w.t, filepath.Join(w.ws, ".gitignore"), "logs/\n.ccnavi/\n/.claude/worktrees/\n")
	shkit.Write(w.t, filepath.Join(w.ws, "README.md"), "# readme\n")
	git(w.t, w.ws, "add", ".gitignore", "README.md")
	git(w.t, w.ws, "commit", "-q", "-m", "ignore worktrees")
	git(w.t, w.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
	git(w.t, w.ws, "push", "-q", w.bare, "main")
	code(w.t, w.sh("first"), 0)
	truth(w.t, shkit.IsDir(w.wt("first")), "前提: 他のワークツリーが 1 本ある")
	lacks(w.t, git(w.t, w.ws, "status", "--porcelain", "--untracked-files=all"), ".claude/worktrees")
}

func TestWorktreeAFailingReadTreeStillStopsWithSnapshotFailed(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.leaveWorktreesIgnored()
	shkit.Write(t, filepath.Join(w.ws, "notes.txt"), "メモ\n")
	accept := codeOf(t, w.sh("second"))
	r := code(t, w.shEnv(map[string]string{"FAKE_READ_TREE_FAIL": "1"}, "second", "--accept", accept, "--copy-wip"), 1)
	has(t, out(r), "スナップショットを作れませんでした")
}

// ---- ルートを読むだけで書かない・終わっていない取り込みを取り違えない

func TestWorktreeReadingTheRootDoesNotRewriteItsIndex(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.dirtyRoot()
	// 中身は同じで stat だけ違う追跡済みのファイル。git status が index を書き直す元になる。
	path := filepath.Join(w.ws, ".gitignore")
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	later := info.ModTime().Add(5e9)
	if err := os.Chtimes(path, later, later); err != nil {
		t.Fatal(err)
	}
	index := filepath.Join(w.ws, ".git", "index")
	before := shkit.Read(t, index)
	w.sh("topic")
	eq(t, shkit.Read(t, index), before, "index")
}

func TestWorktreeAdoptRefusesANameBoundToATicket(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	git(t, w.ws, "update-ref", "refs/remotes/origin/topic", "HEAD")
	r := code(t, w.sh("topic", "--adopt", "--base", "main"), 1)
	has(t, r.Stderr, "チケットに結び付く")
	truth(t, !shkit.Exists(w.recordPath("topic")), "記録を書いた")
}

// ---- C1〜C3: 先に断るもの

func TestWorktreeAnUnansweredBaseBoundIsRefused(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	code(t, w.shEnv(map[string]string{"CCNAVI_ENTRY_PATH": filepath.Join(w.dir, "none")}, "topic"), 1)
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
}

func TestWorktreeGitBefore240IsRefused(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	r := code(t, w.shEnv(map[string]string{"FAKE_GIT_VERSION": "2.39.5"}, "topic"), 1)
	has(t, r.Stderr, "2.40 未満")
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
	code(t, w.shEnv(map[string]string{"FAKE_GIT_VERSION": "2.40.0"}, "topic"), 0)
}

// ---- 引数

// 引数の誤りの通し。ほかの廃止した引数・名前の形・同時に渡せない旗は cut.TestArgs が見る。
func TestWorktreeRetiredArgumentsExit2WithGuidance(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	r := code(t, w.sh("topic", "--from-origin"), 2)
	has(t, r.Stderr, "廃止しました", "--accept")
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
}

// 手で切ったワークツリーには記録が無いため、引き取る手を言う。記録があるときは、ずれが無いときの通しが見る。
func TestWorktreeExistingWorktreeReportsWhetherTheRecordExists(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	git(t, w.ws, "worktree", "add", "-q", w.wt("manual"), "-b", "manual", "main")
	r := code(t, w.sh("manual"), 0)
	has(t, r.Stdout, "取り込みの記録は、無い（ccnavi-direct/manual）", "manual' --adopt --base <取り込み先>")
	lacks(t, r.Stdout, "確認に答えられません")
	// 最後の行は、従来どおり次の一手です。
	truth(t, strings.HasPrefix(lastLine(r.Stdout), "次: 編集は"), lastLine(r.Stdout))
}

func TestWorktreeUnattendedAddsTheFixedLineOnlyWhenTheVariableIsZero(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name, value string
		shown       bool
	}{{"taken", "0", true}, {"manual", "0", true}, {"manual", "1", false}} {
		t.Run(c.name+"="+c.value, func(t *testing.T) {
			t.Parallel()
			w := newWorktree(t, "ws")
			git(t, w.ws, "branch", "taken")
			git(t, w.ws, "worktree", "add", "-q", w.wt("manual"), "-b", "manual", "main")
			r := w.shEnv(map[string]string{"CLAUDE_CODE_SESSION_ATTENDED": c.value}, c.name)
			said := out(r)
			eq(t, strings.Contains(said, "このセッションでは確認に答えられません"), c.shown, "固定の行 ("+said+")")
		})
	}
}

// origin にだけ同名のブランチがある（MR の元ブランチかもしれない）。新しく切らず ccnavi-start.sh へ案内する。
func TestWorktreeSameNameBranchOnOriginStopsAndPointsToStart(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	git(t, w.ws, "push", "-q", w.bare, "main:refs/heads/topic")
	r := code(t, w.sh("topic"), 1)
	has(t, r.Stdout, "origin にブランチ topic が既にある", "ccnavi-start.sh' --mr", "ccnavi-start.sh' --issue")
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
	// 別の名前なら切れる
	code(t, w.sh("topic2"), 0)
}

// worktree add が通らなければ、記録と S の ref を残さない。
func TestWorktreeARefusedWorktreeAddRemovesTheRecordAndTheRef(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	// 行き先の親をファイルにして worktree add を失敗させる。
	shkit.Remove(t, filepath.Join(w.ws, ".claude", "worktrees"))
	shkit.Write(t, filepath.Join(w.ws, ".claude", "worktrees"), "file")
	r := w.sh("topic")
	truth(t, r.Code != 0, "終了コードが 0: "+r.Stdout)
	truth(t, !shkit.Exists(w.recordPath("topic")), "記録が残った")
	truth(t, !w.hasRef("refs/ccnavi/direct/topic/s"), "S の ref が残った")
}

func TestWorktreeDryRunCreatesNothing(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	r := code(t, w.shEnv(failFetch, "topic", "--dry-run"), 0)
	has(t, r.Stdout, "dry-run")
	truth(t, !shkit.Exists(w.wt("topic")), "ワークツリーを切った")
	eq(t, git(t, w.ws, "branch", "--list", "topic"), "", "ブランチ topic")
	truth(t, !shkit.Exists(filepath.Join(w.ws, ".git", "ccnavi-direct")), "記録の置き場ができた")
}

// ---- 引き取り（--adopt）

func TestWorktreeAdoptShowsTheRangeAndTheCodeFirstAndWritesNothing(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	// --dry-run と、合わないコードは何も書かない。
	dry := code(t, w.sh("topic", "--adopt", "--base", "main", "--dry-run"), 0)
	has(t, dry.Stdout, "dry-run")
	wrong := code(t, w.sh("topic", "--adopt", "--base", "main", "--accept", "deadbeef"), 3)
	has(t, wrong.Stdout, "確認コードが今の状態と合わない")
	truth(t, !shkit.Exists(w.recordPath("topic")), "記録を書いた")
	first := code(t, w.sh("topic", "--adopt", "--base", "main"), 3)
	has(t, first.Stdout, "この範囲全体をエージェントの成果として扱う", "work 0", "work 1", "w1.txt", git(t, w.ws, "rev-parse", "--short", "main"))
	codeOf(t, first)
	truth(t, !shkit.Exists(w.recordPath("topic")), "記録を書いた")
	eq(t, git(t, w.ws, "for-each-ref", "refs/ccnavi/direct/topic"), "", "refs/ccnavi/direct/topic")
}

func TestWorktreeAdoptWithTheCodeWritesTheRecordAndTheRef(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	before := w.snapshotOfRoot()
	accept := codeOf(t, w.sh("topic", "--adopt", "--base", "main"))
	done := code(t, w.sh("topic", "--adopt", "--base", "main", "--accept", accept), 0)
	baseSHA := git(t, w.ws, "rev-parse", "main")
	rec := w.record("topic")
	eq(t, rec["version"], "4", "version")
	eq(t, rec["base_branch"], "main", "base_branch")
	eq(t, rec["snap_sha"], baseSHA, "snap_sha")
	eq(t, rec["snap_wip"], "no", "snap_wip")
	eq(t, rec["side_by_side"], "no", "side_by_side")
	eq(t, rec["confirm"], "adopt", "confirm")
	eq(t, git(t, w.ws, "rev-parse", "refs/ccnavi/direct/topic/s"), baseSHA, "S の ref")
	has(t, done.Stdout, "取り込み先: main")
	eq(t, w.snapshotOfRoot(), before, "ユーザの index とワークツリー")
}

func TestWorktreeAdoptSideBySideIsOnlySetWhenGiven(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	accept := codeOf(t, w.sh("topic", "--adopt", "--base", "main"))
	code(t, w.sh("topic", "--adopt", "--base", "main", "--accept", accept, "--side-by-side"), 0)
	eq(t, w.record("topic")["side_by_side"], "yes", "side_by_side")
}

func TestWorktreeAdoptCodeChangesWhenTheBranchMoves(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	accept := codeOf(t, w.sh("topic", "--adopt", "--base", "main"))
	commitIn(t, w.wt("topic"), "more.txt", "m\n", "more")
	code(t, w.sh("topic", "--adopt", "--base", "main", "--accept", accept), 3)
	truth(t, !shkit.Exists(w.recordPath("topic")), "記録を書いた")
}

func TestWorktreeAdoptNeedsTheBaseAndItMustBeTheCurrentBase(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	none := code(t, w.sh("topic", "--adopt"), 2)
	has(t, none.Stderr, "--base")
	git(t, w.ws, "branch", "other")
	wrong := code(t, w.sh("topic", "--adopt", "--base", "other"), 1)
	has(t, wrong.Stderr, "今の取り込み先")
}

// 互いを merge して、共通の祖先が 2 つになる（交差する merge）。
func TestWorktreeAdoptRefusesWhenThereAreSeveralMergeBases(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 0)
	commitIn(t, w.ws, "x.txt", "x\n", "x on main")
	commitIn(t, w.wt("topic"), "y.txt", "y\n", "y on topic")
	topicY := git(t, w.wt("topic"), "rev-parse", "HEAD")
	mainX := git(t, w.ws, "rev-parse", "HEAD")
	git(t, w.ws, "merge", "-q", "--no-edit", topicY)
	git(t, w.wt("topic"), "merge", "-q", "--no-edit", mainX)
	eq(t, len(strings.Fields(git(t, w.ws, "merge-base", "--all", "topic", "main"))), 2, "merge-base の数")
	r := code(t, w.sh("topic", "--adopt", "--base", "main"), 1)
	has(t, r.Stderr, "merge-base が複数")
	truth(t, !shkit.Exists(w.recordPath("topic")), "記録を書いた")
}

func TestWorktreeAdoptRefusesAnAlreadyRecordedWorktreeAndAMissingOne(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	code(t, w.sh("topic"), 0)
	again := code(t, w.sh("topic", "--adopt", "--base", "main"), 1)
	has(t, again.Stderr, "記録が既にあります")
	missing := code(t, w.sh("nothing", "--adopt", "--base", "main"), 1)
	has(t, missing.Stderr, "ワークツリー")
}

func TestWorktreeAdoptRefusesAWorktreeThatOpensAnotherBranch(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	git(t, w.wt("topic"), "checkout", "-q", "-b", "elsewhere")
	code(t, w.sh("topic", "--adopt", "--base", "main"), 1)
	truth(t, !shkit.Exists(w.recordPath("topic")), "記録を書いた")
}

func TestWorktreeAdoptIsRefusedWhenTheBaseBoundCannotBeAnswered(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	code(t, w.shEnv(map[string]string{"CCNAVI_ENTRY_PATH": "/nonexistent"}, "topic", "--adopt", "--base", "main"), 1)
	truth(t, !shkit.Exists(w.recordPath("topic")), "記録を書いた")
}

func TestWorktreeIntegrateWithoutARecordPointsToAdoptAndItWorksAfterAdopting(t *testing.T) {
	t.Parallel()
	w := newWorktree(t, "ws")
	w.uncordedWorktree("topic", 2)
	gitsh := filepath.Join(w.ws, ".ccnavi", "scripts", "ccnavi-git.sh")
	integrate := func() run.Result { return runScript(t, gitsh, w.ws, w.env(nil), "integrate", "topic") }
	refused := integrate()
	truth(t, refused.Code != 0, "終了コードが 0: "+refused.Stdout)
	has(t, out(refused), "--adopt")
	accept := codeOf(t, w.sh("topic", "--adopt", "--base", "main"))
	code(t, w.sh("topic", "--adopt", "--base", "main", "--accept", accept), 0)
	code(t, integrate(), 0)
}
