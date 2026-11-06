package flow

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
)

// フローの値はユーザかエージェントが書いたもので、文面に混ぜる前に必ずここを通す。

// textLimit は 1 行に載せる文の長さの上限（符号位置）。
const textLimit = 120

// 文に ccnavi の接頭辞を真似させない。`[` の直後が `ccnavi` で始まる括弧（大文字小文字・空白・結合文字・
// 書式の制御は無視する）は亀甲括弧にする。見た目だけが似た別の字（キリル文字・全角の括弧）には対応しない。
const (
	badgeWord  = "ccnavi"
	badgeOpen  = "〔"
	badgeClose = "〕"
	badgeReach = 64 // 括弧の閉じを探す範囲（元の文字数）
	fenceShown = "〔区切りに似た文〕"
)

// FencePhrases は案内の区切りの行の文。フローの文の中に出たら置き換える。プロジェクトのスキルの目録の
// 区切り（catalog）も同じく置き換えるので、目録の区切りはここに並べた文で書く。
var FencePhrases = []string{
	"ここから合意済みのフローの本文",
	"フローの本文ここまで",
	"ここからプロジェクトのスキルの目録",
	"目録ここまで",
}

// lineBreaks は Clean が空白 1 つにする改行・タブ（制御文字の中で消さずに残すもの）。
const lineBreaks = "\r\n\t\v\f\u0085  "

// Clean は文脈に載せる値から、改行と制御文字（書式の制御も）を除く。改行・タブは空白にする。
func Clean(text string) string {
	var b strings.Builder
	for _, r := range text {
		switch {
		case strings.ContainsRune(lineBreaks, r):
			b.WriteByte(' ')
		case unicode.In(r, unicode.Cc, unicode.Cf):
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

// textOf は文字列か数だけを文にする（Python の _text）。リストやマップは中身を辿らない。
// 整数の値の小数（1.0）は整数の表記にする（ボードの JavaScript の String と揃える）。
func textOf(v any) string {
	switch x := v.(type) {
	case bool:
		return ""
	case float64:
		if x == math.Trunc(x) && math.Abs(x) < 1e21 {
			if x == 0 {
				return "0"
			}
			return strconv.FormatFloat(x, 'f', 0, 64)
		}
		return pyfmt.Str(x)
	case string:
		return x
	case int, *big.Int:
		return pyfmt.Str(x)
	}
	return ""
}

// Line は 1 行にまとめて切る（Python の _line）。ccnavi の接頭辞と案内の区切りは真似させない。
func Line(v any) string {
	text, ok := v.(string)
	if !ok {
		if err, isErr := v.(error); isErr {
			text = err.Error()
		} else {
			text = textOf(v)
		}
	}
	shown := strings.Join(strings.FieldsFunc(Clean(pyfmt.Head(text, textLimit*8)), ospath.IsSpace), " ")
	shown = neutral(shown)
	if utf8.RuneCountInString(shown) > textLimit {
		shown = pyfmt.Head(shown, textLimit) + "…"
	}
	return shown
}

var (
	marks   = []*unicode.RangeTable{unicode.Mn, unicode.Mc, unicode.Me}
	ignored = []*unicode.RangeTable{unicode.Mn, unicode.Mc, unicode.Me, unicode.Cf, unicode.Cc, unicode.Zs}
)

// skeleton は比べるための表記と、表記の 1 字ごとの元の位置（符号位置）。1 字ずつ正準分解して正準等価の
// 違いをそろえ、結合文字・書式の制御・制御文字・空白を除き、大文字小文字をそろえる。
func skeleton(text []rune) ([]rune, []int) {
	var chars []rune
	var origin []int
	for i, ch := range text {
		for _, part := range norm.NFD.String(string(ch)) {
			if ospath.IsSpace(part) || unicode.In(part, ignored...) {
				continue
			}
			// 1 字の大文字小文字をそろえる（"/" は含まないので、Windows の区切りの読み替えは効かない）。
			for _, folded := range pathcase.Fold(string(part)) {
				chars = append(chars, folded)
				origin = append(origin, i)
			}
		}
	}
	return chars, origin
}

// indexFrom は hay の from 以降で needle が始まる位置。無ければ -1。
func indexFrom(hay, needle []rune, from int) int {
	for i := from; i+len(needle) <= len(hay); i++ {
		if hasPrefixAt(hay, needle, i) {
			return i
		}
	}
	return -1
}

func hasPrefixAt(hay, needle []rune, at int) bool {
	if at < 0 || at+len(needle) > len(hay) {
		return false
	}
	for j, r := range needle {
		if hay[at+j] != r {
			return false
		}
	}
	return true
}

// neutral はフローの文が ccnavi の知らせや案内の区切りに見えないようにする。
func neutral(s string) string {
	text := []rune(s)
	skel, origin := skeleton(text)
	replace := map[int]string{}
	word := []rune(badgeWord)
	open, closing := []rune("["), []rune("]")
	for at := indexFrom(skel, open, 0); at >= 0; at = indexFrom(skel, open, at+1) {
		if !hasPrefixAt(skel, word, at+1) {
			continue
		}
		start := origin[at]
		replace[start] = badgeOpen
		if c := indexFrom(skel, closing, at+1); c >= 0 && origin[c]-start <= badgeReach {
			replace[origin[c]] = badgeClose
		}
	}
	for _, phrase := range FencePhrases {
		needle, _ := skeleton([]rune(phrase))
		for at := indexFrom(skel, needle, 0); at >= 0; at = indexFrom(skel, needle, at+len(needle)) {
			first, last := origin[at], origin[at+len(needle)-1]
			for last+1 < len(text) && unicode.In(text[last+1], marks...) {
				last++ // 末尾の文字に付いた結合文字（分解した濁点など）も置き換えに含める
			}
			replace[first] = fenceShown
			for i := first + 1; i <= last; i++ {
				replace[i] = ""
			}
		}
	}
	if len(replace) == 0 {
		return s
	}
	var b strings.Builder
	for i, r := range text {
		if v, ok := replace[i]; ok {
			b.WriteString(v)
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// asMap は v がマップならそれ、違えば nil（nil の *yaml.Map の取り出しは既定の値を返す）。
func asMap(v any) *yaml.Map {
	m, _ := v.(*yaml.Map)
	return m
}

func asList(v any) []any {
	l, _ := v.([]any)
	return l
}

// mapsIn は v が列なら、その中のマップだけ。
func mapsIn(v any) []*yaml.Map {
	var out []*yaml.Map
	for _, item := range asList(v) {
		if m, ok := item.(*yaml.Map); ok {
			out = append(out, m)
		}
	}
	return out
}

// capped は parts を上限で切ったあとの列に、残りの数の 1 項を足す。
func capped(parts []string, total int) []string {
	if total > len(parts) {
		return append(parts, "…ほか "+strconv.Itoa(total-len(parts))+" 件")
	}
	return parts
}

// head は列の先頭 n 件。
func head[T any](items []T, n int) []T {
	if len(items) > n {
		return items[:n]
	}
	return items
}
