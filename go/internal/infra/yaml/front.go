package yaml

import (
	"strings"

	"ccnavi/internal/infra/ospath"
)

// FrontFence は md の頭の frontmatter を囲う行（開きと閉じ）。
const FrontFence = "---"

// frontDocEnd は YAML の文書の終わりの行。閉じとして受ける読み手がある。
const frontDocEnd = "..."

// FrontStyle は囲いの行の読み方。Python の読み手ごとに違い、同じ md でも答えが変わるので、呼び手が選ぶ。
type FrontStyle struct {
	// Indented は囲いの行の頭の空白を許す（line.strip() で比べる）。偽なら末尾の空白だけを除く（line.rstrip()）。
	Indented bool
	// DocEnd は `...` も閉じとして受ける。
	DocEnd bool
}

// FrontState は頭の frontmatter の有り様。
type FrontState int

const (
	// FrontAbsent は 1 行目が囲いでない（空の文書も）。
	FrontAbsent FrontState = iota
	// FrontUnclosed は開いたが閉じの行が無い。
	FrontUnclosed
	// FrontClosed は閉じまである。
	FrontClosed
)

// FrontBody は text の頭の frontmatter の中身（囲いの間の行を "\n" で継いだもの）と有り様を返す。
// 中身は閉じまであるときだけ返す。読む長さの上限・BOM・復号は呼び手が先に済ませる（読み手ごとに違う）。
func FrontBody(text string, s FrontStyle) (string, FrontState) {
	trim := ospath.Rstrip
	if s.Indented {
		trim = ospath.Strip
	}
	lines := ospath.Splitlines(text)
	if len(lines) == 0 || trim(lines[0]) != FrontFence {
		return "", FrontAbsent
	}
	for i := 1; i < len(lines); i++ {
		if line := trim(lines[i]); line == FrontFence || (s.DocEnd && line == frontDocEnd) {
			return strings.Join(lines[1:i], "\n"), FrontClosed
		}
	}
	return "", FrontUnclosed
}
