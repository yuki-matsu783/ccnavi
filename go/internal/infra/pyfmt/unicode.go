package pyfmt

import (
	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
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
