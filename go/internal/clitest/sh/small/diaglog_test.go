package small

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// 診断ログ（ccnavi-common-log.sh の log_*）と、sh・拡張（log.ts）・実行ファイル（records/diaglog）が同じ入力から
// 同じ行を出すこと。ccnavi-git.sh の reject と ccnavi-review.sh の fail は、拒否の文面ではなく識別子だけを残す。

// logCase は、3 つの言語に同じ入力として渡す 1 件（レベル・本文・値の組。値は渡した順に並ぶ）。
type logCase struct {
	level  string
	msg    string
	fields [][2]string
}

var logCases = []logCase{
	{"INFO", "push を拒否した", [][2]string{{"reason", "unapproved"}, {"ticket", "T-12"}}},
	{"WARN", "値の逃がし方", [][2]string{{"v", "a b"}, {"q", `say "hi"\x`}, {"eq", "k=v"}, {"tab", "a\tb"}}},
	{"ERROR", "改行を\n含む本文", [][2]string{{"n", "l1\nl2\r\nl3\rl4"}, {"empty", ""}, {"bs", `a\b`}}},
	{"INFO", "値の無い行", nil},
	{"DEBUG", "判定の材料", [][2]string{{"sub", "push"}, {"path", "/tmp/x y/z"}}},
	{"INFO", "clone https://u:p@h/x を読んだ", [][2]string{
		{"url", "https://user:glpat-SECRET@gitlab.example/x.git"},
		{"scp", "oauth2:tok@gitlab.example:org/r.git"},
		{"plain", "git@github.com:org/r.git"},
		{"said", "push to ssh://git@h:22/p failed"},
	}},
}

// expectedLine は、行の頭（時刻と pid）を除いた (レベル, 出どころ, 残り)。
type expectedLine struct{ level, name, rest string }

// expectedLines は logCases から出る行。
var expectedLines = []expectedLine{
	{"INFO ", "probe", "push を拒否した reason=unapproved ticket=T-12"},
	{"WARN ", "probe", "値の逃がし方 v=\"a b\" q=\"say \\\"hi\\\"\\\\x\" eq=\"k=v\" tab=\"a\tb\""},
	{"ERROR", "probe", `改行を\n含む本文 n="l1\nl2\nl3\nl4" empty= bs=a\b`},
	{"INFO ", "probe", "値の無い行"},
	{"DEBUG", "probe", `判定の材料 sub=push path="/tmp/x y/z"`},
	{"INFO ", "probe", "clone https://***@h/x を読んだ url=https://***@gitlab.example/x.git" +
		" scp=***@gitlab.example:org/r.git plain=git@github.com:org/r.git" +
		` said="push to ssh://***@h:22/p failed"`},
}

// logHead は行の頭（時刻・レベル・出どころ・pid）。
var logHead = regexp.MustCompile(`^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}) (DEBUG|INFO |WARN |ERROR) ([^\[\s]+)\[(\d+)\] `)

// bodyOf は行の頭を外し、(レベル, 出どころ, 残り) を返す。
func bodyOf(t *testing.T, line string) expectedLine {
	t.Helper()
	m := logHead.FindStringSubmatchIndex(line)
	if m == nil {
		t.Fatalf("行の頭の形が違う: %q", line)
	}
	return expectedLine{line[m[4]:m[5]], line[m[6]:m[7]], line[m[1]:]}
}

// shWord は sh の 1 語。CR は script に生で書かず、`$cr`（script の冒頭で作る）でつなぐ。Git Bash は生の CR を除くため。
func shWord(text string) string {
	parts := strings.Split(text, "\r")
	for i, p := range parts {
		parts[i] = shQuote(p)
	}
	return strings.Join(parts, `"$cr"`)
}

// shCall は logCase を sh の呼び出しに直す。
func shCall(c logCase) string {
	words := []string{"log_" + strings.ToLower(c.level), shWord(c.msg)}
	if len(c.fields) > 0 {
		words = append(words, "--")
		for _, f := range c.fields {
			words = append(words, shWord(f[0]+"="+f[1]))
		}
	}
	return strings.Join(words, " ")
}

// logCallsAll は logCases を 1 本の script の呼び出しにする。
func logCallsAll() string {
	lines := make([]string, 0, len(logCases))
	for _, c := range logCases {
		lines = append(lines, shCall(c))
	}
	return strings.Join(lines, "\n")
}

// probeHeader は、呼び出しを書く script の冒頭。`$0` のディレクトリの共通部を読む。
const probeHeader = "set -eu\n. \"$(dirname \"$0\")/ccnavi-common.sh\"\ncr=$(printf '\\r')\n"

// runLogScript は calls を書いた script（`.ccnavi/scripts/<name>`）を ws で起こす。
func runLogScript(t *testing.T, ws, name, calls string, extra map[string]string) run.Result {
	t.Helper()
	writeFile(t, filepath.Join(ws, ".ccnavi", "scripts", name), probeHeader+calls+"\nprintf 'done\\n'\n")
	return runProgram(t, firstShell(t), run.Opts{Dir: ws, Env: extra},
		filepath.Join(".ccnavi", "scripts", name))
}

// diagLines は ws の logs/diag/<name>.log の行。無ければ空。
func diagLines(t *testing.T, ws, name string) []string {
	t.Helper()
	return splitLines(readOptional(t, filepath.Join(ws, "logs", "diag", name+".log")))
}

// symlink はリンクを張る。張れない環境（Windows の権限など）は飛ばす。
func symlink(t *testing.T, target, link string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Skip("リンクを作れない")
	}
}

// wantDone は、script が最後まで走って done を出し、標準エラーに何も出さなかったことを確かめる。
func wantDone(t *testing.T, res run.Result) {
	t.Helper()
	if res.Code != 0 || res.Stdout != "done\n" || res.Stderr != "" {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", res.Code, res.Stdout, res.Stderr)
	}
}

func TestShLoggerWritesTheLineShape(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, calls string
		want        expectedLine
	}{
		{"値の組が並ぶ", "log_info push を拒否した -- reason=unapproved ticket=T-12",
			expectedLine{"INFO ", "probe", "push を拒否した reason=unapproved ticket=T-12"}},
		{"本文の語は空白でつながる", "log_warn 1 つ目 '2 つ目' -- a=1",
			expectedLine{"WARN ", "probe", "1 つ目 2 つ目 a=1"}},
		{"= の無い値は空の値", "log_info m -- flag",
			expectedLine{"INFO ", "probe", "m flag="}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			ws := newWorkspace(t)
			wantDone(t, runLogScript(t, ws, "probe.sh", c.calls, nil))
			lines := diagLines(t, ws, "probe")
			if len(lines) != 1 {
				t.Fatalf("行数 = %d, want 1: %q", len(lines), lines)
			}
			if got := bodyOf(t, lines[0]); got != c.want {
				t.Errorf("got %+v, want %+v", got, c.want)
			}
		})
	}
}

func TestShLoggerNamesTheLogFromTheScriptOrCCNaviLogName(t *testing.T) {
	t.Parallel()
	ws := newWorkspace(t)
	runLogScript(t, ws, "other.sh", "log_info x", nil)
	if n := len(diagLines(t, ws, "other")); n != 1 {
		t.Errorf("other.sh の行数 = %d, want 1", n)
	}
	runLogScript(t, ws, "probe.sh", "log_info x", map[string]string{"CCNAVI_LOG_NAME": "named"})
	if n := len(diagLines(t, ws, "named")); n != 1 {
		t.Errorf("CCNAVI_LOG_NAME の行数 = %d, want 1", n)
	}
}

func TestShLoggerFindsTheWorkspaceFromASubdirectory(t *testing.T) {
	t.Parallel()
	ws := newWorkspace(t)
	sub := filepath.Join(ws, "a", "b")
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(ws, ".ccnavi", "scripts", "probe.sh")
	writeFile(t, script, ". \"$(dirname \"$0\")/ccnavi-common.sh\"\nlog_info x\n")
	res := runProgram(t, firstShell(t), run.Opts{
		Dir: sub,
		Env: map[string]string{"CCNAVI_WORKSPACE": ws, env.EntryPath: exe.ShPath(t)},
	}, script)
	if res.Code != 0 {
		t.Fatalf("code = %d, stderr = %q", res.Code, res.Stderr)
	}
	if n := len(diagLines(t, ws, "probe")); n != 1 {
		t.Errorf("行数 = %d, want 1", n)
	}
}

func TestShLoggerFiltersByLevel(t *testing.T) {
	t.Parallel()
	const calls = "log_debug d\nlog_info i\nlog_warn w\nlog_error e"
	cases := []struct {
		level string
		want  []string
	}{
		{"", []string{"INFO ", "WARN ", "ERROR"}},
		{"debug", []string{"DEBUG", "INFO ", "WARN ", "ERROR"}},
		{"Warn", []string{"WARN ", "ERROR"}},
		{"loud", []string{"INFO ", "WARN ", "ERROR"}},
	}
	for _, c := range cases {
		t.Run("level="+c.level, func(t *testing.T) {
			t.Parallel()
			ws := newWorkspace(t)
			var extra map[string]string
			if c.level != "" {
				extra = map[string]string{"CCNAVI_LOG_LEVEL": c.level}
			}
			runLogScript(t, ws, "probe.sh", calls, extra)
			var got []string
			for _, line := range diagLines(t, ws, "probe") {
				got = append(got, bodyOf(t, line).level)
			}
			if fmt.Sprint(got) != fmt.Sprint(c.want) {
				t.Errorf("levels = %q, want %q", got, c.want)
			}
		})
	}
}

// 木が無ければ、ログは黙って捨てる。このリポジトリの共通部を、`$0` に入口のパスを渡して読む。
func TestShLoggerWithoutAWorkspaceDropsQuietly(t *testing.T) {
	t.Parallel()
	ws := base.TempDir(t)
	script := "set -eu\n. \"$0\"\nlog_error x\nprintf 'done\\n'\n"
	res := runProgram(t, firstShell(t), run.Opts{Dir: ws}, "-c", script, commonSh())
	if res.Code != 0 || res.Stdout != "done\n" || res.Stderr != "" {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", res.Code, res.Stdout, res.Stderr)
	}
	if _, err := os.Stat(filepath.Join(ws, "logs")); !os.IsNotExist(err) {
		t.Errorf("logs/ ができた: %v", err)
	}
}

func TestShLoggerNeverWritesToStdoutOrStderr(t *testing.T) {
	t.Parallel()
	ws := newWorkspace(t)
	res := runLogScript(t, ws, "probe.sh", logCallsAll(), map[string]string{"CCNAVI_LOG_LEVEL": "DEBUG"})
	wantDone(t, res)
	if n := len(diagLines(t, ws, "probe")); n != len(logCases) {
		t.Errorf("行数 = %d, want %d", n, len(logCases))
	}
}

func TestShLoggerMasksCredentials(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, calls, want string
	}{
		{"値の中の URL の資格情報", "log_info m -- 'reason=push to https://user:glpat-SECRET@gitlab.example/x.git failed'",
			`m reason="push to https://***@gitlab.example/x.git failed"`},
		{"本文の中の資格情報", "log_info 'clone https://u:SECRET@h/x と' 'oauth2:SECRET@h:org/r.git'",
			"clone https://***@h/x と ***@h:org/r.git"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			ws := newWorkspace(t)
			runLogScript(t, ws, "probe.sh", c.calls, nil)
			lines := diagLines(t, ws, "probe")
			if len(lines) != 1 {
				t.Fatalf("行数 = %d, want 1", len(lines))
			}
			if strings.Contains(lines[0], "SECRET") {
				t.Errorf("資格情報が残った: %q", lines[0])
			}
			if got := bodyOf(t, lines[0]).rest; got != c.want {
				t.Errorf("rest = %q, want %q", got, c.want)
			}
		})
	}
}

// 診断ログの場所にリンクが張られていても、リンク先に書かない（保護の強さ）。
func TestShLoggerDoesNotFollowALinkedLogFile(t *testing.T) {
	t.Parallel()
	ws := newWorkspace(t)
	victim := filepath.Join(ws, "logs", "decisions.jsonl")
	writeFile(t, victim, "{}\n")
	symlink(t, "../decisions.jsonl", filepath.Join(ws, "logs", "diag", "probe.log"))
	wantDone(t, runLogScript(t, ws, "probe.sh", "log_error x -- a=1", nil))
	if got := readOptional(t, victim); got != "{}\n" {
		t.Errorf("リンク先が書き換わった: %q", got)
	}
}

// logs/ や logs/diag/ がリンクでも、外へは書かない。
func TestShLoggerDoesNotFollowALinkedPlace(t *testing.T) {
	t.Parallel()
	ws := newWorkspace(t)
	outside := base.TempDir(t)
	symlink(t, outside, filepath.Join(ws, "logs", "diag"))
	runLogScript(t, ws, "probe.sh", "log_error x", nil)
	if entries, _ := os.ReadDir(outside); len(entries) != 0 {
		t.Errorf("外に書いた: %v", entries)
	}

	ws2 := newWorkspace(t)
	outside2 := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(outside2, "diag"), 0o755); err != nil {
		t.Fatal(err)
	}
	symlink(t, outside2, filepath.Join(ws2, "logs"))
	wantDone(t, runLogScript(t, ws2, "probe.sh", "log_error x", nil))
	if entries, _ := os.ReadDir(filepath.Join(outside2, "diag")); len(entries) != 0 {
		t.Errorf("外に書いた: %v", entries)
	}
}

// ログの名前に `/`・`..`・空白・`.` があれば書かない（外へ出る名前の防ぎ）。
func TestShLoggerRefusesNamesWithOtherCharacters(t *testing.T) {
	t.Parallel()
	for _, name := range []string{"../escape", "a.b", "a b", "a/b"} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			ws := newWorkspace(t)
			wantDone(t, runLogScript(t, ws, "probe.sh", "log_error x",
				map[string]string{"CCNAVI_LOG_NAME": name}))
			if _, err := os.Stat(filepath.Join(ws, "logs")); !os.IsNotExist(err) {
				t.Errorf("logs/ ができた")
			}
			if _, err := os.Stat(filepath.Join(ws, "escape.log")); !os.IsNotExist(err) {
				t.Errorf("escape.log ができた")
			}
		})
	}
}

// 新しく作るログは所有者だけが読み書きできる（POSIX の権限。Windows は見ない）。
func TestShLoggerNewFileIsOwnerOnly(t *testing.T) {
	t.Parallel()
	if runtime.GOOS == "windows" {
		t.Skip("権限のビットは POSIX だけ")
	}
	ws := newWorkspace(t)
	writeFile(t, filepath.Join(ws, ".ccnavi", "scripts", "umask.sh"),
		probeHeader+"umask 022\nlog_info x\n")
	runProgram(t, firstShell(t), run.Opts{Dir: ws}, filepath.Join(".ccnavi", "scripts", "umask.sh"))
	info, err := os.Stat(filepath.Join(ws, "logs", "diag", "umask.log"))
	if err != nil {
		t.Fatal(err)
	}
	if mode := info.Mode().Perm(); mode != 0o600 {
		t.Errorf("mode = %o, want 600", mode)
	}
}

func TestShLoggerWritesToTheGivenRoot(t *testing.T) {
	t.Parallel()
	ws := newWorkspace(t)
	other := base.TempDir(t)
	runLogScript(t, ws, "probe.sh", "ccnavi_log_root="+shQuote(other)+"\nlog_info x", nil)
	if _, err := os.Stat(filepath.Join(other, "logs", "diag", "probe.log")); err != nil {
		t.Errorf("渡したルートに書かなかった: %v", err)
	}
	if n := len(diagLines(t, ws, "probe")); n != 0 {
		t.Errorf("ワークスペースにも書いた: %d 行", n)
	}
}

// 実際の sh が出す行が、決めた形そのものであること（揃うだけでなく）。
func TestShLinesAreTheExpectedLines(t *testing.T) {
	t.Parallel()
	ws := newWorkspace(t)
	res := runLogScript(t, ws, "probe.sh", logCallsAll(), map[string]string{"CCNAVI_LOG_LEVEL": "DEBUG"})
	if res.Stderr != "" {
		t.Fatalf("標準エラーに出た: %q", res.Stderr)
	}
	checkLines(t, diagLines(t, ws, "probe"))
}

// node（拡張の log.ts を型を取り除いて読む）が、同じ入力から同じ行を出すこと。node が無ければ飛ばす。
func TestNodeWritesTheSameLines(t *testing.T) {
	t.Parallel()
	node, err := exec.LookPath("node")
	if err != nil {
		t.Skip("node が見つからない")
	}
	root := base.TempDir(t)
	logTS := filepath.Join(base.Root(), "extensions", "vscode", "ccnavi-board", "src", "log.ts")
	// 各件は [レベル, 本文, [[名前, 値], …]] の並び（値の順を保つ）。
	cases := make([][]any, 0, len(logCases))
	for _, c := range logCases {
		pairs := append([][2]string{}, c.fields...)
		cases = append(cases, []any{c.level, c.msg, pairs})
	}
	driver := "import * as log from " + jsonString(t, fileURL(logTS)) + ";\n" +
		"const logger = log.get(\"probe\", " + jsonString(t, root) + ");\n" +
		"const cases = " + jsonString(t, cases) + ";\n" +
		"for (const [level, msg, pairs] of cases) {\n" +
		"  (logger as any)[level.toLowerCase()](msg, Object.fromEntries(pairs));\n" +
		"}\n"
	driverPath := filepath.Join(root, "driver.mts")
	writeFile(t, driverPath, driver)
	res := runProgram(t, node, run.Opts{Dir: root, Env: map[string]string{env.LogLevel: "DEBUG"}},
		"--experimental-strip-types", "--no-warnings", driverPath)
	if res.Code != 0 && strings.Contains(res.Stderr, "strip-types") {
		t.Skip("この node は型を剥がして読めない")
	}
	if res.Code != 0 || res.Stdout != "" || res.Stderr != "" {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", res.Code, res.Stdout, res.Stderr)
	}
	checkLines(t, diagLines(t, root, "probe"))
}

// checkLines は、出た行が expectedLines と同じ（時刻と pid を除く）ことを確かめる。
func checkLines(t *testing.T, lines []string) {
	t.Helper()
	if len(lines) != len(expectedLines) {
		t.Fatalf("行数 = %d, want %d: %q", len(lines), len(expectedLines), lines)
	}
	for i, line := range lines {
		if got := bodyOf(t, line); got != expectedLines[i] {
			t.Errorf("%d 行目 = %+v, want %+v", i, got, expectedLines[i])
		}
	}
}

// jsonString は値を JSON の文字列（JS の式としても読める）にする。
func jsonString(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// fileURL は絶対パスを file: の URL にする（Windows の `C:/…` も含む）。
func fileURL(path string) string {
	abs, err := filepath.Abs(path)
	if err != nil {
		panic(err)
	}
	slash := filepath.ToSlash(abs)
	if !strings.HasPrefix(slash, "/") {
		slash = "/" + slash
	}
	return (&url.URL{Scheme: "file", Path: slash}).String()
}

// ----- ccnavi-git.sh と ccnavi-review.sh の拒否・失敗 -----

// guardWorkspace は、ワークスペースに 2 本の番人（ccnavi-git.sh・ccnavi-review.sh）を置く。
func guardWorkspace(t *testing.T) string {
	t.Helper()
	ws := newWorkspace(t)
	for _, name := range []string{"ccnavi-git.sh", "ccnavi-review.sh"} {
		data, err := os.ReadFile(filepath.Join(base.ScriptsDir(), name))
		if err != nil {
			t.Fatal(err)
		}
		writeFile(t, filepath.Join(ws, ".ccnavi", "scripts", name), string(data))
	}
	return ws
}

func runGuard(t *testing.T, ws, name string, args ...string) run.Result {
	t.Helper()
	script := filepath.Join(ws, ".ccnavi", "scripts", name)
	return runProgram(t, firstShell(t), run.Opts{
		Dir: ws,
		Env: map[string]string{"CCNAVI_WORKSPACE": ws, env.EntryPath: exe.ShPath(t)},
	}, append([]string{script}, args...)...)
}

// reject と fail の呼び出しは、識別子を文字どおりに書く（例外は実行ファイルが決めた `$gc_id`）。
func TestGuardRejectsAndFailsNameAnIdentifier(t *testing.T) {
	t.Parallel()
	for _, c := range []struct{ name, word string }{{"ccnavi-git.sh", "reject"}, {"ccnavi-review.sh", "fail"}} {
		data, err := os.ReadFile(filepath.Join(base.ScriptsDir(), c.name))
		if err != nil {
			t.Fatal(err)
		}
		callRE := regexp.MustCompile(`(^|[\s;|&(])` + c.word + ` `)
		wantRE := regexp.MustCompile(`(^|[\s;|&(])` + c.word + ` ([a-z][a-z-]*|"\$gc_id") "`)
		var calls []string
		for _, line := range strings.SplitAfter(string(data), "\n") {
			if callRE.MatchString(line) && !strings.HasPrefix(strings.TrimLeft(line, " \t"), "#") {
				calls = append(calls, line)
			}
		}
		if len(calls) == 0 {
			t.Errorf("%s に %s の呼び出しが無い", c.name, c.word)
		}
		for _, line := range calls {
			if !wantRE.MatchString(line) {
				t.Errorf("%s: 識別子でない呼び出し: %q", c.name, strings.TrimSpace(line))
			}
		}
	}
}

func TestRejectLogsTheIdentifierNotTheText(t *testing.T) {
	t.Parallel()
	ws := guardWorkspace(t)
	res := runGuard(t, ws, "ccnavi-git.sh", "log", "--upload-pack=https://u:SECRET@h/x")
	if res.Code != 2 {
		t.Fatalf("code = %d, want 2", res.Code)
	}
	want := "ccnavi-git: --upload-pack=https://u:SECRET@h/x は、" +
		"読むだけのサブコマンドをファイル書き込みや外部コマンド実行に変えます。" +
		"出力を保存したいなら、このラッパースクリプトが logs/ に全量を残すので" +
		"そちらを読んでください。\n"
	if res.Stderr != want {
		t.Errorf("stderr = %q, want %q", res.Stderr, want)
	}
	lines := diagLines(t, ws, "ccnavi-git")
	if len(lines) != 1 {
		t.Fatalf("診断ログ = %q", lines)
	}
	if got := bodyOf(t, lines[0]).rest; got != "拒否した sub=log reason=output-or-exec" {
		t.Errorf("rest = %q", got)
	}
}

func TestRejectDoesNotFollowALinkedLog(t *testing.T) {
	t.Parallel()
	ws := guardWorkspace(t)
	victim := filepath.Join(ws, "logs", "decisions.jsonl")
	writeFile(t, victim, "{}\n")
	symlink(t, "../decisions.jsonl", filepath.Join(ws, "logs", "diag", "ccnavi-git.log"))
	res := runGuard(t, ws, "ccnavi-git.sh", "reset")
	if res.Code != 2 {
		t.Fatalf("code = %d, want 2", res.Code)
	}
	if !strings.HasPrefix(res.Stderr, "ccnavi-git: reset は作業中の変更やコミットを削除します。") {
		t.Errorf("stderr = %q", res.Stderr)
	}
	if got := readOptional(t, victim); got != "{}\n" {
		t.Errorf("リンク先が書き換わった: %q", got)
	}
}

func TestFailLogsTheIdentifierNotTheText(t *testing.T) {
	t.Parallel()
	ws := guardWorkspace(t)
	res := runGuard(t, ws, "ccnavi-review.sh", "https://u:SECRET@h")
	if res.Code != 2 {
		t.Fatalf("code = %d, want 2", res.Code)
	}
	// 使える副命令の並びは増える（merged の後ろに mr-state）。頭の文面だけ固定する。
	head := "ccnavi-review: https://u:SECRET@h は指定できません。" +
		"指定できるのは review-request / confirm / comment / decide / ready / fetch / origin / merged"
	if !strings.HasPrefix(res.Stderr, head) {
		t.Errorf("stderr = %q", res.Stderr)
	}
	if !strings.HasSuffix(res.Stderr, " です。\n") {
		t.Errorf("stderr の終わり = %q", res.Stderr)
	}
	lines := diagLines(t, ws, "ccnavi-review")
	if len(lines) != 1 {
		t.Fatalf("診断ログ = %q", lines)
	}
	if got := bodyOf(t, lines[0]).rest; got != "止めた sub=https://***@h exit=2 reason=unknown-sub" {
		t.Errorf("rest = %q", got)
	}
}
