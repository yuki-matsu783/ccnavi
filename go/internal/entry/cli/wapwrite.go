package cli

import (
	"fmt"
	"io"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"ccnavi/internal/infra/argparse"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/lifecycle/wapmerge"
	"ccnavi/internal/tickets/model/useronly"
)

// write-and-push のローカルの書き込み（ccnavi-common-write-and-push.sh の 6〜8 と 11）。sh は push と ls-remote だけを
// 打ち、ここの答えを読む。答えは一覧（`--record-writes`）の横の `<一覧>.answer` に 1 行 1 項目で書く（標準出力は
// 状態の操作のもので、ボードが JSON を読む）。
//
//   write-and-push 1        頭の行。無ければ sh は「実行ファイルが答えなかった」と読む
//   say <文>                sh が文面の頭を付けて標準エラーへ出す
//   push yes|no             no は書いたものも送るものも無い
//   tree・branch・base・commit・head・list   戻すときに `write-and-push undo <答え>` が読む（commit は none もある）
//   keep <パス>             戻すときに触らない未コミットの記録
//   keep-worktree yes       戻すときにワークツリーを戻さない（approval-request）
//   file <blob> <パス>      送った先頭の、書いたパスの中身（approval-request の結果ファイルの申し送り）

const wapAnswerSuffix = ".answer"

// wapCommitTimeout はコミット 1 回の期限（CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT の秒。数字でなければ 60 秒）。
func wapCommitTimeout() time.Duration {
	v := os.Getenv(env.WriteAndPushCommitTimeout)
	if n, err := strconv.Atoi(v); err == nil && strings.Trim(v, "0123456789") == "" {
		return time.Duration(n) * time.Second
	}
	return 60 * time.Second
}

// wapAnswer は答えのファイルの中身。
type wapAnswer struct {
	said  wapmerge.Said
	lines []string
}

func (w *wapAnswer) say(said ...string) { w.said = append(w.said, said...) }

func (w *wapAnswer) set(key, value string) { w.lines = append(w.lines, key+" "+value) }

func (w *wapAnswer) text() string {
	var b strings.Builder
	b.WriteString(wap.Head + "\n")
	for _, s := range w.said {
		b.WriteString("say " + ospath.Squeeze(s) + "\n")
	}
	for _, l := range w.lines {
		b.WriteString(l + "\n")
	}
	return b.String()
}

// readWapAnswer は答えのファイルを `<キー>` ごとの値の並びにする。
func readWapAnswer(path string) (map[string][]string, bool) {
	text, err := fsio.ReadDiskText(path, fsio.Replace)
	if err != nil || !strings.HasPrefix(text, wap.Head+"\n") {
		return nil, false
	}
	out := map[string][]string{}
	for _, line := range strings.Split(text, "\n")[1:] {
		if k, v, ok := strings.Cut(line, " "); ok {
			out[k] = append(out[k], v)
		}
	}
	return out, true
}

func firstOf(m map[string][]string, key string) string {
	if v := m[key]; len(v) > 0 {
		return v[0]
	}
	return ""
}

func readList(path string) ([]string, bool) {
	text, err := fsio.ReadDiskText(path, fsio.Replace)
	if err != nil {
		return nil, false
	}
	var out []string
	for _, line := range strings.Split(text, "\n") {
		if line != "" {
			out = append(out, line)
		}
	}
	return out, true
}

// wapWrite は `--record-commit <文>` つきの 1 回。親のワークツリーで、元の先頭を読み、子の finish なら子の先頭を
// 合流し（`--record-merge <sha>`）、状態の操作を書き込みの記録つきで走らせ、書いたパスをコミットする。失敗したら
// この起動の中で合流ごと元に戻す。INT・TERM・HUP は受けて、書き終えてから戻して 130 で終える（途中で死なない）。
func wapWrite(in *input, stdout, stderr io.Writer, parser *argparse.Parser, a opts, unmarked *useronly.Verb) int {
	answerPath := resolvedTarget(a.Str("record_writes")) + wapAnswerSuffix
	listPath := resolvedTarget(a.Str("record_writes"))
	ans := &wapAnswer{}
	code := wapWriteSteps(in, stdout, stderr, parser, a, unmarked, ans, listPath)
	if failed := writeRecordFile(answerPath, ans.text()); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: write-and-push の答えを %s に保存できませんでした (%s)\n", answerPath, failed)
		return config.ExitError
	}
	return code
}

func wapWriteSteps(in *input, stdout, stderr io.Writer, parser *argparse.Parser, a opts, unmarked *useronly.Verb,
	ans *wapAnswer, listPath string) int {
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM, syscall.SIGHUP)
	defer signal.Stop(signals)
	stopped := func() bool {
		select {
		case <-signals:
			return true
		default:
			return false
		}
	}
	tree := ospath.Abspath(a.Str("record_tree"))
	message := a.Str("record_commit")
	// 1. 元の先頭とブランチ。戻すときの基準になる。
	base := wapmerge.Head(tree)
	branch := wapmerge.Branch(tree)
	if base == "" || branch == "" {
		ans.say("親のワークツリーの先頭を読めない。何も反映していません")
		return config.ExitError
	}
	conf, _ := config.Load(rootOf(a))
	override(conf, a)
	restore := wapmerge.Restore{Keep: map[string]bool{}, Nothing: a.head("ticket", "approval-request")}
	kept := wap.Kept(conf, tree)
	for _, k := range kept {
		restore.Keep[k] = true
	}
	// 2. 合流（子の finish）。衝突したら取りやめて、何も変えずに止める。
	var merging *wapmerge.Merging
	if sha := a.Str("record_merge"); sha != "" {
		child := ""
		if cmd := a.command(); len(cmd) >= 3 {
			child = cmd[2]
		}
		m, said, ok := wapmerge.Begin(tree, base, sha, child, branch)
		ans.say(said...)
		if !ok {
			return config.ExitError
		}
		merging = m
	}
	// 3. 書く。
	code := recordedRun(in, stdout, stderr, parser, a, unmarked)
	written, listed := readList(listPath)
	rollback := func() {
		said, _ := merging.Abort(restore, written)
		ans.say(said...)
		ans.say(restore.Written(tree, written, base)...)
		os.Remove(listPath)
	}
	if !listed {
		said, _ := merging.Abort(restore, nil)
		ans.say(said...)
		ans.say("実行ファイルが書いたパスの一覧を出さなかった。書いたものが分からないのでコミットしない。親のワークツリー（" + tree + "）をユーザが確かめてください")
		return config.ExitError
	}
	if code != config.ExitOK {
		rollback()
		return code
	}
	if stopped() {
		ans.say("送る前に止められた。書いたものを戻す")
		rollback()
		return interruptedCode
	}
	// 4. コミット。合流の途中なら、合流と状態を 1 つのマージコミットにする。
	var c wapmerge.Committed
	what := "コミットできなかった（制限時間で打ち切った場合を含む）。"
	if merging != nil && merging.Active {
		c = merging.Commit(written, message, wapCommitTimeout())
		what = "合流のコミットができなかった（制限時間で打ち切った場合を含む）。"
	} else {
		c = wapmerge.Commit(tree, written, message, wapCommitTimeout())
	}
	ans.say(c.Said...)
	if !c.OK {
		if c.Refused != "" {
			ans.say(what + c.Refused)
		}
		rollback()
		return config.ExitError
	}
	if stopped() {
		ans.say("送る前に止められた。書いたものを戻す")
		if c.Sha != "" {
			said, _ := restore.Undo(tree, branch, c.Sha, base, written)
			ans.say(said...)
		} else {
			rollback()
		}
		os.Remove(listPath)
		return interruptedCode
	}
	// 5. 送るものがあるか。書いたものが無く、先頭がリモートの参照と同じなら送らない。
	head := wapmerge.Head(tree)
	if c.Sha == "" && head == wapmerge.Remote(tree, branch) {
		os.Remove(listPath)
		ans.set("push", "no")
		return config.ExitOK
	}
	ans.set("push", "yes")
	commit := c.Sha
	if commit == "" {
		commit = "none"
	}
	for _, kv := range [][2]string{{"tree", tree}, {"branch", branch}, {"base", base}, {"commit", commit}, {"head", head}, {"list", listPath}} {
		ans.set(kv[0], kv[1])
	}
	for _, k := range kept {
		ans.set("keep", k)
	}
	if restore.Nothing {
		ans.set("keep-worktree", "yes")
	}
	for _, f := range wapmerge.Blobs(tree, head, written) {
		ans.set("file", f)
	}
	return config.ExitOK
}

// interruptedCode は信号で止めたときの終了コード（sh の trap と同じ 130）。
const interruptedCode = 130

// wapUndo は `write-and-push undo <答え>`。push が通らなかった（か送る前に止められた）回の自分のコミットを、
// 比較つきで元の先頭に戻す。答えは頭の行と `say` と、戻せたら `undone yes`（戻せなければ無く、1 で終わる）。
func wapUndo(stdout io.Writer, answerPath string) int {
	m, ok := readWapAnswer(answerPath)
	io.WriteString(stdout, wap.Head+"\n")
	if !ok || firstOf(m, "commit") == "" {
		fmt.Fprintln(stdout, "say write-and-push の答え（"+ospath.Squeeze(answerPath)+"）を読めない。戻さずに止めた。ユーザが確かめてください")
		return config.ExitError
	}
	commit := firstOf(m, "commit")
	if commit == "none" {
		fmt.Fprintln(stdout, "undone yes")
		return config.ExitOK
	}
	restore := wapmerge.Restore{Keep: map[string]bool{}, Nothing: firstOf(m, "keep-worktree") == "yes"}
	for _, k := range m["keep"] {
		restore.Keep[k] = true
	}
	written, _ := readList(firstOf(m, "list"))
	said, done := restore.Undo(firstOf(m, "tree"), firstOf(m, "branch"), commit, firstOf(m, "base"), written)
	for _, s := range said {
		fmt.Fprintln(stdout, "say "+ospath.Squeeze(s))
	}
	if done {
		fmt.Fprintln(stdout, "undone yes")
	}
	return okIf(done)
}

// wapLanded は `write-and-push landed <答え>`。取り込み直した親のワークツリーに、前の回の自分のコミットが入っていれば
// （届いていたのに確かめも失敗していた）`landed yes <先頭>`、でなければ `landed no`。
func wapLanded(stdout io.Writer, answerPath string) int {
	m, ok := readWapAnswer(answerPath)
	io.WriteString(stdout, wap.Head+"\n")
	tree, commit := firstOf(m, "tree"), firstOf(m, "commit")
	if ok && commit != "none" && wapmerge.Contains(tree, commit) {
		fmt.Fprintln(stdout, "landed yes "+wapmerge.Head(tree))
		return config.ExitOK
	}
	fmt.Fprintln(stdout, "landed no")
	return config.ExitOK
}

// wapCommit は `write-and-push commit <ツリー> <一覧> <文>`。一覧のパスだけをコミットする（hook のマーカーや、
// 承認の push の置き場）。答えは `commit <sha>|none`、git が断ったら `refused <理由>`、ほかに言うことは `say`。
func wapCommit(stdout io.Writer, tree, listPath, message string) int {
	io.WriteString(stdout, wap.Head+"\n")
	paths, ok := readList(listPath)
	if !ok {
		fmt.Fprintln(stdout, "say コミットするパスの一覧（"+ospath.Squeeze(listPath)+"）を読めない。コミットしない")
		return config.ExitError
	}
	c := wapmerge.Commit(ospath.Abspath(tree), paths, message, wapCommitTimeout())
	for _, s := range c.Said {
		fmt.Fprintln(stdout, "say "+ospath.Squeeze(s))
	}
	if !c.OK {
		if c.Refused != "" {
			fmt.Fprintln(stdout, "refused "+ospath.Squeeze(c.Refused))
		}
		return config.ExitError
	}
	sha := c.Sha
	if sha == "" {
		sha = "none"
	}
	fmt.Fprintln(stdout, "commit "+sha)
	return config.ExitOK
}

// wapBusy は `write-and-push busy <ツリー>`。途中の操作があれば `busy <名前>`。
func wapBusy(stdout io.Writer, tree string) int {
	io.WriteString(stdout, wap.Head+"\n")
	if name := wapmerge.Busy(ospath.Abspath(tree)); name != "" {
		fmt.Fprintln(stdout, "busy "+name)
	}
	return config.ExitOK
}

// wapUnsent は `write-and-push unsent <ツリー> <ブランチ>`。まだ送れていないパス（リモートの先頭との差。参照が
// 無ければ直近のコミットの分）を `unsent <パス>` で 1 行ずつ。
func wapUnsent(stdout io.Writer, tree, branch string) int {
	io.WriteString(stdout, wap.Head+"\n")
	for _, p := range wapmerge.Unsent(ospath.Abspath(tree), branch) {
		fmt.Fprintln(stdout, "unsent "+p)
	}
	return config.ExitOK
}
