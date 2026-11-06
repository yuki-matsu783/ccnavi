package fields

import (
	"fmt"
	"strings"
)

// bestWidth は PyYAML の書き手が行を折る幅。
const bestWidth = 80

// escapes は PyYAML の ESCAPE_REPLACEMENTS。
var escapes = map[rune]string{
	0: "0", '\a': "a", '\b': "b", '\t': "t", '\n': "n", '\v': "v", '\f': "f", '\r': "r", 0x1B: "e",
	'"': `"`, '\\': `\`, 0x85: "N", 0xA0: "_", 0x2028: "L", 0x2029: "P",
}

// printable は allow_unicode の書き手がそのまま書く字か。
func printable(ch rune) bool {
	if strings.ContainsRune("\"\\\u0085\u2028\u2029\uFEFF", ch) {
		return false
	}
	return 0x20 <= ch && ch <= 0x7E || 0xA0 <= ch && ch <= 0xD7FF || 0xE000 <= ch && ch <= 0xFFFD
}

// Quoted は文字列を、YAML が文字列として読み戻せる二重引用符の表記にする。Python の
// yaml.safe_dump(value, allow_unicode=True, default_style='"') の 1 行目以降と同じ（80 字で折り、続きの行は
// 2 字下げる）。折れた表記は欄の行に複数行のまま入る。
//
// infra/yaml に書き出しが無いので、欄の値に要るこの形だけをここに写した。書き出しが infra に入れば移す。
func Quoted(value string) string {
	text := []rune(value)
	var b strings.Builder
	b.WriteByte('"')
	column := 1
	start := 0
	for end := 0; end <= len(text); end++ {
		ch := rune(-1)
		if end < len(text) {
			ch = text[end]
		}
		if ch < 0 || !printable(ch) {
			if start < end {
				b.WriteString(string(text[start:end]))
				column += end - start
				start = end
			}
			if ch >= 0 {
				var data string
				switch e, ok := escapes[ch]; {
				case ok:
					data = `\` + e
				case ch <= 0xFF:
					data = fmt.Sprintf(`\x%02X`, ch)
				case ch <= 0xFFFF:
					data = fmt.Sprintf(`\u%04X`, ch)
				default:
					data = fmt.Sprintf(`\U%08X`, ch)
				}
				b.WriteString(data)
				column += len(data)
				start = end + 1
			}
		}
		if 0 < end && end < len(text)-1 && (ch == ' ' || start >= end) && column+(end-start) > bestWidth {
			data := ""
			if start < end {
				data = string(text[start:end])
				start = end
			}
			b.WriteString(data + `\`)
			// 折った行は字下げ 2 で続ける（最上位の値の字下げ）。
			b.WriteString("\n  ")
			column = 2
			if text[start] == ' ' {
				b.WriteByte('\\')
				column++
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}
