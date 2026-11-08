package pyfmt

import (
	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
	"golang.org/x/text/width"
)

var folder = cases.Fold()

// Casefold は Python の str.casefold()。パスでない語（識別子・コマンドの語・ブランチ名）を比べるキーに使う。
// パスは区切りも機械に合わせる pathcase.Fold を通す。
func Casefold(s string) string { return folder.String(s) }

// NFKC は Python の unicodedata.normalize("NFKC", s)。全角の英数字や合字を素の字にそろえる。
func NFKC(s string) string { return norm.NFKC.String(s) }

// NFC は Python の unicodedata.normalize("NFC", s)。macOS の HFS+ が NFD で返すディレクトリの名前を、
// 識別子と同じ表記にそろえるのにも使う。
func NFC(s string) string { return norm.NFC.String(s) }

// NFD は Python の unicodedata.normalize("NFD", s)。正準分解して、正準等価の違い（合成済みの字と分解した字）をそろえる。
func NFD(s string) string { return norm.NFD.String(s) }

// Combining は Python の unicodedata.combining(r)（正準結合クラス。結合しない字は 0）。
func Combining(r rune) int { return int(norm.NFD.PropertiesString(string(r)).CCC()) }

// EastAsianWidth は Python の unicodedata.east_asian_width(r)（"W"・"F"・"H"・"Na"・"A"・"N"）。
func EastAsianWidth(r rune) string {
	switch width.LookupRune(r).Kind() {
	case width.EastAsianWide:
		return "W"
	case width.EastAsianFullwidth:
		return "F"
	case width.EastAsianHalfwidth:
		return "H"
	case width.EastAsianNarrow:
		return "Na"
	case width.EastAsianAmbiguous:
		return "A"
	}
	return "N"
}
