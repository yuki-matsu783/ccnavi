package docs

import (
	"io"
	"strconv"
	"strings"
	"unicode"

	"ccnavi/internal/catalog/docindex"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// jsonIndent は `--format json` の字下げ（json.dumps の indent=2）。
const jsonIndent = 2

// charWidth は 1 字の見た目の幅を返す。東アジアの全角（W・F）は 2、結合文字と書式の制御は 0 と数える。
func charWidth(r rune) int {
	if pyfmt.Combining(r) != 0 || unicode.In(r, unicode.Mn, unicode.Me, unicode.Cf) {
		return 0
	}
	switch pyfmt.EastAsianWidth(r) {
	case "W", "F":
		return 2
	}
	return 1
}

// displayWidth は文字列の見た目の幅。
func displayWidth(s string) int {
	n := 0
	for _, r := range s {
		n += charWidth(r)
	}
	return n
}

func pad(s string, w int) string { return s + strings.Repeat(" ", max(0, w-displayWidth(s))) }

// oneLine は表と詳しい形に出す値を 1 行にまとめる（改行で行が分かれないように）。
func oneLine(s string) string {
	if !strings.ContainsAny(s, "\n\r") {
		return s
	}
	return ospath.Squeeze(s)
}

func countLine(shown, matched, total int) string {
	line := "matched=" + strconv.Itoa(matched)
	if shown != matched {
		line += " shown=" + strconv.Itoa(shown)
	}
	return line + " total=" + strconv.Itoa(total) + "\n"
}

// render は hits を f の形で書く。
func render(out io.Writer, hits []docindex.Row, matched, total int, f Format) {
	var b strings.Builder
	switch f {
	case formatCount:
		b.WriteString(countLine(len(hits), matched, total))
	case formatPath:
		for _, r := range hits {
			b.WriteString(r.ConceptID() + "\n")
		}
	case formatJSONL:
		for _, r := range hits {
			b.WriteString(r.Line() + "\n")
		}
	case FormatJSON:
		list := make([]any, len(hits))
		for i, r := range hits {
			list[i] = r.JSON()
		}
		s, _ := pyjson.Dumps(list, pyjson.Options{RawUnicode: true, Indent: jsonIndent})
		b.WriteString(s + "\n")
	case formatDetail:
		for _, r := range hits {
			b.WriteString("- " + r.ConceptID() + "\n" +
				"  type       : " + oneLine(str(r, fieldType)) + "\n" +
				"  title      : " + oneLine(str(r, fieldTitle)) + "\n" +
				"  description: " + oneLine(str(r, fieldDescription)) + "\n" +
				"  tags       : " + oneLine(strings.Join(arr(r, fieldTags), ", ")) + "\n" +
				"  keywords   : " + oneLine(strings.Join(arr(r, fieldKeywords), ", ")) + "\n" +
				"  mtime      : " + r.Mtime() + "\n")
		}
	default:
		// 表の形。type と concept_id の列は、全角を 2 桁と数えた表示幅で桁をそろえる。
		types := make([]string, len(hits))
		tw, cw := 0, 0
		for i, r := range hits {
			types[i] = oneLine(str(r, fieldType))
			tw = max(tw, displayWidth(types[i]))
			cw = max(cw, displayWidth(r.ConceptID()))
		}
		for i, r := range hits {
			line := pad(types[i], tw) + "  " + pad(r.ConceptID(), cw) + "  " + oneLine(str(r, fieldTitle))
			b.WriteString(ospath.Rstrip(line) + "\n")
		}
	}
	io.WriteString(out, b.String())
}
