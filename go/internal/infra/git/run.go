package git

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// program は起こす git の名前（PATH から探す）。
const program = "git"

// 目的ごとの git の期限。期限は場所ごとに違うので
// （実行前チェックの中は短く、ユーザが打つ操作は長く）、呼び手は目的に合う名前を選ぶ。
// ワークツリーを読む hook の中の期限は StateTimeout（state.go）。
const (
	// DefaultTimeout は期限を渡されなかったときの期限。状態の操作・Stop の促し・知らせの読み取りもこれを使う。
	DefaultTimeout = 5 * time.Second
	// RiskTimeout はリスクの計測が差分と状態を読む期限。
	RiskTimeout = 10 * time.Second
	// ReadTimeout は診断と問い合わせ（status・--lint・stop-review の材料）が git から状態と中身を 1 回読む期限。
	ReadTimeout = 10 * time.Second
	// ReviewTimeout はレビューの依頼が親のワークツリーを読む期限。
	ReviewTimeout = 15 * time.Second
	// MergeTimeout は write-and-push の合流と、ワークツリーの検査（未コミット・差分）の期限。
	MergeTimeout = 20 * time.Second
	// CleanupTimeout はワークツリーの片付けと、引き継ぎが版の中身を読む期限。
	CleanupTimeout = 30 * time.Second
	// IndexTimeout は md の索引（--docs）が ls-files と check-ignore に 1 回ずつ与える期限。索引の期限があれば
	// 残りで縮めるが、IndexMinTimeout より短くはしない（0 にはしない）。
	IndexTimeout    = 10 * time.Second
	IndexMinTimeout = 100 * time.Millisecond
	// IndexStaleTimeout は SessionStart で索引を新しくしないとき、既存の索引を集めるのに git に与える期限。
	IndexStaleTimeout = time.Second
	// FrontmatterHintTimeout は frontmatter の無い md の案内が、その md が無視されているかを聞く期限。
	// 過ぎたら案内しない。
	FrontmatterHintTimeout = 2 * time.Second
	// CommitHintTimeout はコミットの後の案内（直接作業のコミットか）が git を読む時間の合計。PostToolUse を長く
	// 待たせない。
	CommitHintTimeout = 2 * time.Second
)

// ShortSHA は文に出す短い sha の桁数。
const ShortSHA = 12

// Options.Deadline の手前に残す余白（deadlineMargin）と、これ以下の残りなら起こさない下限（deadlineFloor）。
// 期限が足りるかは Expired で見る。
const (
	deadlineMargin = 500 * time.Millisecond
	deadlineFloor  = 200 * time.Millisecond
)

// killedPipeWait は期限で殺した後、出力の管が閉じるのを待つ長さ。孫のプロセスが管を握っていても戻る。
const killedPipeWait = 100 * time.Millisecond

// pathspecEnv はユーザの環境で pathspec の読み方を変える環境変数。check-ignore はこれらを受けず 128 で止まる。
var pathspecEnv = []string{env.GitLiteralPathspecs, env.GitGlobPathspecs, env.GitNoglobPathspecs, env.GitIcasePathspecs}

// rawPathsArgs は非 ASCII を含むパスを 8 進の表記に引用させない設定。パスをそのまま突き合わせる側が使う。
var rawPathsArgs = []string{"-c", "core.quotePath=false"}

// 起こせなかった理由の種類。Done.Failure を errors.Is で分ける。
var (
	errMissing = errors.New("git を起こせない")
	errTimeout = errors.New("git が期限に達した")
)

// failure は起こせなかった理由。文は OS のエラーの文（`[Errno 2] No such file or directory: 'git'` の形）にする
// （呼び手がそのまま理由としてユーザに見せる）。
type failure struct {
	text string
	kind error
}

func (f failure) Error() string        { return f.text }
func (f failure) Is(target error) bool { return f.kind != nil && target == f.kind }

// Done は git が走った結果。起こせなかったときは Failure に理由が入り、Code は 1。
type Done struct {
	Code    int
	Out     string
	Stderr  string
	Failure error
}

// OK は git が走って 0 で終わったか。
func (d Done) OK() bool { return d.Failure == nil && d.Code == 0 }

// Missing は git（か作業ディレクトリ）が見つからなかったか。
func (d Done) Missing() bool { return errors.Is(d.Failure, errMissing) }

// TimedOut は期限に達したか（Options.Deadline の期限不足も含む）。
func (d Done) TimedOut() bool { return errors.Is(d.Failure, errTimeout) }

// FirstLine は失敗の文の 1 行目。git が言った文（標準エラー）を、無ければ起こせなかった理由を採る。どちらも無ければ空。
func (d Done) FirstLine() string {
	said := d.Stderr
	if said == "" && d.Failure != nil {
		said = d.Failure.Error()
	}
	if lines := ospath.Splitlines(ospath.Strip(said)); len(lines) > 0 {
		return lines[0]
	}
	return ""
}

// Options は git の起こし方。ゼロ値は DefaultTimeout で、期限（Deadline）は無く、標準入力は親のものを渡す。
type Options struct {
	// Timeout は 1 回の期限。0 なら DefaultTimeout。
	Timeout time.Duration
	// Deadline は処理全体の期限。ゼロ値でなければ、1 回の期限を min(Timeout, 残り - deadlineMargin) に縮め、
	// 残りが足りなければ（Expired）起こさずに TimedOut の Done を返す。守るのは「期限を過ぎてから起こさない」
	// ことだけで、処理全体が期限内に終わることは約束しない。
	Deadline time.Time
	// RawPaths は core.quotePath=false を掛ける。
	RawPaths bool
	// input は標準入力に渡す文字列（check-ignore --stdin など）。nil なら親の標準入力のまま。
	input *string
	// PlainPathspecs はユーザの環境の pathspec の読み方（pathspecEnv）を外し、git の既定に戻す。
	PlainPathspecs bool
}

func (o Options) timeout() time.Duration {
	if o.Timeout <= 0 {
		return DefaultTimeout
	}
	return o.Timeout
}

// Run は git を 1 回起こし、終わり方をそのまま返す。出力は UTF-8 として読み（読めないバイトは U+FFFD）、
// 改行は "\n" にそろえる。
//
// git を起こす場所はここ 1 つにする。受け方は呼び手ごとに違っても、起こし方（引数・環境・期限・
// 起こせなかったときの捕まえ方）は同じで、別々に書くと片方だけが置いていかれる。期限は場所ごとに
// 違う（実行前チェックの中は短く、ユーザが打つ操作は長く）ので呼び手が渡す。
func Run(cwd string, args []string, o Options) Done {
	// 処理全体の期限があれば、残りに合わせて 1 回の期限を縮める。
	if !o.Deadline.IsZero() {
		if Expired(o.Deadline) {
			return Done{Code: 1, Failure: failure{text: "期限不足", kind: errTimeout}}
		}
		o.Timeout = min(o.timeout(), time.Until(o.Deadline)-deadlineMargin)
	}
	// 起こせたか（fail）と git の終わり方（code）は別に返す。起こせなかったときの Code は 1。
	code, out, errOut, fail := start(cwd, args, o)
	if fail != nil {
		return Done{Code: 1, Failure: fail}
	}
	return Done{Code: code, Out: decode(out), Stderr: decode(errOut)}
}

// runGit は state・history・restore が git を起こす口（テストが引数と期限を見るために差し替える）。
var runGit = Run

// Expired は期限までの残りが、余白を引くと下限以下か（もう git を起こさない）。ゼロ値の期限（期限無し）は偽。
func Expired(deadline time.Time) bool {
	return !deadline.IsZero() && time.Until(deadline)-deadlineMargin <= deadlineFloor
}

// Output は終了コードと標準出力。起こせなければ (1, "")。
func Output(cwd string, args []string, o Options) (int, string) {
	done := Run(cwd, args, o)
	if done.Failure != nil {
		return 1, ""
	}
	return done.Code, done.Out
}

// Text は git が 0 で終わったときの標準出力（前後の空白を除く）。走れなかった・0 以外で終わったときは ("", false)。
func Text(cwd string, args []string, o Options) (string, bool) {
	done := Run(cwd, args, o)
	if !done.OK() {
		return "", false
	}
	return ospath.Strip(done.Out), true
}

// Blob はその版に入っている 1 本の中身をバイト列のまま返す。返すのは (中身, 読めた)。
//
// 文字列で読むと UTF-8 でない中身と単独の CR が読み替えられ、中身の突き合わせが食い違う。
// その版にそのパスが無ければ (nil, true)、起こせない・期限に達した・版が無いときは (nil, false)。
func Blob(cwd, rev, path string, o Options) ([]byte, bool) {
	listed := Run(cwd, []string{"ls-tree", "-z", rev, "--", path}, o)
	if !listed.OK() {
		return nil, false
	}
	if ospath.Strip(strings.Trim(listed.Out, "\x00")) == "" {
		return nil, true
	}
	code, out, _, fail := start(cwd, []string{"cat-file", "blob", rev + ":" + path}, Options{Timeout: o.Timeout})
	if fail != nil || code != 0 {
		return nil, false
	}
	if out == nil {
		out = []byte{} // 空のファイルを「パスが無い」（nil）と見分ける
	}
	return out, true
}

func decode(b []byte) string {
	text, _ := fsio.Decode(b, fsio.Replace)
	return text
}

// start は git を起こして終わるまで待つ。起こせなかったときは failure を返す。
func start(cwd string, args []string, o Options) (int, []byte, []byte, error) {
	if fail := checkDir(cwd); fail != nil {
		return 1, nil, nil, fail
	}
	// 先頭に -c core.quotePath=false を足す。足さないと、非 ASCII のパスが "\343..." の形で返る。
	full := args
	if o.RawPaths {
		full = append(append([]string(nil), rawPathsArgs...), args...)
	}
	timeout := o.timeout()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, program, full...)
	if errors.Is(cmd.Err, exec.ErrNotFound) {
		return 1, nil, nil, startFailure(cmd.Err)
	}
	cmd.Dir = cwd
	cmd.Env = environ(o.PlainPathspecs)
	// 子プロセスが孫に出力の口を渡したまま残ると Run が返らないので、終わったあとの待ちに上限を付ける。
	cmd.WaitDelay = killedPipeWait
	if o.input != nil {
		cmd.Stdin = strings.NewReader(*o.input)
	} else {
		cmd.Stdin = os.Stdin
	}
	var out, errOut bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errOut
	err := cmd.Run()
	// 期限切れで殺されたときは終了コードを見ず、期限切れの理由にする。
	if ctx.Err() == context.DeadlineExceeded {
		return 1, nil, nil, timedOut(full, timeout)
	}
	var exit *exec.ExitError
	switch {
	// ErrWaitDelay は git 自身は終わっていて、出力の口の待ちだけが上限に達したこと。結果は有効。
	case err == nil, errors.Is(err, exec.ErrWaitDelay):
		return 0, out.Bytes(), errOut.Bytes(), nil
	case errors.As(err, &exit):
		return exit.ExitCode(), out.Bytes(), errOut.Bytes(), nil
	}
	return 1, nil, nil, startFailure(err)
}

// timedOut は期限切れの理由の文（`Command '['git', ...]' timed out after 2.0 seconds`）。
func timedOut(args []string, timeout time.Duration) error {
	command := make([]any, 0, len(args)+1)
	command = append(command, program)
	for _, a := range args {
		command = append(command, a)
	}
	text := fmt.Sprintf("Command '%s' timed out after %s seconds", pyfmt.Repr(command), pyfmt.Float(timeout.Seconds()))
	return failure{text: text, kind: errTimeout}
}

// checkDir は作業ディレクトリへ移れるかを先に見る。無い作業ディレクトリは、
// git が無いのと同じ「無い」エラーにそろえる（空の名前も無いものとして扱う）。Windows は名前が無効の 267。
func checkDir(cwd string) error {
	info, err := os.Stat(cwd)
	if err == nil && info.IsDir() {
		return nil
	}
	if ospath.Windows {
		return failure{text: fsio.ErrorText(syscall.Errno(errorDirectory))}
	}
	if err == nil {
		err = &fs.PathError{Op: "chdir", Path: cwd, Err: syscall.ENOTDIR}
	} else if cwd == "" {
		err = &fs.PathError{Op: "chdir", Path: cwd, Err: syscall.ENOENT}
	}
	var path *fs.PathError
	if errors.As(err, &path) {
		err = &fs.PathError{Op: "chdir", Path: cwd, Err: path.Err}
	}
	f := failure{text: fsio.ErrorText(err)}
	if errors.Is(err, fs.ErrNotExist) {
		f.kind = errMissing
	}
	return f
}

// errorDirectory は Windows の ERROR_DIRECTORY（作業ディレクトリの名前が無効）。
const errorDirectory = 267

// startFailure は起こせなかった理由。文には起こす名前（"git"）を付ける（Windows は付けない）。
func startFailure(err error) error {
	var errno syscall.Errno
	if !errors.As(err, &errno) {
		if errors.Is(err, exec.ErrNotFound) {
			errno = syscall.ENOENT
		} else {
			return failure{text: err.Error()}
		}
	}
	var text string
	if ospath.Windows {
		text = fsio.ErrorText(errno)
	} else {
		text = fsio.ErrorText(&fs.PathError{Op: "exec", Path: program, Err: errno})
	}
	f := failure{text: text}
	if errors.Is(errno, fs.ErrNotExist) {
		f.kind = errMissing
	}
	return f
}

// environ は親の環境から env.Repository の変数を外し、GIT_OPTIONAL_LOCKS=0 と GIT_NO_LAZY_FETCH=1 を足したもの。
// GIT_OPTIONAL_LOCKS=0 は、期限で殺したときに status が取った index.lock を残さないため（diff や restore のように
// index を書き換える操作の lock は止まらない）。GIT_NO_LAZY_FETCH=1 は、partial clone の無い blob を取りに
// ネットワークへ出ず、読めないにするため。
func environ(plainPathspecs bool) []string {
	drop := append([]string{env.GitOptionalLocks, env.GitNoLazyFetch}, env.Repository()...)
	if plainPathspecs {
		drop = append(drop, pathspecEnv...)
	}
	out := ospath.WithoutEnv(os.Environ(), drop)
	return append(out, env.GitOptionalLocks+"=0", env.GitNoLazyFetch+"=1")
}
