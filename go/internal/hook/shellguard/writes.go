package shellguard

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/shell"
)

// シェルから書き込む形の読み。親のワークツリーへの書き込み（CheckParentWrite）とルート直下への書き込み
// （CheckRootWrite）が、同じ読みを使う。
//
// 読む書き込み先は次のとおり。リダイレクト（`>`・`>>`・`>|`・`&>`・`<>`。fd の複製は数えない）の行き先、allArgs の引数、
// copies の行き先（`-t` の値か最後の引数）、`sed -i`・`perl -i` の対象、`dd` の `of=`、outputOptions の値。
// 読めないもの（限界）は、変数・置換で組み立てた書き込み先、スクリプトのファイルを実行する形、ビルドの道具が居場所に書く生成物。
// インタプリタに渡したコードは中を読まず、文字列のまま返す（呼び手が書き込みを疑う語で見る）。

// allArgs は引数がすべて書き込み先（作る・消す・書き換える）になるコマンド。mv は元も消える。
var allArgs = []string{"tee", "touch", "mkdir", "rm", "rmdir", "truncate", "mv", "shred", "unlink"}

// copies は行き先（`-t` の値か最後の引数）に書くコマンド。
var copies = []string{"cp", "install", "ln"}

// valueOptions は値を次の語に取る選択肢（書き込み先と読み違えないため）。
var valueOptions = map[string][]string{
	"touch":    {"-d", "-r", "-t", "--date", "--reference"},
	"mkdir":    {"-m", "--mode", "-Z"},
	"truncate": {"-s", "-r", "--size", "--reference"},
	"mv":       {"-S", "--suffix", "-t", "--target-directory"},
	"cp":       {"-S", "--suffix", "-t", "--target-directory"},
	"install":  {"-S", "--suffix", "-t", "--target-directory", "-m", "-o", "-g"},
	"ln":       {"-S", "--suffix", "-t", "--target-directory"},
	"shred":    {"-n", "-s", "--iterations", "--size"},
	"sed":      {"-e", "-f", "--expression", "--file", "-l", "--line-length"},
	"perl":     {"-e", "-E", "-M", "-I"},
}

// outputOptions は出力先を選択肢で受け取るコマンド（書き込み先は選択肢の値）。
var outputOptions = map[string][]string{
	"curl": {"-o", "--output"},
	"wget": {"-O", "--output-document"},
	"sort": {"-o", "--output"},
}

// interpreters は任意のコードを文字列で受け取るインタプリタ（名前の頭）と、その文字列を取る選択肢。
var (
	interpreters = []string{"python", "node", "perl", "ruby", "php", "deno", "bun", "pwsh", "powershell"}
	codeFlags    = []string{"-c", "-e", "-E", "--eval", "-p", "--print", "-r", "-Command", "-command"}
	// sedScripts は sed のスクリプトを選択肢で渡す形（このときは最初の位置引数もファイル）。
	sedScripts = []string{"-e", "-f", "--expression", "--file"}
)

// code はインタプリタに渡したコード 1 つ。base はその居場所（known が偽なら読めない）。
type code struct {
	base  string
	known bool
	text  string
}

// scanned はコマンド 1 本の読み。base は書き込み先の語を解く起点（known が偽なら読めない）。
type scanned struct {
	targets []string
	base    string
	known   bool
	codes   []code
}

// scanCommand はコマンド 1 本の書き込み先とインタプリタに渡したコード。起点は `cd` と実行役の行き先をたどった居場所。
func scanCommand(cwd string, p shell.Placed) scanned {
	words := stripHeads(p.Words)
	var chdirs []string
	var out scanned
	// リダイレクトは外側のコマンドの語にある。実行役を外す前に拾う。
	out.targets = redirectTargets(words)
	place := func() (string, bool) {
		if cwd == "" {
			return "", false
		}
		return callPlace(cwd, p, chdirs)
	}
	for depth := 0; len(words) > 0 && depth <= shell.UnwrapDepth; depth++ {
		k := shell.NameIndex(words)
		if k >= len(words) {
			break
		}
		name := pyfmt.Lower(shell.Base(words[k]))
		args := dropRedirects(words[k+1:])
		if shell.IsRunner(name) {
			inner := shell.RunnerCommand(name, args)
			if len(inner) == 0 {
				break
			}
			chdirs = append(chdirs, runnerChdirs(name, args)...)
			words = inner
			continue
		}
		switch {
		case hasAnyPrefix(name, interpreters):
			text, ok := codeOf(args)
			if ok {
				// 引用の中の `>` `<` は身代わりの文字に置き換わっている。コードとして読むので戻す。
				base, known := place()
				out.codes = append(out.codes, code{base, known, shell.RestoreQuoted(text)})
			}
			if strings.HasPrefix(name, "perl") && inPlace(name, args) {
				files := positionals(name, args)
				if !ok {
					files = files[min(1, len(files)):]
				}
				out.targets = append(out.targets, files...)
			}
		case slices.Contains(allArgs, name):
			out.targets = append(out.targets, positionals(name, args)...)
		case slices.Contains(copies, name):
			out.targets = append(out.targets, copyDestination(name, args)...)
		case name == "sed" && inPlace(name, args):
			files := positionals(name, args)
			scripted := slices.ContainsFunc(args, func(a string) bool {
				return slices.Contains(sedScripts, a) || strings.HasPrefix(a, "--expression=") || strings.HasPrefix(a, "--file=")
			})
			if !scripted {
				files = files[min(1, len(files)):]
			}
			out.targets = append(out.targets, files...)
		case name == "dd":
			for _, a := range args {
				if strings.HasPrefix(a, "of=") {
					out.targets = append(out.targets, a[len("of="):])
				}
			}
		case outputOptions[name] != nil:
			out.targets = append(out.targets, optionValues(args, outputOptions[name])...)
		}
		break
	}
	out.base, out.known = place()
	return out
}

func hasAnyPrefix(name string, heads []string) bool {
	return slices.ContainsFunc(heads, func(h string) bool { return strings.HasPrefix(name, h) })
}

// writeTarget はシェルの書き込み先 1 つ。word は書いたとおりの語、base は語を解く起点（known が偽なら読めない）、
// origin は取り出した元のコマンド（文に出す短い形）。
type writeTarget struct {
	word   string
	base   string
	known  bool
	origin string
}

// writeTargets はシェルの書き込み先を並べる。subject は打たれたコマンド、layers は実行役が中で実行するコマンドの層。
func writeTargets(cwd, subject string, layers []string) []writeTarget {
	var out []writeTarget
	for n, text := range append([]string{subject}, layers...) {
		for _, p := range shell.PlacedCommands(text, n > 0) {
			s := scanCommand(cwd, p)
			origin := short(shell.RestoreQuoted(strings.Join(p.Words, " ")))
			// `cd` の行き先を読めなかったときは、読めない `cd` を無かったことにして打たれた場所から解く。
			// 起点が無いまま相対パスを捨てると、`cd "$PWD" &&` を足すだけで見えなくなる。
			base, known := s.base, s.known
			if !known && cwd != "" {
				base, known = cwd, true
			}
			for _, t := range s.targets {
				out = append(out, writeTarget{shell.RestoreQuoted(t), base, known, origin})
			}
		}
	}
	return out
}

// resolveWord は書き込み先の語を、リンクを解いた絶対パスに直す。読めなければ空。
//
// 変数・置換・`~`・`{`（`find -exec rm {}` の置き場所など）を含む語と、起点が読めない相対パスは読めない。グロブは
// 固定の部分（最後の `/` まで）のディレクトリを返し、末尾に区切りを付ける（中を書くものとして当たる）。
func resolveWord(word string, base string, known bool) string {
	text := strings.ReplaceAll(word, `\`, "/")
	if text == "" || strings.ContainsAny(text, "$`~{") {
		return ""
	}
	g := strings.IndexAny(text, globChars)
	if g >= 0 {
		text = dirPart(text[:g])
	}
	if !ospath.IsAbs(text) {
		if !known {
			return ""
		}
		text = ospath.Join(base, text)
	}
	path := ospath.Realpath(text)
	if g >= 0 {
		return strings.TrimRight(path, ospath.Sep) + ospath.Sep
	}
	return path
}

// optionValues は `-o FILE`・`-oFILE`・`--output=FILE`・`--output FILE` の FILE。
func optionValues(args []string, options []string) []string {
	var out []string
	for i, arg := range args {
		for _, option := range options {
			long := strings.HasPrefix(option, "--")
			switch {
			case arg == option && i+1 < len(args):
				out = append(out, args[i+1])
			case long && strings.HasPrefix(arg, option+"="):
				_, value, _ := strings.Cut(arg, "=")
				out = append(out, value)
			case !long && strings.HasPrefix(arg, option) && len(arg) > 2:
				out = append(out, arg[2:])
			}
		}
	}
	return out
}

func redirectTargets(words []string) []string {
	var out []string
	for i := 0; i < len(words); {
		width := shell.RedirectWidth(words, i)
		if width == 0 {
			i++
			continue
		}
		op, target := words[i+width-2], words[i+width-1]
		duplicate := strings.HasSuffix(op, "&") && (pyfmt.IsDigit(target) || target == "-")
		if strings.Contains(op, ">") && !duplicate {
			out = append(out, target)
		}
		i += width
	}
	return out
}

func dropRedirects(args []string) []string {
	var kept []string
	for i := 0; i < len(args); {
		if width := shell.RedirectWidth(args, i); width > 0 {
			i += width
			continue
		}
		kept = append(kept, args[i])
		i++
	}
	return kept
}

func positionals(name string, args []string) []string {
	values := valueOptions[name]
	var out []string
	after := false
	for i := 0; i < len(args); {
		arg := args[i]
		i++
		if after || arg == "-" || !strings.HasPrefix(arg, "-") {
			out = append(out, arg)
			continue
		}
		if arg == "--" {
			after = true
			continue
		}
		if slices.Contains(values, arg) {
			i++
		}
	}
	return out
}

func copyDestination(name string, args []string) []string {
	for i, arg := range args {
		switch {
		case (arg == "-t" || arg == "--target-directory") && i+1 < len(args):
			return []string{args[i+1]}
		case strings.HasPrefix(arg, "--target-directory="):
			_, value, _ := strings.Cut(arg, "=")
			return []string{value}
		case strings.HasPrefix(arg, "-t") && len(arg) > 2 && !strings.HasPrefix(arg, "--"):
			return []string{arg[2:]}
		}
	}
	if p := positionals(name, args); len(p) > 1 {
		return p[len(p)-1:]
	}
	return nil
}

// inPlace は `sed -i`・`perl -i` の形か（まとめた短い選択肢 `-pi`・`-Ei` も）。
func inPlace(name string, args []string) bool {
	values := map[rune]bool{}
	for _, v := range valueOptions[name] {
		if r := []rune(v); len(r) == 2 {
			values[r[1]] = true
		}
	}
	for _, arg := range args {
		if arg == "--" {
			return false
		}
		if strings.HasPrefix(arg, "--in-place") {
			return true
		}
		if !strings.HasPrefix(arg, "-") || strings.HasPrefix(arg, "--") {
			continue
		}
		for _, letter := range arg[1:] {
			if letter == 'i' {
				return true
			}
			if values[letter] {
				break
			}
		}
	}
	return false
}

// codeOf はインタプリタに文字列で渡したコード。無ければ偽。
func codeOf(args []string) (string, bool) {
	for i, arg := range args {
		if i+1 >= len(args) {
			break
		}
		if slices.Contains(codeFlags, arg) {
			return args[i+1], true
		}
		// まとめた短い選択肢（`-pe`・`-ic`）。最後の字がコードを取る選択肢なら次の語。
		runes := []rune(arg)
		bundled := strings.HasPrefix(arg, "-") && !strings.HasPrefix(arg, "--") && len(runes) > 2
		if bundled && slices.Contains(codeFlags, "-"+string(runes[len(runes)-1])) {
			return args[i+1], true
		}
	}
	return "", false
}

// short は文に出す短い形（空白をつめて 60 字まで）。
func short(text string) string {
	text = strings.Join(strings.FieldsFunc(text, ospath.IsSpace), " ")
	if len([]rune(text)) <= shortLimit {
		return text
	}
	return pyfmt.Head(text, shortLimit-len(ellipsis)) + ellipsis
}

const (
	shortLimit = 60
	ellipsis   = "..."
)
