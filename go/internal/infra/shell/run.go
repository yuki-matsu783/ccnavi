package shell

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"time"

	envnames "ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

// Program は外部のスクリプトを走らせるシェル（PATH から探す）。
const Program = "sh"

// 目的ごとの期限。
const (
	// ScriptTimeout はリスクの計測の script: の項目に与える時間。
	ScriptTimeout = 30 * time.Second
	// SyntaxTimeout は sh -n で文法を確かめる時間。
	SyntaxTimeout = 10 * time.Second
	// ConditionTimeout は作業の決まり（CCNAVI.md）の sh の条件 1 つに待つ時間。過ぎたら判定できないとして真。
	ConditionTimeout = 2 * time.Second
)

// killedPipeWait は期限で殺した後、出力の管が閉じるのを待つ長さ。孫のプロセスが管を握っていても戻る。
const killedPipeWait = time.Second

// ErrNoShell は sh が PATH に見つからない。
var ErrNoShell = errors.New("sh が見つからない")

// Ran は sh が走った結果。0 以外で終わったのも、期限で殺したのも、走った結果として返す。
type Ran struct {
	// Shell は探し当てた sh のパス（Python の shutil.which("sh")）。
	Shell          string
	Code           int
	Stdout, Stderr []byte
	TimedOut       bool
}

// Script は sh に args を渡して走らせ、終わるまで待つ。dir が空なら親の作業ディレクトリ、env は親の環境に足す
// "名前=値"。親の環境からは git の見るリポジトリを決める変数（env.Repository）を外す。git の hook の中から ccnavi が
// 起こされたとき、sh の中の git が dir のツリーでなくその hook のリポジトリを見ないようにするため。起こせなかったときは error で、sh が見つからなければ ErrNoShell（と exec.ErrNotFound）を包む。
func Script(args []string, dir string, env []string, timeout time.Duration) (Ran, error) {
	path, err := exec.LookPath(Program)
	if err != nil {
		return Ran{}, fmt.Errorf("%w: %w", ErrNoShell, err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, args...)
	cmd.Dir = dir
	cmd.Env = append(ospath.WithoutEnv(os.Environ(), envnames.Repository()), env...)
	cmd.WaitDelay = killedPipeWait
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err = cmd.Run()
	ran := Ran{Shell: path, Stdout: stdout.Bytes(), Stderr: stderr.Bytes()}
	if ctx.Err() != nil {
		ran.TimedOut = true
		return ran, nil
	}
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		ran.Code = exit.ExitCode()
		return ran, nil
	}
	return ran, err
}
