//go:build js && wasm

// Chrome 拡張が読む WASM の入口。globalThis.ccnaviHandle(<要求の JSON>) を置き、答えの JSON の文字列を返す。
// 仮のツリーは拡張が渡す仮想のファイルシステム（globalThis.fs）の /ws に組む。呼び出しは同期で、Go の側で
// 待ち合わせをしない（ファイルの呼び出しは仮想のファイルシステムがその場で答える）。
package main

import (
	"syscall/js"

	"ccnavi/internal/entry/chrome"
)

// root は仮のツリーを組む場所（仮想のファイルシステムの中）。
const root = "/ws"

func main() {
	js.Global().Set("ccnaviHandle", js.FuncOf(func(_ js.Value, args []js.Value) any {
		// 引数が無い呼び出しは、空の要求として handle に渡し、handle の苦情の形で返す。
		if len(args) == 0 {
			return chrome.Handle("", root)
		}
		return chrome.Handle(args[0].String(), root)
	}))
	// main が戻るとランタイムが止まり、登録した ccnaviHandle を呼べなくなる。そのため永久に待つ。
	select {}
}
