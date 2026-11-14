package land

import (
	"fmt"
	"io"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/records/diaglog"
)

// Names はラッパーの文に入れる呼び方。Self は `sh <ccnavi-git.sh>`、SelfQ・WorktreeQ・TicketQ はパスを単引用符で
// 囲んだ `sh '<ccnavi-git.sh>'`・`sh '<ccnavi-worktree.sh>'`・`sh '<ccnavi-ticket.sh>'`。
type Names struct {
	Self, SelfQ, WorktreeQ, TicketQ string
}

// Input は Run の材料。
type Input struct {
	Names
	Root, Cwd string
	Conf      *config.Settings
	// Terminal は、ラッパーの標準入力が端末か（自己防衛の対象を含む取り込みはターミナルから打たせる）。
	Terminal bool
	// MaxLines は一覧を出す行数の上限（ラッパーの CCNAVI_GIT_MAX_LINES）。
	MaxLines int
}

// gitTimeout は 1 回の git の期限。コミットのフックや大きな木の計算を待つので長くとる。
const gitTimeout = 10 * time.Minute

// exit は、処理を途中で終えるための終了コード。panic で運び、Run が受ける。
type exit int

// run は 1 回の取り込みの状態。
type run struct {
	Input
	stdout, stderr io.Writer
	log            *diaglog.Logger
	sub            string

	name, mark, wt, gdir, top, common, main, repo, rec, doneFile, ref, tmp, head string
	// did は integrate-done が済ませたこと（止めたときに言う）。
	did string
	// draftWhy・draftNotes は下書きの条件の結果。tmpWhy は一時ワークツリーを消さなかった理由。
	draftWhy, draftNotes, tmpWhy, refsFailed string
}

// Run は `integrate <名前>`・`integrate --abort <名前>`・`integrate-done <名前> [-m <メッセージ>]`（args の先頭が
// サブコマンド）を行い、終了コードを返す。0 は済んだ、1 は止めた（理由と次の一手を出した）、2 は拒否。
func Run(in Input, stdout, stderr io.Writer, args []string) (code int) {
	r := &run{Input: in, stdout: stdout, stderr: stderr, log: diaglog.Get("ccnavi-git", in.Root)}
	if r.MaxLines <= 0 {
		r.MaxLines = 40
	}
	defer func() {
		got := recover()
		if e, ok := got.(exit); ok {
			code = int(e)
			return
		}
		if got != nil {
			panic(got)
		}
	}()
	r.sub, args = args[0], args[1:]
	switch {
	case r.sub == "integrate" && len(args) > 0 && args[0] == "--abort":
		r.abort(args[1:])
	case r.sub == "integrate":
		r.integrate(args)
	default:
		r.done(args)
	}
	return config.ExitOK
}

// reject は拒否する（標準エラーに `ccnavi-git: <文>`、診断ログに識別子、終了コード 2）。
func (r *run) reject(id, message string) {
	fmt.Fprintf(r.stderr, "ccnavi-git: %s\n", message)
	r.log.Info("拒否した", "sub", r.sub, "reason", id)
	panic(exit(2))
}

// finish は終了コード code で終える。
func (r *run) finish(code int) {
	panic(exit(code))
}

// say は標準出力に 1 行出す。
func (r *run) say(format string, a ...any) {
	fmt.Fprintf(r.stdout, format+"\n", a...)
}

// print は文面ファイルから引いた文（texts.Say）を 1 行として出す。
func (r *run) print(text string) {
	fmt.Fprintln(r.stdout, text)
}

// list は上限まで、行の頭に 2 つの空白を付けて出す。
func (r *run) list(lines []string) {
	for i, line := range lines {
		if i >= r.MaxLines {
			break
		}
		r.say("  %s", line)
	}
}

// git は dir で git を起こし、標準出力（末尾の改行を落とす）と、0 で終わったかを返す。
func (r *run) git(dir string, args ...string) (string, bool) {
	done := git.Run(dir, args, git.Options{Timeout: gitTimeout})
	return strings.TrimRight(done.Out, "\n"), done.OK()
}

// gitDone は dir で git を起こし、終わり方をそのまま返す（索引のファイルを指定できる）。
func (r *run) gitDone(dir, index string, args ...string) git.Done {
	return git.Run(dir, args, git.Options{Timeout: gitTimeout, IndexFile: index, RawPaths: true})
}

// out は cwd で git を起こした標準出力（失敗なら空）。
func (r *run) out(args ...string) string {
	out, _ := r.git(r.Cwd, args...)
	return out
}

// ok は cwd で起こした git が 0 で終わったか。
func (r *run) ok(args ...string) bool {
	_, ok := r.git(r.Cwd, args...)
	return ok
}

// verify は rev が指すオブジェクト（`rev-parse --verify --quiet`）。無ければ空。
func (r *run) verify(rev string) string {
	return r.out("rev-parse", "--verify", "--quiet", rev)
}

// hasBranch は refs/heads/<名前> があるか。
func (r *run) hasBranch() bool {
	return r.ok("show-ref", "--verify", "--quiet", "refs/heads/"+r.name)
}

// short は sha の短い表記。引けなければそのまま。
func (r *run) short(sha string) string {
	if s := r.out("rev-parse", "--short", sha); s != "" {
		return s
	}
	return sha
}

// isAncestor は a が b の祖先か。
func (r *run) isAncestor(a, b string) bool {
	return r.ok("merge-base", "--is-ancestor", a, b)
}

// names は 2 つの木の間で変わるパス（`diff-tree -r -z --no-renames --name-only`）。
func (r *run) names(args ...string) []string {
	done := r.gitDone(r.Cwd, "", append([]string{"diff-tree", "-r", "-z", "--no-renames", "--name-only"}, args...)...)
	return splitZ(done.Out)
}

// splitZ は NUL 区切りの並び（空は落とす）。
func splitZ(out string) []string {
	var paths []string
	for _, p := range strings.Split(out, "\x00") {
		if p != "" {
			paths = append(paths, p)
		}
	}
	return paths
}

// exists は、パスがあるか（リンクはたどらない）。
func exists(path string) bool {
	return ospath.DiskLexists(path)
}

// record は「キー 値」の行の記録を読む（sh の ccnavi_record_get と同じ）。
func record(path string) map[string]string {
	if !ospath.IsFile(path) {
		return map[string]string{}
	}
	return readRecord(path)
}

// writeRecord は記録を書き直す（同じディレクトリの一時ファイルに書いてから置き換える）。値の改行は空白にする。
func writeRecord(path string, kv ...string) bool {
	var b strings.Builder
	for i := 0; i+1 < len(kv); i += 2 {
		b.WriteString(kv[i] + " " + strings.NewReplacer("\r", " ", "\n", " ").Replace(kv[i+1]) + "\n")
	}
	return fsio.ReplaceFile(path, []byte(b.String()), ".tmp") == ""
}

// now は記録に残す時刻（秒）。
func now() string {
	return fmt.Sprint(time.Now().Unix())
}
