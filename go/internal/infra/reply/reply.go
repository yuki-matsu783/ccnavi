// Package reply は、sh の段取りから呼ばれる動詞の答え（1 行 1 項目）を組み立てる。行の先頭の語で sh が分ける。
//
//   - `<名前> <値>`: sh が使う値。値に改行があれば、行ごとに同じ名前で出す（sh は ccnavi_val でつなぎ直す）。
//     改行を空白にして 1 行に収める値は Line で出す
//   - `out <行>`・`err <行>`: sh がそのまま標準出力・標準エラーに出す行
//   - `fail <識別子> <終了コード>` と `msg <行>`: sh が `<sh の名前>: <文>` を標準エラーに出して止める
//   - `exit <終了コード>`: sh が出力を済ませてから、その終了コードで終える
package reply

import (
	"strconv"
	"strings"
)

// Lines は答えの行を溜める。
type Lines struct{ b strings.Builder }

// Val は値を出す（空なら出さない）。
func (o *Lines) Val(key, v string) {
	if v == "" {
		return
	}
	for _, line := range strings.Split(v, "\n") {
		o.b.WriteString(key + " " + line + "\n")
	}
}

// Line は 1 行を出す。値は空白でつなぎ、改行は空白にする（答えの行に紛れさせない）。値が無ければ名前だけの行。
func (o *Lines) Line(key string, values ...string) {
	text := strings.NewReplacer("\r\n", " ", "\n", " ", "\r", " ").Replace(strings.Join(values, " "))
	if text == "" {
		o.b.WriteString(key + "\n")
		return
	}
	o.b.WriteString(key + " " + text + "\n")
}

// Out は標準出力に出す行。
func (o *Lines) Out(text string) { o.Val("out", text) }

// Err は標準エラーに出す行。
func (o *Lines) Err(text string) { o.Val("err", text) }

// Fail は止める（識別子は診断ログに残す）。
func (o *Lines) Fail(id string, code int, message string) {
	o.b.WriteString("fail " + id + " " + strconv.Itoa(code) + "\n")
	o.Val("msg", message)
}

// Exit は、出力を済ませてから終える終了コード。
func (o *Lines) Exit(code int) { o.b.WriteString("exit " + strconv.Itoa(code) + "\n") }

// String は答えの全文。
func (o *Lines) String() string { return o.b.String() }
