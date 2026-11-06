// Package fields はチケットの frontmatter の欄を、読み直さずに行単位で書き換え・読み取る。ユーザが書いたコメントや
// `|` のブロックを残し、親のブランチの diff に余計な変更を出さないため。実行後チェックが「この変更は ccnavi の
// サブコマンドが書いたぶんか」を内容で答える正規化（ScriptShape）も、同じ行単位の切り出しで持つ。
//
// Python からの対応: ticket_fields をそのまま移した。set_fields → SetFields、set_mr → SetMR（MR_* → MRResult）、
// plain_int_field → PlainIntField、mr_is_empty → MREmpty、script_fields_set → ScriptFieldsSet、
// script_shape → ScriptShape。_yaml_scalar は infra の yaml.Quoted を使う。
package fields

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/tickets/model/ticket"
)

// MRKey は MR の番号の欄。
const MRKey = "mr"

// MRResult は SetMR の結果。
type MRResult string

const (
	MRSet        MRResult = "set"        // 空だったので書いた
	MRSame       MRResult = "same"       // 同じ番号が既にあった（何もしない）
	MRDiffers    MRResult = "differs"    // 違う値が既にあった（書かない）
	MRUnreadable MRResult = "unreadable" // frontmatter が無い・閉じていない
)

// valueParseLimit は値なしかを YAML で読む表記の長さの上限。承認が置く空の欄は数文字で、巨大な表記を YAML に
// 掛けると実行後チェックが止まる。
const valueParseLimit = 1024

// split は全文を行にし、閉じの `---` の行の番号を返す。frontmatter の形でなければ偽。
func split(text string) ([]string, int, bool) {
	lines := ospath.Splitlines(text)
	if len(lines) == 0 || ospath.Strip(lines[0]) != ticket.Fence {
		return lines, 0, false
	}
	for i := 1; i < len(lines); i++ {
		if ospath.Strip(lines[i]) == ticket.Fence {
			return lines, i, true
		}
	}
	return lines, 0, false
}

func indented(line string) bool { return strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t") }

// keyOf は行の `:` の前を欄の名前として読む（`:` が無ければ行の全体）。
func keyOf(line string) string {
	key, _, _ := strings.Cut(line, ":")
	return ospath.Strip(key)
}

// valueOf は行の `:` の後ろ（無ければ空と偽）。
func valueOf(line string) (string, bool) {
	_, value, ok := strings.Cut(line, ":")
	return value, ok
}

func join(lines []string, text string) string {
	out := strings.Join(lines, "\n")
	if strings.HasSuffix(text, "\n") {
		out += "\n"
	}
	return out
}

// SetFields は frontmatter の、スクリプトが書く欄（ticket.ScriptFields）だけを行単位で書き換える。無い欄は閉じの
// `---` の前に足す。ほかの欄は渡しても書かない。fields は書く順に並べる。
func SetFields(text string, fields [][2]string) string {
	lines, end, ok := split(text)
	if !ok {
		return text
	}
	var pending [][2]string
	for _, f := range fields {
		if slices.Contains(ticket.ScriptFields, f[0]) {
			pending = append(pending, f)
		}
	}
	for i := 1; i < end; i++ {
		key := keyOf(lines[i])
		at := slices.IndexFunc(pending, func(f [2]string) bool { return f[0] == key })
		if at >= 0 && !indented(lines[i]) {
			lines[i] = key + ": " + yaml.Quoted(pending[at][1])
			pending = slices.Delete(pending, at, at+1)
		}
	}
	for _, f := range pending {
		lines = slices.Insert(lines, end, f[0]+": "+yaml.Quoted(f[1]))
		end++
	}
	return join(lines, text)
}

// fieldLine は欄の行の番号（字下げした行は数えない）。無ければ -1。
func fieldLine(lines []string, end int, key string) int {
	for i := 1; i < end; i++ {
		if !indented(lines[i]) && keyOf(lines[i]) == key {
			return i
		}
	}
	return -1
}

func continued(lines []string, i, end int) bool { return i+1 < end && indented(lines[i+1]) }

// PlainIntField は欄が `key: 12` の 1 行だけの正の整数ならその値と真。無い・空・引用符つき・`!12`・続きの行・
// 注釈つきは偽（ccnavi が書く形だけを読む）。
func PlainIntField(text, key string) (int, bool) {
	lines, end, ok := split(text)
	if !ok {
		return 0, false
	}
	i := fieldLine(lines, end, key)
	if i < 0 || continued(lines, i, end) {
		return 0, false
	}
	value, _ := valueOf(lines[i])
	value = ospath.Strip(value)
	if !isASCIIDigits(value) {
		return 0, false
	}
	n, err := pyfmt.ParseInt(value)
	if err != nil || n <= 0 {
		return 0, false
	}
	return n, true
}

func isASCIIDigits(s string) bool {
	return s != "" && strings.Trim(s, "0123456789") == ""
}

// MREmpty は MR の欄が無いか、値なしか。続きの行があれば値ありとする（ScriptFieldsSet と同じ読み方）。
// frontmatter が無ければ偽。
func MREmpty(text string) bool {
	lines, end, ok := split(text)
	if !ok {
		return false
	}
	i := fieldLine(lines, end, MRKey)
	if i < 0 {
		return true
	}
	if continued(lines, i, end) {
		return false
	}
	value, _ := valueOf(lines[i])
	return !hasValue(value)
}

// heldNumber は欄の値の表記が表す MR の番号（`12`・`"!12"`・`#12`）。読めなければ偽。
func heldNumber(raw string) (int, bool) {
	if len([]rune(raw)) > valueParseLimit {
		return 0, false
	}
	value, err := yaml.Load(raw)
	if err != nil {
		return 0, false
	}
	if _, isBool := value.(bool); isBool {
		return 0, false
	}
	if s, ok := value.(string); ok {
		value = ospath.Strip(strings.TrimLeft(ospath.Strip(s), "!#"))
	}
	s := pyfmt.Str(value)
	if !isASCIIDigits(s) {
		return 0, false
	}
	n, err := pyfmt.ParseInt(s)
	return n, err == nil
}

// SetMR は mr: が空のときだけ番号を書く。同じ番号（`!12`・`#12` も）が既にあれば MRSame、違う値なら MRDiffers で
// 書かない。行単位で書き、コメントや本文は触らない。
func SetMR(text string, number int) (string, MRResult) {
	lines, end, ok := split(text)
	if !ok {
		return text, MRUnreadable
	}
	i := fieldLine(lines, end, MRKey)
	line := MRKey + ": " + pyfmt.Str(number)
	switch {
	case i >= 0 && !MREmpty(text):
		value, _ := valueOf(lines[i])
		if held, ok := heldNumber(value); ok && held == number {
			return text, MRSame
		}
		return text, MRDiffers
	case i >= 0:
		lines[i] = line
	default:
		lines = slices.Insert(lines, end, line)
	}
	return join(lines, text), MRSet
}

// ScriptFieldsSet はその版が既に値を持っている、スクリプトの欄。正規化した内容を照合する側が「除いてよい欄」を
// 決めるのに使う。除いてよいのはコミット済みの版がまだ持っていない欄だけ（サブコマンドはどれも 1 度しか書かない
// ので、既に値がある欄が変わったのならサブコマンドが書いたものではない。とくに base_sha は着手の基準点）。
func ScriptFieldsSet(text string) []string {
	lines, end, ok := split(text)
	if !ok {
		return nil
	}
	var held []string
	for i := 1; i < end; i++ {
		line := lines[i]
		value, has := valueOf(line)
		if indented(line) || !has {
			continue
		}
		// 続きがあれば値は 2 行目以降にあるかもしれない（`|` や `# c` の次の行など）。
		key := keyOf(line)
		if slices.Contains(ticket.ScriptFields, key) && (continued(lines, i, end) || hasValue(value)) {
			held = append(held, key)
		}
	}
	return held
}

// hasValue は欄の 1 行目の値の表記が空でない値を持つか。承認は欄を `started_at: ""` の形で置くので、字面ではなく
// YAML として読んで空文字と null を値なしとする。読めない・長すぎる表記は値ありとする（除く欄が減る側）。
func hasValue(raw string) bool {
	if ospath.Strip(raw) == "" {
		return false
	}
	if len([]rune(raw)) > valueParseLimit {
		return true
	}
	value, err := yaml.Load(raw)
	if err != nil {
		return true
	}
	return value != nil && value != ""
}

// ScriptShape は frontmatter を持つチケットなら、drop の欄（ticket.ScriptFields の部分集合）とその値として続く
// 字下げの行を除いて正規化した内容と真。無ければ偽。台帳ではなく内容で見るのは、承認とマーカーが親のブランチに
// 乗って別の機械へ届くため。切り出し方は SetFields と揃える（揃えないと、書いた直後の内容が「スクリプトが書いて
// いない形」に見える）。
func ScriptShape(text string, drop []string) (string, bool) {
	lines, end, ok := split(text)
	if !ok {
		return "", false
	}
	var kept []string
	dropping := false
	for i, line := range lines {
		if i < 1 || i >= end {
			kept = append(kept, line)
			continue
		}
		if dropping && indented(line) {
			continue
		}
		dropping = !indented(line) && slices.Contains(drop, keyOf(line))
		if !dropping {
			kept = append(kept, line)
		}
	}
	return strings.Join(kept, "\n"), true
}
