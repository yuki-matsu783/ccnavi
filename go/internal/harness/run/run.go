package run

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/pathcase"
)

const (
	rootOption = "--root"
	claudeDir  = ".claude"
)

const worktreesDir = "worktrees"

// Opts は 1 回の起動の入力。
type Opts struct {
	// Input は標準入力。
	Input string
	// Env は「その起動に見える環境そのもの」。nil ならテストのプロセスの環境をそのまま渡す。渡したマップに無い変数は見えない。
	// 例外は走った機械の git の設定を締め出す分（base.GitEnv）と、学びの蓄積を切る CCNAVI_LEARNING=disable で、空のマップでも足す。
	// 呼び手が同じ名前を渡したときは呼び手を優先する（締め出し方そのものを試すテストのため）。
	Env map[string]string
	// Dir は作業ディレクトリ。空ならテストのプロセスのもの。
	Dir string
	// NoMessages は、起こす前に文面ファイルを置かないとき（`.ccnavi/` の無い木そのものを試すテスト）に立てる。
	NoMessages bool
}

// Result は起こしたプロセスの結果。標準出力・標準エラーは CRLF を LF に揃えてある（Windows でも同じ文字列で比べるため）。
type Result struct {
	Code           int
	Stdout, Stderr string
}

// Ccnavi は `ccnavi <args…>` を 1 回起こす。
//
// 起こす前に、実行ファイルが使うワークスペースルートに文面ファイル（`.ccnavi/messages/`）が無ければ、このリポジトリの文面を
// コピーする。文を見るテストの大半は文面を気にしない使い捨ての木を組むので、木ごとにコピーさせない。文面ファイルを置いた木
// （壊れた文面を試す木を含む）と、このリポジトリの中の木（`tests/fixtures` など）には触らない。木が git のリポジトリなら、
// コピーした文面を `.git/info/exclude` に書いて git から見えなくする（未追跡の保護対象として、判定や報告に混ざらないように）。
func Ccnavi(t testing.TB, args []string, opts ...Opts) Result {
	t.Helper()
	o := first(opts)
	environ := environment(o.Env)
	if !o.NoMessages {
		putMessagesWhenAbsent(t, rootOf(args, environ, o.Dir))
	}
	return start(t, exe.Path(t), args, o, environ)
}

// Sh は `sh <script> <args…>` を 1 回起こす。script が相対名なら `.ccnavi/scripts/` のできあがりの姿（base.ScriptsDir）の下の名前。
// 環境の決まりは Ccnavi と同じ。文面ファイルは置かない（sh のテストは自分で木を組む）。
func Sh(t testing.TB, script string, args []string, opts ...Opts) Result {
	t.Helper()
	o := first(opts)
	if !filepath.IsAbs(script) {
		script = filepath.Join(base.ScriptsDir(), script)
	}
	return start(t, "sh", append([]string{script}, args...), o, environment(o.Env))
}

func first(opts []Opts) Opts {
	if len(opts) == 0 {
		return Opts{}
	}
	return opts[0]
}

// environment は子に渡す環境。呼び手の渡した環境（無ければ自分の環境）に、既定を下に敷く。
func environment(given map[string]string) []string {
	merged := map[string]string{env.Learning: "disable"}
	if given == nil {
		for _, kv := range os.Environ() {
			k, v, _ := strings.Cut(kv, "=")
			merged[k] = v
		}
	}
	for k, v := range base.GitEnv() {
		merged[k] = v
	}
	for k, v := range given {
		merged[k] = v
	}
	out := make([]string, 0, len(merged))
	for k, v := range merged {
		out = append(out, k+"="+v)
	}
	return out
}

func start(t testing.TB, program string, args []string, o Opts, environ []string) Result {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), base.ExecTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, program, args...)
	cmd.Env = environ
	cmd.Dir = o.Dir
	cmd.Stdin = strings.NewReader(o.Input)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	var exit *exec.ExitError
	if err != nil && !errors.As(err, &exit) {
		t.Fatalf("%s を起こせない: %v", program, err)
	}
	if ctx.Err() != nil {
		t.Fatalf("%s が %v で終わらない", program, base.ExecTimeout)
	}
	return Result{cmd.ProcessState.ExitCode(), lf(stdout.String()), lf(stderr.String())}
}

func lf(s string) string { return strings.ReplaceAll(s, "\r\n", "\n") }

// rootOf は実行ファイルが使うワークスペースルート。`--root`、CLAUDE_PROJECT_DIR、作業ディレクトリから上って
// `.claude` を持つ所（ワークツリーは飛ばす）の順。実行ファイルの既定のルートと同じ決め方。
func rootOf(args []string, environ []string, dir string) string {
	for i, word := range args {
		if word == rootOption && i+1 < len(args) {
			return args[i+1]
		}
		if v, ok := strings.CutPrefix(word, rootOption+"="); ok {
			return v
		}
	}
	for _, kv := range environ {
		if v, ok := strings.CutPrefix(kv, env.ClaudeProjectDir+"="); ok && v != "" {
			return v
		}
	}
	if dir == "" {
		dir, _ = os.Getwd()
	}
	dir, _ = filepath.Abs(dir)
	for {
		holder := filepath.Dir(dir)
		if isDir(filepath.Join(dir, claudeDir)) && !isWorktreeDir(dir) {
			return dir
		}
		if holder == dir {
			return ""
		}
		dir = holder
	}
}

// isWorktreeDir は `<ワークスペースルート>/.claude/worktrees/<名前>` の位置にあるディレクトリか。
func isWorktreeDir(dir string) bool {
	holder := filepath.Dir(dir)
	return pathcase.SameName(filepath.Base(holder), worktreesDir) &&
		pathcase.SameName(filepath.Base(filepath.Dir(holder)), claudeDir)
}

// SamePath は同じ場所を指す表記か。どの機械でも大文字小文字を区別しない（pathcase と同じ）。リンクは辿らない。
func SamePath(a, b string) bool {
	return pathcase.SameNorm(a, b)
}

func isDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// insideRepo はこのリポジトリ（base.Root）か、その下か。
func insideRepo(path string) bool {
	_, ok := pathcase.Under(path, base.Root())
	return ok
}

func putMessagesWhenAbsent(t testing.TB, root string) {
	t.Helper()
	// リポジトリの中（tests/fixtures など）に写すと、作業ツリーに追跡されないファイルが残る。
	if root == "" || !isDir(root) || insideRepo(root) {
		return
	}
	if _, err := os.Lstat(filepath.Join(root, ".ccnavi", "messages")); err == nil {
		return
	}
	base.PutMessages(t, root)
	if !isDir(filepath.Join(root, ".git")) {
		return
	}
	info := filepath.Join(root, ".git", "info")
	if err := os.MkdirAll(info, 0o755); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(filepath.Join(info, "exclude"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString("\n/.ccnavi/messages/\n"); err != nil {
		t.Fatal(err)
	}
}

// CleanEnv はテストのプロセスの環境から ccnavi の変数（`CCNAVI_*`）を外し、学びの蓄積を切って（CCNAVI_LEARNING=disable）、
// extra を足したもの。走らせた人の設定が答えを変えないよう、素の環境で試したいテストが Opts.Env に渡す。
func CleanEnv(extra map[string]string) map[string]string {
	out := map[string]string{env.Learning: "disable"}
	for _, kv := range os.Environ() {
		if k, v, _ := strings.Cut(kv, "="); !strings.HasPrefix(k, env.Prefix) {
			out[k] = v
		}
	}
	for k, v := range extra {
		out[k] = v
	}
	return out
}
