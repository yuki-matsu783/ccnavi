package rules

import (
	"errors"
	"fmt"
	"strings"
	"unicode"

	"ccnavi/internal/infra/pyre"
)

// MaxRootLen はこの長さ以上のルートでは {!root} を展開しない。Python 3.12 ではこの形の入れ子は 493 字で
// 組み立てが RecursionError になり、その半分以下で止める（Windows の MAX_PATH は 260 で、256 字のルートは
// その時点で実用にならない）。
const MaxRootLen = 256

// matchEverything は組み立てられなかった deny・ask に持たせる、何にでも当たる式。
const matchEverything = "^"

// unsupported はルールの regex に書けない形。書ける範囲を狭く保ち、組み合わせ爆発を起こしやすい書き方を断つ。
var unsupported = []struct{ literal, label string }{
	{"(?=", "先読み"},
	{"(?!", "否定先読み"},
	{"(?<=", "後読み"},
	{"(?<!", "否定後読み"},
}

// unsupportedIn は書けない形の名前。無ければ空。`\` に 1〜9 が続けば後方参照と数える（`\\1` も含む。Python と同じ）。
func unsupportedIn(expression string) string {
	for _, u := range unsupported {
		if strings.Contains(expression, u.literal) {
			return u.label
		}
	}
	for i := 0; i+1 < len(expression); i++ {
		if expression[i] == '\\' && expression[i+1] >= '1' && expression[i+1] <= '9' {
			return "後方参照"
		}
	}
	return ""
}

// generatedQuantifier は展開した式に繰り返し（`*`・`+`・`{数字`）があるか。逃がした字（`\*`）は数えない。
func generatedQuantifier(expression string) bool {
	runes := []rune(expression)
	for i, r := range runes {
		escaped := i > 0 && runes[i-1] == '\\'
		if escaped {
			continue
		}
		if r == '*' || r == '+' {
			return true
		}
		if r == '{' && i+1 < len(runes) && unicode.IsDigit(runes[i+1]) {
			return true
		}
	}
	return false
}

// skipsAnywhere は {!root} の直後に続けてよい書き始め（任意の位置まで進む書き方）。
var skipsAnywhere = []string{".*", ".+", `[\s\S]*`, `[\s\S]+`}

// compiled は式 1 つを組み立てた結果。refused は {!root} を展開できなかった理由。
type compiled struct {
	re      *pyre.Regexp
	err     *Problem
	note    *Problem
	refused string
}

// compileExpr は glob か regex の 1 つを判定に使う式にする。本体・except・when の個別条件がこの 1 本を通る
// （{root} の置き換え、{!root} の展開、先読み・後方参照の禁止、大文字小文字の無視を食い違わせないため）。
// label は苦情の頭に付ける語（本体なら空、except なら `except[0] の `）。
func compileExpr(glob, regex, root, name, label string) compiled {
	fail := func(detail string) compiled {
		return compiled{err: &Problem{Severity: Error, Rule: name, Detail: label + detail}}
	}
	usesRoot := strings.Contains(regex, RootPlaceholder) || strings.Contains(glob, RootPlaceholder)
	if usesRoot && root == "" {
		return fail("`" + RootPlaceholder + "` を使うにはワークスペースルートが必要です")
	}
	if strings.Contains(glob, NotRootPlaceholder) {
		return fail("`" + NotRootPlaceholder + "` は `glob` には書けない。`regex: '^" + NotRootPlaceholder + "'` と書いてください")
	}
	var note *Problem
	usesNotRoot := strings.Contains(regex, NotRootPlaceholder)
	if usesNotRoot {
		if misplaced := notRootPlacement(regex); misplaced != "" {
			return fail(misplaced)
		}
		if root == "" {
			return fail("`" + NotRootPlaceholder + "` を使うにはワークスペースルートが必要です")
		}
		if suffix := notRootSuffix(regex); suffix != "" {
			note = &Problem{Severity: Warn, Rule: name, Detail: label + suffix}
		}
	}
	var expression string
	if regex != "" {
		if u := unsupportedIn(regex); u != "" {
			return fail(u + "は利用できない")
		}
		expression = regex
		if usesRoot {
			expression = strings.ReplaceAll(regex, RootPlaceholder, RootPattern(root))
		}
		if usesNotRoot {
			expanded, refused := expandNotRoot(expression, root)
			if refused != "" {
				return compiled{note: note, refused: refused}
			}
			expression = expanded
		}
	} else {
		// glob は文字列全体に当たる約束なので先頭に固定する。固定しないと照合を始める位置ごとに末尾まで読み、
		// `*git push*` のような式が長さの 2 乗で遅くなる。
		written := glob
		if usesRoot {
			written = strings.ReplaceAll(glob, RootPlaceholder, RootGlob(root))
		}
		expression = "^" + pyre.TranslateGlob(written)
	}
	re, err := pyre.Compile(expression, pyre.IgnoreCase)
	switch {
	case err == nil:
		return compiled{re: re, note: note}
	case errors.Is(err, pyre.ErrTooDeep):
		if usesNotRoot {
			return compiled{note: note, refused: "入れ子が深すぎて組み立てられない"}
		}
		return fail("入れ子が深すぎて組み立てられない")
	}
	return fail("正規表現として組み立てられない: " + err.Error())
}

// CompileCondition はルールの外で glob か regex の 1 つを、ルールと同じ読み方で組み立てる（ターン分離の
// ゲートなど、ルールファイルに書かない組み込みの宣言のため）。{root} と {!root} は使えない。
// 組み立てられなければ式は nil で、理由を返す。
func CompileCondition(glob, regex, name string) (*pyre.Regexp, string) {
	built := compileExpr(glob, regex, "", name, "")
	if built.err != nil {
		return nil, built.err.Detail
	}
	if built.re == nil {
		if built.refused != "" {
			return nil, built.refused
		}
		return nil, "式を組み立てられない"
	}
	return built.re, ""
}

// notRootPlacement は {!root} の置き場所への苦情。置けるのは `^` の直後に 1 回だけで、直後に量化子は書けない
// （展開結果ごと省けると「外だけを止める」式がどの対象にも当たる）。
func notRootPlacement(expression string) string {
	if strings.Count(expression, NotRootPlaceholder) > 1 {
		return "`" + NotRootPlaceholder + "` は 1 つの式に 1 回だけ書ける"
	}
	head := "^" + NotRootPlaceholder
	if !strings.HasPrefix(expression, head) {
		return "`" + NotRootPlaceholder + "` は `^` の直後にしか書けない。" +
			"展開されるのは「先頭から、外だと確定するところまで」を読み進める式で、" +
			"前に何かを置くと意味を持たない"
	}
	rest := expression[len(head):]
	if rest != "" && strings.ContainsRune("?*+{", rune(rest[0])) {
		return "`" + NotRootPlaceholder + "` の直後に量化子（`" + rest[:1] + "`）は書けない。" +
			"展開結果ごと省略したり繰り返したりできてしまい、" +
			"「外だけを止める」はずの式がどの対象にもヒットするようになる"
	}
	return ""
}

// notRootSuffix は {!root} の後ろに続く式への苦情（warn）。展開した式が読み終える位置はパスの区切りとは
// 限らないので、区切りを前提にした書き方は意図どおりに動かない。
func notRootSuffix(expression string) string {
	rest := expression[len("^"+NotRootPlaceholder):]
	if rest == "" {
		return ""
	}
	for _, s := range skipsAnywhere {
		if strings.HasPrefix(rest, s) {
			return ""
		}
	}
	shown := []rune(rest)
	if len(shown) > 16 {
		shown = shown[:16]
	}
	return "`" + NotRootPlaceholder + "` の直後に `" + string(shown) + "` が続いている。" +
		"展開した式がマッチし終える位置はパスの区切りとは限らないので、区切りを前提にした" +
		"表記は意図どおりに動かない。任意の位置から続けるなら `.*` で受けてください"
}

// expandNotRoot は {!root} を展開した式。組み立てられなければ (元の式, 理由)。
func expandNotRoot(expression, root string) (string, string) {
	real := RealRoot(root)
	if real == "" {
		return expression, "ワークスペースルートが区切りだけで、「外」を決められない"
	}
	if n := len([]rune(real)); n >= MaxRootLen {
		return expression, fmt.Sprintf("ワークスペースルートが長すぎて `%s` を組み立てられない（%d 字 / 上限 %d 字）",
			NotRootPlaceholder, n, MaxRootLen-1)
	}
	generated := NotRootPattern(real)
	// 生成した部分が約束（繰り返しも先読みも含まない）を守っているかを見る。いまの組み立てでは発火しない備え。
	if generatedQuantifier(generated) || unsupportedIn(generated) != "" {
		return expression, "展開した式が契約を守っていない（組み立ての不具合）"
	}
	return strings.ReplaceAll(expression, NotRootPlaceholder, generated), ""
}

// degrade は {!root} を組み立てられなかったときの扱い。deny・ask は match の全部に当てて止め、
// allow はルールごと使わない（当たったものとして扱うと、ccnavi が判定を返さない範囲が広がる）。
// 書いた文面は残し、止めた説明は DegradedMessage に持つ。
func degrade(rule *Rule, section Decision, name, detail string) (*Rule, Problem) {
	problem := Problem{Severity: Error, Rule: name, Detail: detail}
	if section == Allow {
		return nil, problem
	}
	rule.Compiled, _ = pyre.Compile(matchEverything, 0)
	rule.DegradedMessage = detail + "。保護が消えるのを避けるため、ルール '" + DegradedRuleName + "' が見るツール" +
		"（" + rule.Match + "）を止めています。ワークスペースをもっと浅い場所へ移すか、" +
		"ユーザに、このルールが書かれているルールファイルから削除してもらってください。"
	return rule, problem
}
