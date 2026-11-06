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

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// Program は起こす git の名前（PATH から探す）。
const Program = "git"

// DefaultTimeout は期限を渡されなかったときの期限。
const DefaultTimeout = 5 * time.Second

// RunUntil が期限の手前に残す余白（DeadlineMargin）と、これ以下の残りなら起こさない下限（DeadlineFloor）。
// 期限が足りるかは Expired で見る。
const (
	DeadlineMargin = 500 * time.Millisecond
	DeadlineFloor  = 200 * time.Millisecond
)

// killedPipeWait は期限で殺した後、出力の管が閉じるのを待つ長さ。孫のプロセスが管を握っていても戻る。
const killedPipeWait = 100 * time.Millisecond

// git に渡す環境変数。
const (
	// EnvOptionalLocks=0 は status が index のついでの更新に取る index.lock を止める。期限で殺すと
	// lock が残り、以後の add や commit が止まる。diff や restore のように index を書き換える操作の lock は止まらない。
	EnvOptionalLocks = "GIT_OPTIONAL_LOCKS"
	// EnvNoLazyFetch=1 は partial clone の遅延取得を止める。無い blob を取りにネットワークへ出ず、読めないにする。
	EnvNoLazyFetch = "GIT_NO_LAZY_FETCH"
)

// pathspecEnv はユーザの環境で pathspec の読み方を変える環境変数。check-ignore はこれらを受けず 128 で止まる。
var pathspecEnv = []string{
	"GIT_LITERAL_PATHSPECS",
	"GIT_GLOB_PATHSPECS",
	"GIT_NOGLOB_PATHSPECS",
	"GIT_ICASE_PATHSPECS",
}

// rawPathsArgs は非 ASCII を含むパスを 8 進の表記に引用させない設定。パスをそのまま突き合わせる側が使う。
var rawPathsArgs = []string{"-c", "core.quotePath=false"}

// 起こせなかった理由の種類。Done.Failure を errors.Is で分ける。
var (
	ErrMissing = errors.New("git を起こせない")
	ErrTimeout = errors.New("git が期限に達した")
)

// failure は起こせなかった理由。文は Python の subprocess が投げた例外の str と同じにする
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
func (d Done) Missing() bool { return errors.Is(d.Failure, ErrMissing) }

// TimedOut は期限に達したか（RunUntil の期限不足も含む）。
func (d Done) TimedOut() bool { return errors.Is(d.Failure, ErrTimeout) }

// Options は git の起こし方。ゼロ値は DefaultTimeout で、標準入力は親のものを渡す。
type Options struct {
	// Timeout は期限。0 なら DefaultTimeout。
	Timeout time.Duration
	// RawPaths は core.quotePath=false を掛ける。
	RawPaths bool
	// Input は標準入力に渡す文字列（check-ignore --stdin など）。nil なら親の標準入力のまま。
	Input *string
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
	code, out, errOut, fail := start(cwd, args, o)
	if fail != nil {
		return Done{Code: 1, Failure: fail}
	}
	return Done{Code: code, Out: decode(out), Stderr: decode(errOut)}
}

// runGit は state・history・restore が git を起こす口（テストが引数と期限を見るために差し替える）。
var runGit = Run

// RunUntil は期限（deadline）を守って git を起こす。期限は min(o の期限, 残り - 0.5 秒)。
// 残りが 0.2 秒以下なら起こさずに TimedOut の Done を返す。守るのは「期限を過ぎてから起こさない」ことだけで、
// 処理全体が期限内に終わることは約束しない。
func RunUntil(cwd string, args []string, deadline time.Time, o Options) Done {
	if Expired(deadline) {
		return Done{Code: 1, Failure: failure{text: "期限不足", kind: ErrTimeout}}
	}
	o.Timeout = min(o.timeout(), time.Until(deadline)-DeadlineMargin)
	return Run(cwd, args, o)
}

// Expired は期限までの残りが、余白を引くと下限以下か（もう git を起こさない）。
func Expired(deadline time.Time) bool {
	return time.Until(deadline)-DeadlineMargin <= DeadlineFloor
}

// Output は終了コードと標準出力。起こせなければ (1, "")。
func Output(cwd string, args []string, o Options) (int, string) {
	done := Run(cwd, args, o)
	if done.Failure != nil {
		return 1, ""
	}
	return done.Code, done.Out
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
	full := args
	if o.RawPaths {
		full = append(append([]string(nil), rawPathsArgs...), args...)
	}
	timeout := o.timeout()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, Program, full...)
	if errors.Is(cmd.Err, exec.ErrNotFound) {
		return 1, nil, nil, startFailure(cmd.Err)
	}
	cmd.Dir = cwd
	cmd.Env = environ(o.PlainPathspecs)
	cmd.WaitDelay = killedPipeWait
	if o.Input != nil {
		cmd.Stdin = strings.NewReader(*o.Input)
	} else {
		cmd.Stdin = os.Stdin
	}
	var out, errOut bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errOut
	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return 1, nil, nil, timedOut(full, timeout)
	}
	var exit *exec.ExitError
	switch {
	case err == nil, errors.Is(err, exec.ErrWaitDelay):
		return 0, out.Bytes(), errOut.Bytes(), nil
	case errors.As(err, &exit):
		return exit.ExitCode(), out.Bytes(), errOut.Bytes(), nil
	}
	return 1, nil, nil, startFailure(err)
}

// timedOut は subprocess.TimeoutExpired の文（`Command '['git', ...]' timed out after 2.0 seconds`）。
func timedOut(args []string, timeout time.Duration) error {
	command := make([]any, 0, len(args)+1)
	command = append(command, Program)
	for _, a := range args {
		command = append(command, a)
	}
	text := fmt.Sprintf("Command '%s' timed out after %s seconds", pyfmt.Repr(command), pyfmt.Float(timeout.Seconds()))
	return failure{text: text, kind: ErrTimeout}
}

// checkDir は作業ディレクトリへ移れるかを先に見る。Python の subprocess は無い作業ディレクトリを
// git が無いのと同じ FileNotFoundError で返す（空の名前も無いものになる）。Windows は名前が無効の 267。
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
		f.kind = ErrMissing
	}
	return f
}

// errorDirectory は Windows の ERROR_DIRECTORY（作業ディレクトリの名前が無効）。
const errorDirectory = 267

// startFailure は起こせなかった理由。Python は起こす名前（"git"）を付けて言う（Windows は付けない）。
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
		text = fsio.ErrorText(&fs.PathError{Op: "exec", Path: Program, Err: errno})
	}
	f := failure{text: text}
	if errors.Is(errno, fs.ErrNotExist) {
		f.kind = ErrMissing
	}
	return f
}

// environ は親の環境に EnvOptionalLocks=0 と EnvNoLazyFetch=1 を足したもの。
func environ(plainPathspecs bool) []string {
	drop := []string{EnvOptionalLocks, EnvNoLazyFetch}
	if plainPathspecs {
		drop = append(drop, pathspecEnv...)
	}
	var env []string
	for _, kv := range os.Environ() {
		name, _, _ := strings.Cut(kv, "=")
		if !hasName(drop, name) {
			env = append(env, kv)
		}
	}
	return append(env, EnvOptionalLocks+"=0", EnvNoLazyFetch+"=1")
}

// hasName は環境変数の名前が names にあるか。Windows の名前は大文字小文字を区別しない。
func hasName(names []string, name string) bool {
	for _, n := range names {
		// 環境変数の名前は OS の決まりで比べる（どの機械でも区別しない pathcase.Fold ではない）。
		if n == name || (ospath.Windows && strings.EqualFold(n, name)) {
			return true
		}
	}
	return false
}
