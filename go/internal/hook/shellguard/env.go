package shellguard

import (
	"regexp"
	"slices"
	"strings"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"
)

// 同じコマンド行で置いてよい変数。出力の量と待ち時間だけを変え、検査の材料には触れない。通すものを並べる形にして、
// あとから sh が読むようになった変数は止まる側にする。
var passThrough = []string{env.FetchTimeout, env.GitFailLines, env.GitKeepLogs, env.GitMaxLines}

// gitPassThrough は `GIT_` で始まる変数のうち、置いてよいもの。どれも作者の名前と出力の形だけを変え、git が
// 操作するリポジトリ・ワークツリー・索引・設定には触れない。`GIT_DIR=<親>/.git GIT_WORK_TREE=<親> sh …ccnavi-git.sh
// commit` を子のワークツリーで打つと、居場所の検査は子を見るのに、git は親へコミットする。
var gitPassThrough = []string{
	"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
	"GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE",
	"GIT_PAGER", "GIT_TERMINAL_PROMPT", "GIT_MERGE_AUTOEDIT",
}

// 止める変数の頭。sh はワークスペースルートと実行ファイルを `CCNAVI_` の変数から読む。
const (
	ccnaviPrefix = "CCNAVI_"
	gitPrefix    = "GIT_"
)

// alsoBlocked は頭のほかに止める変数。sh のワークスペースルートの探し方に影響する。
var alsoBlocked = []string{env.ClaudeProjectDir}

// EnvCleared は `env -i`（環境を丸ごと空にする）を名前の並びに書く表記。settings.json の env が書き出した置き場も
// 消え、sh は既定の置き場を見る。
const EnvCleared = "env -i"

var envClears = []string{"-i", "--ignore-environment", "-"}

// declares は引数に変数名を取り、その変数を置く・外す・書き出すコマンド。
var declares = []string{"export", "declare", "typeset", "readonly", "local", "unset"}

var (
	assignment = regexp.MustCompile(`^([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?\+?=`)
	varName    = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
)

// Blocked は、同じコマンド行で置くと保護済みの sh を呼ぶ形を止める変数か。
func Blocked(name string) bool {
	if slices.Contains(passThrough, name) || slices.Contains(gitPassThrough, name) {
		return false
	}
	return strings.HasPrefix(name, ccnaviPrefix) || strings.HasPrefix(name, gitPrefix) || slices.Contains(alsoBlocked, name)
}

// CheckEnv は、保護済みの sh を呼ぶコマンド行に止める変数の代入があれば、sh のパスと変数名（書いた順・重なり無し）。
//
// 数えるのはコマンド行のどこで置いたものでもよい。前置きの代入、`env X=…`、`export X=…`、書き出してある変数への素の
// 代入（settings.json の env が書き出した変数は代入だけで sh に届く）、`unset X`・`env -u X`。読み切れない形は
// allow がヒットせずに確認になるので、ここでは見ない。
func CheckEnv(subject string, degraded bool) (script string, names []string) {
	if degraded {
		return "", nil
	}
	places := shell.PlacedCommands(subject, false)
	for _, p := range places {
		if call, ok := findScriptCall(p.Words); ok {
			script = call.script
			break
		}
	}
	if script == "" {
		return "", nil
	}
	for _, p := range places {
		for _, n := range setNames(p.Words) {
			if (n == EnvCleared || Blocked(n)) && !slices.Contains(names, n) {
				names = append(names, n)
			}
		}
	}
	return script, names
}

// setNames はコマンド 1 本が置く・外す変数の名前。実行役の層も見る。
func setNames(words []string) []string {
	var names []string
	words = stripHeads(words)
	for depth := 0; len(words) > 0 && depth <= shell.UnwrapDepth; depth++ {
		k := shell.NameIndex(words)
		for _, w := range words[:k] {
			if a := assigned(w); a != "" {
				names = append(names, a)
			}
		}
		if k >= len(words) {
			break
		}
		name := shell.Base(words[k])
		args := words[k+1:]
		if name == "env" {
			names = append(names, envNames(args)...)
		}
		if slices.Contains(declares, name) {
			for _, arg := range args {
				if strings.HasPrefix(arg, "-") || strings.HasPrefix(arg, "+") {
					continue
				}
				if a := assigned(arg); a != "" {
					names = append(names, a)
				} else if varName.MatchString(arg) {
					names = append(names, arg)
				}
			}
			break
		}
		if !shell.IsRunner(name) {
			break
		}
		inner := shell.RunnerCommand(name, args)
		if len(inner) == 0 {
			break
		}
		words = inner
	}
	return names
}

// envNames は `env` が置く・外す変数（`-u X`・`--unset=X`・`X=…`）。環境を空にする形は EnvCleared。
func envNames(args []string) []string {
	var names []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		switch {
		case (arg == "-u" || arg == "--unset" || arg == "-C" || arg == "--chdir") && i+1 < len(args):
			if arg == "-u" || arg == "--unset" {
				names = append(names, args[i+1])
			}
			i++
		case strings.HasPrefix(arg, "--unset="):
			names = append(names, arg[len("--unset="):])
		case strings.HasPrefix(arg, "-u") && len(arg) > 2:
			names = append(names, arg[2:])
		case slices.Contains(envClears, arg) || clearsByBundle(arg):
			names = append(names, EnvCleared)
		case assigned(arg) != "":
			names = append(names, assigned(arg))
		case !strings.HasPrefix(arg, "-"):
			return names
		}
	}
	return names
}

// clearsByBundle はまとめた短いオプションに `i` がある（`-vi`）。
func clearsByBundle(arg string) bool {
	return strings.HasPrefix(arg, "-") && !strings.HasPrefix(arg, "--") && strings.Contains(arg[1:], "i")
}

func assigned(word string) string {
	if m := assignment.FindStringSubmatch(word); m != nil {
		return m[1]
	}
	return ""
}

// EnvMessage は環境変数の代入で止めた文。
func EnvMessage(root, script string, names []string) string {
	pass := slices.Clone(passThrough)
	slices.Sort(pass)
	return texts.Say(root, CodeEnv, texts.Values{
		"script":       script,
		"names":        strings.Join(names, ", "),
		"pass_through": strings.Join(pass, ", "),
	}, nil)
}
