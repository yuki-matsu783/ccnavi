package start

import (
	"strconv"
	"strings"
)

// outcome は、ccnavi-start.sh の段取りから呼ばれる動詞の答え。1 行 1 項目で、行の先頭の語で分ける。
//
//   - `<名前> <値>`: sh が使う値。値に改行があれば、行ごとに同じ名前で出す（sh は `sed -n 's/^<名前> //p'` でつなぎ直す）
//   - `out <行>`・`err <行>`: sh がそのまま標準出力・標準エラーに出す行
//   - `fail <識別子> <終了コード>` と `msg <行>`: sh が `ccnavi-start: <文>` を標準エラーに出して止める
//   - `exit <終了コード>`: sh が出力を済ませてから、その終了コードで終える
type outcome struct{ b strings.Builder }

// val は値を出す（空なら出さない）。
func (o *outcome) val(key, v string) {
	if v == "" {
		return
	}
	for _, line := range strings.Split(v, "\n") {
		o.b.WriteString(key + " " + line + "\n")
	}
}

// out は標準出力に出す行。
func (o *outcome) out(text string) { o.val("out", text) }

// err は標準エラーに出す行。
func (o *outcome) err(text string) { o.val("err", text) }

// fail は止める（識別子は診断ログに残す）。
func (o *outcome) fail(id string, code int, message string) {
	o.b.WriteString("fail " + id + " " + strconv.Itoa(code) + "\n")
	o.val("msg", message)
}

// exit は、出力を済ませてから終える終了コード。
func (o *outcome) exit(code int) { o.b.WriteString("exit " + strconv.Itoa(code) + "\n") }

// String は答えの全文。
func (o *outcome) String() string { return o.b.String() }

// 終了コード（ccnavi-start.sh の契約）。
const (
	exitUnmet = 1 // 前提の未充足
	exitUsage = 2 // 引数か環境の誤り
	exitAsk   = 3 // 候補が複数などで、何も作らずにユーザに聞く
)

// shq は、文に載せる引数を単引用符で囲む（中の ' は '"'"'。sh の ccnavi_sh_quote と同じ）。
func shq(s string) string { return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'" }
