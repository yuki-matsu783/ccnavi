package shell

import (
	"regexp"
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
)

// 語の並びだけを見て、コマンド名・実行役・オプションの幅・リダイレクトを見分ける。

type set map[string]bool

func newSet(items ...string) set {
	s := make(set, len(items))
	for _, item := range items {
		s[item] = true
	}
	return s
}

// 引用の中の `>` と `<` の身代わり。走査が shlex に渡す前に置き換え、引用された `>` `<` を演算子と
// 読ませない。`<<` は置き換えない（`"<<"` は演算子と区別が付かず、縮退させる）。
// 入力に現れたものは prepare が取り除く。
const (
	quotedGT = "\x02"
	quotedLT = "\x03"
)

// quotedBack は身代わりの文字から書かれた文字へ戻す表。並びは置き換える順。
var quotedBack = [...][2]string{{quotedGT, ">"}, {quotedLT, "<"}}

// RestoreQuoted は身代わりの文字を書かれた `>` `<` に戻す。
func RestoreQuoted(text string) string {
	for _, pair := range quotedBack {
		text = strings.ReplaceAll(text, pair[0], pair[1])
	}
	return text
}

// operators はコマンドとコマンドを分けるトークン。置換の中身は走査が取り出してあるので、
// 残る括弧はサブシェルと case の `)` だけ。
var operators = newSet(";", ";;", "&", "&&", "|", "||", "|&", "(", ")")

// runsAString は文字列を受け取って実行するコマンド。何を実行するかが語に無い。
var runsAString = newSet("eval", "source", ".", "xargs")

// readonlyRunnee は xargs と find -exec の後ろに置いてよい、読むだけのコマンド。
// 中身をそのまま出す cat・head・tail は入れない。
var readonlyRunnee = newSet(
	"grep", "egrep", "fgrep", "wc", "ls", "file", "stat", "sort", "uniq", "basename", "dirname", "echo",
)

// ReadonlyRunnees は xargs と find -exec の後ろで読むだけと数えるコマンド名（並べ替え済み）。
func ReadonlyRunnees() []string {
	names := make([]string, 0, len(readonlyRunnee))
	for name := range readonlyRunnee {
		names = append(names, name)
	}
	slices.Sort(names)
	return names
}

// findActions は find の述語のうち、書き込みや実行をするもの。
var findActions = newSet("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls")

// findExec は find の、後ろに書いたコマンドを実行する述語。`;` か `+` までがコマンド。
var findExec = []string{"-exec", "-execdir", "-ok", "-okdir"}

// xargsValueOptions は xargs の、次の語を値に取るオプション。一覧に無いものは値を取らない。
var xargsValueOptions = newSet(
	"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a",
	"--max-args", "--max-procs", "--delimiter", "--arg-file", "--max-chars", "--max-lines", "--process-slot-var",
)

// reserved はコマンドの位置で予約語になる語。予約語の後ろはコマンドの先頭なので 1 本に切る
// （`then find . -delete` の find を find と読むため）。
var reserved = newSet(
	"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "case", "esac",
	"select", "time", "coproc", "!", "{", "}",
)

// takesAWord は後ろの 1 本がコマンドでない予約語（`case $x in` の値、`for x in` の変数名）。
var takesAWord = newSet("case", "for", "select")

// IsReserved はコマンドの位置で予約語になる語か。
func IsReserved(word string) bool { return reserved[word] }

// TakesAWord は後ろの 1 本をコマンドとして読まない予約語か。
func TakesAWord(word string) bool { return takesAWord[word] }

// takesCodeFlag はフラグを付けたときだけ文字列を実行するコマンド。シェルで線を引く。
// インタプリタの 1 行スクリプトは禁止語に触れる文書を直す普通の書き方なので入れない。
var takesCodeFlag = map[string][]string{
	"bash": {"-c"}, "sh": {"-c"}, "zsh": {"-c"}, "ksh": {"-c"}, "dash": {"-c"}, "su": {"-c"},
	"find": findExec,
}

// shells はファイルを渡せばそれを、`-c` なら文字列を実行するシェル。
var shells = newSet("sh", "bash", "zsh", "dash", "ksh")

// shellValueOptions はシェルのオプションで、次の語を値に取るもの。
var shellValueOptions = newSet("-o", "+o", "-O", "+O", "--rcfile", "--init-file")

// sources は最初の引数のファイルを今のシェルで実行するもの。
var sources = newSet(".", "source")

// IsShell は `sh` `bash` などのシェルの名前か。
func IsShell(name string) bool { return shells[name] }

// IsSource は `.` か `source` か。
func IsSource(name string) bool { return sources[name] }

// IsShellValueOption はシェルのオプションで次の語を値に取るものか。
func IsShellValueOption(word string) bool { return shellValueOptions[word] }

// UnwrapDepth は実行役のコマンドを外す深さの上限。元の形は数えない。
const UnwrapDepth = 4

// runner は実行役のコマンドの読み方。値を取るオプションと、コマンドの前に置く位置引数の数。
// 一覧に無いオプションは値を取らないと読む。読み違えても誤った層がヒットしないだけで、元の形の
// 判定は変わらない。
type runner struct {
	valueOptions set
	positionals  int
	// assignments はコマンドの前に `名前=値` を並べてよいか。
	assignments bool
	// lookupOnly はそのコマンドを探すだけになる短いオプションの文字（`command -v`）。
	lookupOnly string
}

// runners は別のコマンドを実行することが仕事のコマンド。組み込みで持ち、ルールファイルからは
// 足せない（足せると消せるので、保護の根拠を守られる側に置かない）。
var runners = map[string]runner{
	"env":     {valueOptions: newSet("-u", "--unset", "-C", "--chdir"), assignments: true},
	"command": {lookupOnly: "vV"},
	"exec":    {valueOptions: newSet("-a")},
	"nohup":   {},
	"time":    {valueOptions: newSet("-f", "-o", "--format", "--output")},
	"nice":    {valueOptions: newSet("-n", "--adjustment")},
	"sudo": {valueOptions: newSet(
		"-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t", "-T", "-D", "-R",
		"--user", "--group", "--close-from", "--host", "--prompt", "--other-user", "--role", "--type",
		"--command-timeout", "--chdir", "--chroot",
	), assignments: true},
	"doas":    {valueOptions: newSet("-u", "-C")},
	"timeout": {valueOptions: newSet("-s", "--signal", "-k", "--kill-after"), positionals: 1},
	"stdbuf":  {valueOptions: newSet("-i", "-o", "-e", "--input", "--output", "--error")},
	"chrt":    {positionals: 1},
	"ionice":  {valueOptions: newSet("-c", "-n", "-p", "--class", "--classdata", "--pid")},
	"taskset": {positionals: 1},
}

// IsRunner は別のコマンドを実行する実行役のコマンドの名前か。
func IsRunner(name string) bool {
	_, ok := runners[name]
	return ok
}

// identifierForm は sh の名前（変数・関数）の形。
const identifierForm = `[A-Za-z_][A-Za-z0-9_]*`

// assignmentWord はコマンド名の前に置ける代入。配列の要素と足し込みも代入。名前は 1 つ目の組。
var assignmentWord = regexp.MustCompile(`^(` + identifierForm + `)(\[[^\]]*\])?\+?=`)

// identifier は sh の名前だけの語。
var identifier = regexp.MustCompile(`^` + identifierForm + `$`)

// runnerAssignment は env と sudo が代入と読む引数。`=` を含めば名前の表記を問わない（シェルより広い）。
var runnerAssignment = regexp.MustCompile(`^[^=-][^=]*=`)

// namedFD は bash 4.1 以降の名前付き fd（`{fd}>/dev/null`）。
var namedFD = regexp.MustCompile(`^\{` + identifierForm + `\}$`)

// IsAssignment はコマンドの前に置ける代入の語（`FOO=1`・`a[1]=x`・`x+=1`）か。
func IsAssignment(word string) bool { return assignmentWord.MatchString(word) }

// AssignedName は代入の語の名前（`a[1]+=x` なら `a`）。代入でなければ空。
func AssignedName(word string) string {
	if m := assignmentWord.FindStringSubmatch(word); m != nil {
		return m[1]
	}
	return ""
}

// IsIdentifier は sh の変数・関数の名前に使える形（`[A-Za-z_][A-Za-z0-9_]*`）か。
func IsIdentifier(word string) bool { return identifier.MatchString(word) }

// classify は、文字列をコードとして実行するコマンドがあれば ReasonTakenAsCode を返す。
func classify(commands [][]string) Reason {
	for _, command := range commands {
		// 文字列を実行するコマンドは先頭の語だけを見る。引数の `.` は「このディレクトリ」。
		if len(command) > 0 && runsAString[Base(command[0])] && !xargsReadonly(command) {
			return ReasonTakenAsCode
		}
		// `-` で始まる頭は、`find -exec … \;` の続きか引用された `(` `;` で切れた断片。
		// 書き込み・実行の述語を含むなら、find の続きを見逃さないよう縮退させる。
		if len(command) > 0 && strings.HasPrefix(command[0], "-") && slices.ContainsFunc(command, func(t string) bool { return findActions[t] }) {
			return ReasonTakenAsCode
		}
		// フラグで文字列を実行するものは先頭でなくてもよい（`sudo -u root sh -c`）。
		for i, token := range command {
			name := Base(token)
			flags := takesCodeFlag[name]
			if flags != nil && hasFlag(command[i+1:], flags) &&
				!(name == "find" && execsReadonly(command[i+1:], flags)) {
				return ReasonTakenAsCode
			}
		}
	}
	return ""
}

func readonly(words []string) bool {
	return len(words) > 0 && readonlyRunnee[pathcase.Fold(Base(words[0]))]
}

func xargsReadonly(command []string) bool {
	if Base(command[0]) != "xargs" {
		return false
	}
	rest := skipOptions(command[1:], xargsValueOptions)
	return readonly(rest[:min(1, len(rest))])
}

// execsReadonly は `-exec` などの次の語が、どれも読むだけのコマンドか。
func execsReadonly(tokens []string, flags []string) bool {
	for j, token := range tokens {
		if slices.Contains(flags, token) && !readonly(tokens[j+1:min(j+2, len(tokens))]) {
			return false
		}
	}
	return true
}

// skipOptions は先頭のオプション（と値）を飛ばした残り。`--` はそこで終わる。
func skipOptions(args []string, valueOptions set) []string {
	i := 0
	for i < len(args) {
		token := args[i]
		if token == "--" {
			return args[i+1:]
		}
		if !strings.HasPrefix(token, "-") || token == "-" {
			break
		}
		i += optionWidth(token, args[i+1:], valueOptions)
	}
	return args[min(i, len(args)):]
}

// hasFlag は短いオプションのまとめ書き（`bash -lc`）も見る。
func hasFlag(tokens []string, flags []string) bool {
	for _, token := range tokens {
		for _, flag := range flags {
			if token == flag {
				return true
			}
			if len(flag) == 2 && len(token) > 1 && strings.HasPrefix(token, "-") &&
				!strings.HasPrefix(token, "--") && strings.Contains(token, flag[1:]) {
				return true
			}
		}
	}
	return false
}

// Base はどのプログラムを指すかを変えない部分（ディレクトリと .exe などの拡張子）を除いた名前。
// git のルールが /usr/bin/git と git.exe にもヒットするように。
func Base(name string) string {
	for _, separator := range []string{"/", `\`} {
		if k := strings.LastIndex(name, separator); k >= 0 {
			name = name[k+1:]
		}
	}
	for _, extension := range []string{".exe", ".cmd", ".bat"} {
		// コマンドの名前の拡張子（ASCII）を Python の lower() と同じに比べる。パスの比べではないので pathcase.Fold は使わない。
		if len(name) >= len(extension) && strings.EqualFold(name[len(name)-len(extension):], extension) &&
			isASCII(name[len(name)-len(extension):]) {
			return name[:len(name)-len(extension)]
		}
	}
	return name
}

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// NameIndex はコマンドの位置の語が何番目か。前の代入とリダイレクトを飛ばす。無ければ語の数。
func NameIndex(command []string) int {
	i := 0
	for i < len(command) {
		if IsAssignment(command[i]) {
			i++
			continue
		}
		width := RedirectWidth(command, i)
		if width == 0 {
			return i
		}
		i += width
	}
	return len(command)
}

// RedirectWidth は i から始まるリダイレクトが占める語の数。リダイレクトでなければ 0。
//
// 区切りの演算子は切り出してあるので、コマンドの中に残る演算子の塊はリダイレクト。前に fd の番号か
// 名前付き fd が付く。行き先の語が無いもの（最後の語が演算子）は bash では構文エラーで何も書かれない
// ので、リダイレクトに数えない。
func RedirectWidth(command []string, i int) int {
	fd := isDigits(command[i]) || namedFD.MatchString(command[i])
	k := i
	if fd && i+1 < len(command) {
		k = i + 1
	}
	if k+1 >= len(command) || !isOperator(command[k]) {
		return 0
	}
	return k - i + 2
}

// RunnerCommand は実行役のコマンドの、オプションと値と位置引数を飛ばした残り。無ければ nil。
// `command -v` のように探すだけの形も nil。
func RunnerCommand(name string, args []string) []string {
	r := runners[name]
	i := 0
	for i < len(args) {
		token := args[i]
		if token == "--" {
			i++
			break
		}
		if r.assignments && runnerAssignment.MatchString(token) {
			i++
			continue
		}
		if !strings.HasPrefix(token, "-") || token == "-" {
			break
		}
		if r.lookupOnly != "" && !strings.HasPrefix(token, "--") && strings.ContainsAny(token[1:], r.lookupOnly) {
			return nil
		}
		i += optionWidth(token, args[i+1:], r.valueOptions)
	}
	for r.assignments && i < len(args) && runnerAssignment.MatchString(args[i]) {
		i++
	}
	i += r.positionals
	if i >= len(args) {
		return nil
	}
	return args[i:]
}

// RunnerOptionWidth は実行役のコマンドのオプション 1 つが占める語の数。
func RunnerOptionWidth(name, token string, following []string) int {
	return optionWidth(token, following, runners[name].valueOptions)
}

// RunnerTakesValue は実行役のオプション（`-u`・`--unset` の形）が値を取るか。
func RunnerTakesValue(name, option string) bool { return runners[name].valueOptions[option] }

// optionWidth はオプション 1 つが占める語の数。値を別の語に取るなら 2。`--unset=HOME` と `-o0` は
// 値が同じ語に入る。まとめ書き（`-Eu me`）は値を取る文字が最後に来たときだけ次の語を値に取る。
func optionWidth(token string, following []string, valueOptions set) int {
	if strings.HasPrefix(token, "--") {
		if !strings.Contains(token, "=") && valueOptions[token] && len(following) > 0 {
			return 2
		}
		return 1
	}
	letters := []rune(token)[1:]
	for k, letter := range letters {
		if valueOptions["-"+string(letter)] {
			if k == len(letters)-1 && len(following) > 0 {
				return 2
			}
			return 1
		}
	}
	return 1
}

// isOperator は演算子のトークンか。shlex は演算子の文字だけでできた塊を返す。
func isOperator(token string) bool {
	return token != "" && strings.Trim(token, punctuation) == ""
}

// ---- 標準入力からスクリプトを読むシェル

var (
	stdinShells  = newSet("sh", "bash", "dash", "zsh", "ksh", "ash")
	stdinScripts = newSet("-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0")
	// stdinHeads はシェルの前に置ける語（`… | env sh`）。
	stdinHeads       = newSet("busybox", "env", "command", "exec")
	stdinHeadValues  = newSet("-u", "--unset", "-C", "--chdir", "-a")
	stdinShellLongKV = newSet("--rcfile", "--init-file")
)

func stdinBase(word string) string {
	word = ospath.Slashed(word)
	return pyfmt.Lower(word[strings.LastIndex(word, "/")+1:])
}

// ReadsStdin は標準入力からスクリプトを読むシェルか（`sh`・`sh -s …`・`sh < x`・`busybox sh`・
// `sh /dev/stdin`）。前の env・command・exec とそのオプション・代入は外して見る。`-c` は読まない。
func ReadsStdin(words []string) bool {
	rest := stripStdinHeads(words)
	if len(rest) == 0 || !stdinShells[stdinBase(rest[0])] {
		return false
	}
	rest = rest[1:]
	for k := 0; k < len(rest); {
		if width := RedirectWidth(rest, k); width > 0 {
			k += width
			continue
		}
		word := rest[k]
		if stdinScripts[word] {
			return true
		}
		if word == "--" {
			return k+1 == len(rest) || stdinScripts[rest[k+1]]
		}
		if strings.HasPrefix(word, "--") {
			k++
			if stdinShellLongKV[word] {
				k++
			}
			continue
		}
		if !strings.HasPrefix(word, "-") && !strings.HasPrefix(word, "+") {
			return false
		}
		flags := word[1:]
		if strings.Contains(flags, "c") {
			return false
		}
		if strings.Contains(flags, "s") {
			return true
		}
		// `-o pipefail`・`-O extglob`・`-euo pipefail` は次の語が値。
		k++
		if strings.ContainsAny(flags, "oO") {
			k++
		}
	}
	return true
}

func stripStdinHeads(rest []string) []string {
	for len(rest) > 0 {
		if !stdinHeads[stdinBase(rest[0])] {
			return rest
		}
		rest = rest[1:]
		for len(rest) > 0 && (IsAssignment(rest[0]) || strings.HasPrefix(rest[0], "-")) {
			word := rest[0]
			rest = rest[1:]
			if word == "--" {
				break
			}
			if stdinHeadValues[word] && len(rest) > 0 {
				rest = rest[1:]
			}
		}
	}
	return rest
}
