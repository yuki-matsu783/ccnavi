package templates

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
)

// 取り込みの差分で見えなくなる文字（制御文字・書式文字・行と段落の区切り）。改行とタブは見えるので除く。

// hiddenShown は苦情に並べる文字の数。
const hiddenShown = 8

func hidden(r rune) bool { return r != '\n' && r != '\t' && hiddenCategory(r) }

// HiddenChars は text に含まれる、読み違えを誘う文字（出てきた順で重複なし）。
func HiddenChars(text string) []rune {
	var out []rune
	seen := map[rune]bool{}
	for _, r := range text {
		if hidden(r) && !seen[r] {
			seen[r] = true
			out = append(out, r)
		}
	}
	return out
}

// HiddenProblem は text に読み違えを誘う文字があれば error にする苦情の文。無ければ空。
func HiddenProblem(text string) string {
	found := HiddenChars(text)
	if len(found) == 0 {
		return ""
	}
	more := ""
	if len(found) > hiddenShown {
		found, more = found[:hiddenShown], " ほか"
	}
	shown := make([]string, len(found))
	for i, r := range found {
		shown[i] = EscapeChar(r)
	}
	return "制御文字・書式文字が含まれている（" + strings.Join(shown, " ") + more + "）。" +
		"右から左への上書きや幅ゼロの文字、復帰などは、読み違えを誘うので使えない。削除します。"
}

// EscapeChar は 1 文字を \uXXXX（BMP の外は \UXXXXXXXX）にする。
func EscapeChar(r rune) string {
	if r <= 0xFFFF {
		return fmt.Sprintf(`\u%04x`, r)
	}
	return fmt.Sprintf(`\U%08x`, r)
}

// Reveal は表示用に、制御文字・書式文字を \uXXXX に置き換える。書く中身には使わない。
func Reveal(text string) string {
	var b strings.Builder
	for _, r := range text {
		if hidden(r) {
			b.WriteString(EscapeChar(r))
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// strs は入れ子の中の文字列（キーも）を全部。集合は辿らない（Python の _strings と同じ）。
func strs(v any, out []string) []string {
	switch x := v.(type) {
	case string:
		out = append(out, x)
	case *yaml.Map:
		for k, inner := range x.All() {
			out = strs(k, out)
			out = strs(inner, out)
		}
	case []any:
		for _, inner := range x {
			out = strs(inner, out)
		}
	case yaml.Tuple:
		for _, inner := range x {
			out = strs(inner, out)
		}
	}
	return out
}

// line は空白をつぶして 1 行にする（Python の " ".join(str(v).split())）。
func line(v any) string {
	return strings.Join(strings.FieldsFunc(pyfmt.Str(v), ospath.IsSpace), " ")
}
