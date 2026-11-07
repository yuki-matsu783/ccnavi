package shellguard

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
)

// scriptName は保護済みの sh の名前。置き場は `.ccnavi/scripts/` に固定だが、呼び出しの綴りは絶対パス・相対パス・
// ワークツリーの中のコピーとまちまちなので、パスではなく名前で見る。大小は問わない（Windows と macOS の既定の
// ファイルシステムでは `CCNAVI-GIT.SH` も同じファイルで、区別すると表記を変えるだけで外せる）。
var scriptName = pyre.MustCompile(`ccnavi-[A-Za-z0-9-]+\.sh`, pyre.IgnoreCase)

// gitScript はラッパーの名前を比べる形（小文字）。
var gitScript = pyfmt.Lower(config.GitWrapper)

// chdirShort は実行役のうち、後ろのコマンドを別のディレクトリで走らせる短いオプションの字（長い形はどちらも `--chdir`）。
var chdirShort = map[string]rune{"env": 'C', "sudo": 'D'}

// scriptCall はコマンド 1 本が保護済みの sh を呼ぶ形。index は sh の語が元の語の並びの何番目か
// （同じ綴りの別の語と見分ける）。chdirs は実行役が移す先のディレクトリ（書いた順）。
type scriptCall struct {
	script string
	index  int
	args   []string
	chdirs []string
}

// findScriptCall は `sh <sh>`・`bash <sh>`・`<sh>` の直接の実行・`. <sh>` と、それを `env`・`sudo` などの
// 実行役越しに呼ぶ形を読む。`sh -c '…'` は読み切れない形として shell.Read が縮退させる。
func findScriptCall(words []string) (scriptCall, bool) {
	off := len(words)
	words = stripHeads(words)
	off -= len(words)
	var chdirs []string
	for depth := 0; len(words) > 0 && depth <= shell.UnwrapDepth; depth++ {
		k := shell.NameIndex(words)
		if k >= len(words) {
			return scriptCall{}, false
		}
		name := shell.Base(words[k])
		args := words[k+1:]
		if shell.IsRunner(name) {
			inner := shell.RunnerCommand(name, args)
			if len(inner) == 0 {
				return scriptCall{}, false
			}
			chdirs = append(chdirs, runnerChdirs(name, args)...)
			// inner は args の後ろの部分。元の並びでの位置を保つ。
			off += k + 1 + len(args) - len(inner)
			words = inner
			continue
		}
		if shell.IsShell(name) || shell.IsSource(name) {
			i := 0
			for i < len(args) && (strings.HasPrefix(args[i], "-") || strings.HasPrefix(args[i], "+")) && args[i] != "-" {
				if shell.IsShellValueOption(args[i]) {
					i++
				}
				i++
			}
			if i < len(args) && isScript(args[i]) {
				return scriptCall{args[i], off + k + 1 + i, args[i+1:], chdirs}, true
			}
			return scriptCall{}, false
		}
		if isScript(words[k]) {
			return scriptCall{words[k], off + k, args, chdirs}, true
		}
		return scriptCall{}, false
	}
	return scriptCall{}, false
}

func baseName(word string) string { return ospath.Basename(strings.ReplaceAll(word, `\`, "/")) }

func isScript(word string) bool { return scriptName.FullMatch(baseName(word)) }

func isGitScript(word string) bool { return pyfmt.Lower(baseName(word)) == gitScript }

// mentionsGitScript は文字列のどこかにラッパーの名前があるか（読む前の粗い絞り込み）。
func mentionsGitScript(text string) bool { return strings.Contains(pyfmt.Lower(text), gitScript) }

// stripHeads はコマンドの位置より前に来る語（予約語と関数の定義の頭）を除く。
//
// 関数の本体は定義の行では走らないが、同じコマンド行の後ろで呼べば走る。呼ばれるかは読みで決まらないので、
// 本体は走るものとして数える。頭を除かないと、名前をコマンドと読み違え、本体の sh の呼び出しと代入を見落とす。
func stripHeads(words []string) []string {
	i := 0
	for i < len(words) {
		word := words[i]
		if word == "function" {
			i += 2
			if i < len(words) && words[i] == "()" {
				i++
			}
			continue
		}
		if shell.IsReserved(word) {
			if shell.TakesAWord(word) {
				return nil
			}
			i++
			continue
		}
		if i+1 < len(words) && words[i+1] == "()" && shell.IsIdentifier(word) {
			i += 2
			continue
		}
		if word == "(" {
			i++
			continue
		}
		break
	}
	return words[min(i, len(words)):]
}

// unreadPlace は居場所の表記のうち、シェルが実行の時点で値を決めるもの（変数・置換・glob・`~`）。
const unreadPlace = "$`*?[~"

// callPlace は sh が実際に走る場所。`cd` を追った先に、実行役の `-C`・`-D`・`--chdir` の行き先を重ねる。
// どれかが読めなければ偽（どこで走るかが決まらない。呼び手は止める側に倒す）。
func callPlace(cwd string, p shell.Placed, chdirs []string) (string, bool) {
	if !p.Known {
		return "", false
	}
	place := p.Here
	if !ospath.IsAbs(place) {
		place = ospath.Join(cwd, place)
	}
	for _, target := range chdirs {
		if target == "" || strings.ContainsAny(target, unreadPlace) {
			return "", false
		}
		place = ospath.Join(place, target)
	}
	return place, true
}

// runnerChdirs は実行役が後ろのコマンドを動かすディレクトリ（書いた順）。
func runnerChdirs(name string, args []string) []string {
	short, ok := chdirShort[name]
	if !ok {
		return nil
	}
	var found []string
	for i := 0; i < len(args); {
		arg := args[i]
		if arg == "--" || !strings.HasPrefix(arg, "-") || arg == "-" {
			break
		}
		if strings.HasPrefix(arg, "--") {
			option, value, eq := strings.Cut(arg, "=")
			if option == "--chdir" {
				if eq {
					found = append(found, value)
				} else if i+1 < len(args) {
					found = append(found, args[i+1])
				}
			}
			i += shell.RunnerOptionWidth(name, arg, args[i+1:])
			continue
		}
		letters := []rune(arg[1:])
		for k, letter := range letters {
			if letter == short {
				if rest := string(letters[k+1:]); rest != "" {
					found = append(found, rest)
				} else if i+1 < len(args) {
					found = append(found, args[i+1])
				}
				break
			}
			if shell.RunnerTakesValue(name, "-"+string(letter)) {
				break
			}
		}
		i += shell.RunnerOptionWidth(name, arg, args[i+1:])
	}
	return found
}

// GitCall は `ccnavi-git.sh <sub>` の呼び出し 1 つ。Args はサブコマンドの後ろの引数、Place は走る場所
// （Known が偽なら追えない）。
type GitCall struct {
	Args  []string
	Place string
	Known bool
}

// GitCalls はコマンド行の `ccnavi-git.sh <sub>` の呼び出し。走る場所は cwd から `cd` と実行役の行き先を追った先。
// 読み切れないコマンド行（degraded）と cwd が空のときは空。
func GitCalls(cwd, subject string, degraded bool, sub string) []GitCall {
	if degraded || cwd == "" {
		return nil
	}
	var found []GitCall
	for _, p := range shell.PlacedCommands(subject, false) {
		call, ok := findScriptCall(p.Words)
		if !ok || !isGitScript(call.script) || len(call.args) == 0 || call.args[0] != sub {
			continue
		}
		place, known := callPlace(cwd, p, call.chdirs)
		found = append(found, GitCall{Args: call.args[1:], Place: place, Known: known})
	}
	return found
}
