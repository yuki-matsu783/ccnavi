package run

import (
	"bytes"
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
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
	// Env は、テストのプロセスの環境（base.Main が整えたもの）に足す・上書きする分。呼び手の値が既定より優先される。
	// 子の環境は、プロセスの環境 + 既定（走った機械の git の締め出し base.GitEnv と、学びの蓄積を切る CCNAVI_LEARNING=disable）
	// + Unset の除去 + Env の順に重ねる。Main がプロセスの環境から GIT_*・CLAUDE_*・CCNAVI_*・GITHUB_TOKEN・GITLAB_TOKEN を外してあるので、
	// 走らせた人の設定や、Claude Code の中で回したことは答えを変えない。
	Env map[string]string
	// Unset は、子の環境から外す名前（プロセスの環境と既定の分）。まれに「その変数が無い」姿を試すときだけ使う。
	Unset []string
	// Dir は作業ディレクトリ。空ならテストのプロセスのもの。
	Dir string
	// NoMessages は、起こす前に文面ファイルを置かないとき（`.ccnavi/` の無い木そのものを試すテスト）に立てる。
	NoMessages bool
}

// Result は起こしたプロセスの結果。標準出力・標準エラーは改行（CRLF と単独の CR）を LF に揃えてある（Windows でも同じ文字列で比べるため）。
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
	environ := Environment(o)
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
	return start(t, "sh", append([]string{script}, args...), o, Environment(o))
}

func first(opts []Opts) Opts {
	if len(opts) == 0 {
		return Opts{}
	}
	return opts[0]
}

// Environment は 1 回の起動の子に渡す環境（`NAME=value` の並び。文字列順）。
func Environment(o Opts) []string {
	defaults := base.GitEnv()
	defaults[env.Learning] = "disable"
	return compose(os.Environ(), defaults, o.Unset, o.Env, ospath.Windows)
}

// compose は環境を process・defaults・unset の除去・add の順に重ねる。
// caseless なら（Windows）名前の大文字小文字を区別せず、あとの層が前の層の同名（`Path` と `PATH`）を置き換える。
// 区別しない所で map に名前をそのまま詰めると、同じ変数が 2 つ残り、子にどちらが見えるかが実行ごとに変わる。
func compose(process []string, defaults map[string]string, unset []string, add map[string]string, caseless bool) []string {
	key := func(name string) string {
		if caseless {
			return strings.ToUpper(name)
		}
		return name
	}
	type entry struct{ name, value string }
	merged := map[string]entry{}
	for _, kv := range process {
		name, value, _ := strings.Cut(kv, "=")
		if name == "" { // Windows の `=C:=C:\` のような内部の項目
			continue
		}
		merged[key(name)] = entry{name, value}
	}
	for name, value := range defaults {
		merged[key(name)] = entry{name, value}
	}
	for _, name := range unset {
		delete(merged, key(name))
	}
	for name, value := range add {
		merged[key(name)] = entry{name, value}
	}
	out := make([]string, 0, len(merged))
	for _, e := range merged {
		out = append(out, e.name+"="+e.value)
	}
	sort.Strings(out)
	return out
}

func start(t testing.TB, program string, args []string, o Opts, environ []string) Result {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), base.ExecTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, program, args...)
	cmd.Env = environ
	// 孫のプロセスが標準出力を持ち続けても、上限のあとは待たない（待ち続けると go test 全体の上限まで固まる）。
	cmd.WaitDelay = 5 * time.Second
	base.Uncache()
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

var lfReplacer = strings.NewReplacer("\r\n", "\n", "\r", "\n")

func lf(s string) string { return lfReplacer.Replace(s) }

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
		if isDir(filepath.Join(dir, claudeDir)) && !IsWorktreeDir(dir) {
			return dir
		}
		if holder == dir {
			return ""
		}
		dir = holder
	}
}

// IsWorktreeDir は `<ワークスペースルート>/.claude/worktrees/<名前>` の位置にあるディレクトリか。
func IsWorktreeDir(dir string) bool {
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

// messagesOnce は根ごとに 1 回だけ文面を置く。表の副テストが同じ根へ並行に起こしても、書きかけの文面を子が読まない。
var messagesOnce sync.Map

func putMessagesWhenAbsent(t testing.TB, root string) {
	t.Helper()
	if root == "" {
		return
	}
	once, _ := messagesOnce.LoadOrStore(pathcase.Fold(root), new(sync.Once))
	once.(*sync.Once).Do(func() { putMessages(t, root) })
}

func putMessages(t testing.TB, root string) {
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
