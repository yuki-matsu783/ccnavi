package useronly

import (
	"regexp"
	"strings"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"
)

// HumanPathForm が見るのは、hook のほかに保護が無いユーザの判断の形。
//
//  1. ターミナル要求を切る形（環境変数と `--guard-ticket-approval`）。切れなければ、ターミナルを持たない
//     エージェントは実行ファイルの側で止まる
//  2. ボードの経路の形。`--yes` はターミナルを求めないので、ここが唯一の保護になる
//
// 実行ファイルを呼ぶ表記は追い切れない（`uv run -m ccnavi`、名前を変えたコピー、`awk` の `system()`、
// `python -c` に引数のリストで渡す形）。だからコマンドの位置は見ず、引用符と `\` を除いた生の文字列の全体から
// 探す（`--y""es`・`"--yes"` を同じに読む）。外すのは、並んだコマンドが全部、表示・検索・閲覧の道具のときだけ。

// HumanKind は HumanPathForm が見つけた形の種類。
type HumanKind string

const (
	HumanNone       HumanKind = ""
	HumanGuardOff   HumanKind = "guard-off"
	HumanUserScript HumanKind = "user-script"
	HumanBoard      HumanKind = "board"
	// HumanExpanded は `user` の語と、展開を含む `--` の語が並ぶ形。
	HumanExpanded HumanKind = "expanded"
)

const guardName = env.GuardTicketApproval

var (
	// guardRead は変数を読むだけの形（`$X`・`${X}`・`$env:X`・`${env:X}`）。後ろに代入が続けば読むだけではない。
	guardRead = compile(`\$\{(?:env:)?`+guardName+`\}(?!\s*[:+]?=)`+
		`|\$(?:env:)?`+guardName+`(?![A-Za-z0-9_}])(?!\s*[:+]?=)`, pyre.IgnoreCase)
	guardMention = compile(`(?<![A-Za-z0-9_])`+guardName+`(?![A-Za-z0-9_])`, pyre.IgnoreCase)
	// guardExpandedName は `CCNAVI_` で始まる名前の途中に展開が混じる形（`CCNAVI_GUARD_TICKET_${X}APPROVAL=…`）。
	// 実行の時点でターミナル要求を切る変数の名前になりうる。前が `$`・`{` の形は変数を読むだけなので数えない。
	guardExpandedName = compile(`(?<![A-Za-z0-9_${])CCNAVI_[A-Za-z0-9_]*[$`+"`]", pyre.IgnoreCase)
	// expandedOption は展開を含む `--` で始まる語（`--y${X}es`）。`user` の語が同じコマンド行にあれば止める。
	// 入口の表記と組み合わせの検査は、展開で組み立てた語を文字として読めないため（暫定の止めで、止める側に広い）。
	expandedOption = compile(`(?<![^\s\x00;&|(])--[^\s\x00;&|]*[$`+"`]", 0)
	// userAlone は単独の語の `user`（`user.email` や `--user` は数えない）。
	userAlone  = compile(`(?<![^\s\x00;&|(=])`+userWord+`(?![^\s\x00;&|)])`, pyre.IgnoreCase)
	guardFlag  = compile(`--guard-ticket-approval(?:\s*=\s*|\s+)(?!enable(?![\w-]))`, pyre.IgnoreCase)
	boardRx    = buildBoardCombos()
	userScript = compile(userScriptPattern(), pyre.IgnoreCase)
)

func buildBoardCombos() [][]rx {
	out := make([][]rx, len(boardCombos))
	for i, combo := range boardCombos {
		for _, g := range combo {
			out[i] = append(out[i], compile(wordPattern(names(anywhere, g)), pyre.IgnoreCase))
		}
	}
	return out
}

// readers は表示・検索・閲覧だけをする道具。コマンドを実行する機能か変数に書く機能を持つもの（`sed` の `e`、
// `awk` の `system()`、`git` の別名、`xargs`、`find -exec`、`printf -v`）は入れない。
var readers = map[string]bool{
	"echo": true, "grep": true, "egrep": true, "fgrep": true, "rg": true, "cat": true,
	"less": true, "more": true, "head": true, "tail": true, "wc": true,
}

// 区切りとシェルの代入。どちらも ASCII だけを見るので RE2 で読む。
var (
	segmentSep     = regexp.MustCompile(`[;&|\n]+`)
	assignmentWord = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*\+?=`)
)

// fields は Python の str.split()。
func fields(s string) []string { return strings.FieldsFunc(s, ospath.IsSpace) }

// lastPart は語の最後のパスの要素（`\` も区切りに読む）。
func lastPart(word string) string {
	word = strings.ReplaceAll(word, `\`, "/")
	return word[strings.LastIndex(word, "/")+1:]
}

func flat(subject string) string {
	return strings.ReplaceAll(strings.ReplaceAll(subject, shell.Sep, "\n"), shell.WordSep, " ")
}

// dequoted は引用符とバックスラッシュを除いた文字列。分け書き（`--y""es`）と引用を同じに読む。
// keepTicks ならバッククォート（コマンド置換）は残す。展開の有無を見る検査が使う。
func dequoted(subject string, keepTicks bool) string {
	drop := `'"` + "`" + `\`
	if keepTicks {
		drop = `'"\`
	}
	return strings.Map(func(r rune) rune {
		if strings.ContainsRune(drop, r) {
			return -1
		}
		return r
	}, flat(subject))
}

// onlyReaders は並んだコマンドが全部、表示・検索・閲覧の道具か。
func onlyReaders(text string) bool {
	for _, segment := range segmentSep.Split(text, -1) {
		words := fields(segment)
		// 前置きの代入（`X=… cmd`）は飛ばす。代入そのものは別に見る。
		for len(words) > 0 && assignmentWord.MatchString(words[0]) {
			words = words[1:]
		}
		if len(words) == 0 {
			continue
		}
		name := strings.TrimSuffix(pyfmt.Lower(lastPart(words[0])), ".exe")
		if !readers[name] {
			return false
		}
	}
	return true
}

// commitMessages はコミットの文面（`git commit` / `ccnavi-git.sh commit` の `-m` / `--message` の値）。
// 文面は実行されないので探す対象から外す。コマンド置換を含む値は実行されるので外さない。読み切れなければ空。
func commitMessages(subject string) []string {
	var found []string
	for _, placed := range shell.PlacedCommands(subject, false) {
		words := placed.Words
		committing, gitLike := false, false
		for _, w := range words {
			committing = committing || w == "commit"
			if n := lastPart(w); n == "git" || n == "ccnavi-git.sh" {
				gitLike = true
			}
		}
		if !committing || !gitLike {
			continue
		}
		for i, word := range words {
			var value string
			switch {
			case (word == "-m" || word == "--message") && i+1 < len(words):
				value = words[i+1]
			case strings.HasPrefix(word, "--message="):
				_, value, _ = strings.Cut(word, "=")
			case strings.HasPrefix(word, "-m") && len(word) > 2 && !strings.HasPrefix(word, "--"):
				value = word[2:]
			default:
				continue
			}
			if !strings.Contains(value, "$(") && !strings.Contains(value, "`") {
				found = append(found, value)
			}
		}
	}
	return found
}

// HumanPathForm は hook のほかに保護が無いユーザの判断の形があれば、その種類と見つけた文字列を返す。
func HumanPathForm(subject string) (HumanKind, string) {
	text := dequoted(subject, false)
	ticked := dequoted(subject, true)
	for _, message := range commitMessages(subject) {
		text = strings.Replace(text, dequoted(message, false), " ", 1)
		ticked = strings.Replace(ticked, dequoted(message, true), " ", 1)
	}
	if onlyReaders(text) {
		return HumanNone, ""
	}
	if found, ok := guardMention.text(removeAll(guardRead, text)); ok {
		return HumanGuardOff, found
	}
	if found, ok := guardExpandedName.text(ticked); ok {
		return HumanGuardOff, found
	}
	if found, ok := guardFlag.text(text); ok {
		return HumanGuardOff, ospath.Strip(found)
	}
	if found, ok := userScript.text(text); ok {
		return HumanUserScript, found
	}
	for _, combo := range boardRx {
		var hits []string
		for _, x := range combo {
			found, ok := x.text(text)
			if !ok {
				break
			}
			hits = append(hits, found)
		}
		if len(hits) == len(combo) {
			return HumanBoard, strings.Join(hits, " … ")
		}
	}
	if option, ok := expandedOption.first(ticked); ok && userAlone.search(ticked) {
		rest := string([]rune(ticked)[option.Start:])
		return HumanExpanded, fields(rest)[0]
	}
	return HumanNone, ""
}

// removeAll は Python の x.sub("", s)。
func removeAll(x rx, s string) string {
	if x.re == nil {
		return s
	}
	runes := []rune(s)
	var b strings.Builder
	last := 0
	for _, f := range x.all(s) {
		b.WriteString(string(runes[last:f.Start]))
		last = f.End
	}
	b.WriteString(string(runes[last:]))
	return b.String()
}

// 文面ファイルの識別子と欄。
const (
	textExpanded   = "DENY_TICKET_APPROVAL_CLI__EXPANDED"
	textBoard      = "DENY_TICKET_APPROVAL_CLI__BOARD"
	textUserScript = "DENY_TICKET_APPROVAL_CLI__USER_SCRIPT"
	textGuardOff   = "DENY_TICKET_APPROVAL_CLI__GUARD_OFF"
	valueFound     = "found"
)

// HumanMessage は HumanPathForm が見つけた形で止めた文。
func HumanMessage(root string, kind HumanKind, found string) string {
	values := texts.Values{valueFound: found}
	switch kind {
	case HumanGuardOff:
		return texts.Say(root, textGuardOff, values, nil)
	case HumanUserScript:
		return texts.Say(root, textUserScript, values, nil)
	case HumanExpanded:
		return texts.Say(root, textExpanded, values, nil)
	}
	// 取り込みの形は、承認と残った指摘の対応方針の文に当たらない。
	lowered := folder.String(found)
	why := "approval"
	switch {
	case strings.Contains(lowered, "mirror"):
		why = "mirror"
	case strings.Contains(lowered, "handoff"):
		why = "handoff"
	case strings.HasPrefix(lowered, "--via"):
		why = "via"
	}
	return texts.Say(root, textBoard, values, texts.Variants{"why": why})
}
