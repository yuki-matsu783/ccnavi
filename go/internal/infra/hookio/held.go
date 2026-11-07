package hookio

import (
	"bytes"
	"io"
)

// Held は hook の応答を手元に溜め、Flush で書き出す出力。書き出せたあとに走らせる処理（AfterWritten）を持つ。
// 応答を書いた時点で「知らせた」と印を付ける処理は、溜めただけでは知らせは届いていないので、書き出しまで待たせる。
type Held struct {
	bytes.Buffer
	written []func()
}

// Reset は溜めた応答と、書き出しを待つ処理を捨てる（応答を別のものに差し替える回）。
func (h *Held) Reset() {
	h.Buffer.Reset()
	h.written = nil
}

// Flush は溜めた応答を w に書く。書けたときだけ、待たせていた処理を走らせる。
func (h *Held) Flush(w io.Writer) error {
	if _, err := w.Write(h.Bytes()); err != nil {
		return err
	}
	for _, fn := range h.written {
		fn()
	}
	h.written = nil
	return nil
}

// AfterWritten は、w に書いた応答が届いたあとに fn を走らせる。w が Held なら書き出せたときまで待たせ、ほかの出力は
// 書いた時点で届いているので、すぐ走らせる。
func AfterWritten(w io.Writer, fn func()) {
	if h, ok := w.(*Held); ok {
		h.written = append(h.written, fn)
		return
	}
	fn()
}
