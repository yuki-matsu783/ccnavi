package handoff

import (
	"strings"

	"ccnavi/internal/infra/difflib"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/proposal/templates"
)

// diffContext は差分に添える前後の行数。
const diffContext = 3

// noEOL は差分で、末尾に改行が無いことを言う行。
const noEOL = "\\ 末尾に改行がありません"

const (
	msgInvisible = "差分が見えないのに、バイト列が取り込み先と違う。見えない違いは取り込まない"
	devNull      = "/dev/null"
)

// visibleLines は差分に出す行。改行コード・BOM・制御文字・ゼロ幅の文字・結合文字は \uXXXX にする。
// 末尾に改行が無ければ、そう言う行を足す（改行だけの違いも差分に出す）。
func visibleLines(raw []byte) []string {
	if raw == nil {
		return nil
	}
	lines := strings.Split(decodeReplace(raw, false), "\n")
	tail := lines[len(lines)-1]
	lines = lines[:len(lines)-1]
	out := make([]string, 0, len(lines)+2)
	for _, l := range lines {
		out = append(out, visible(l))
	}
	if tail != "" {
		out = append(out, visible(tail), noEOL)
	}
	return out
}

func visible(text string) string {
	var b strings.Builder
	for _, r := range text {
		if len(templates.HiddenChars(string(r))) > 0 || combining(r) {
			b.WriteString(templates.EscapeChar(r))
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// combining は unicodedata.combining(ch) != 0（正準結合クラスが 0 でない）。
func combining(r rune) bool { return pyfmt.Combining(r) != 0 }

func makeDiff(e *Entry) {
	if e.raw == nil || same(e.current, e.raw) {
		return
	}
	from := devNull
	if e.current != nil {
		from = "a/" + e.Rel
	}
	lines := difflib.UnifiedDiff(visibleLines(e.current), visibleLines(e.raw), from, "b/"+e.Rel, diffContext)
	if len(lines) == 0 {
		e.Errors = append(e.Errors, msgInvisible)
		return
	}
	e.Diff = strings.Join(lines, "\n")
}
