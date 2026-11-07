//go:build !(js && wasm)

// 手元の試験用の入口。`ccnavi-chrome <空のディレクトリ>` で、標準入力の 1 行 1 要求に 1 行で答える。拡張の試験が、
// WASM と同じ要求を手元のファイルシステムの上でも回して答えを突き合わせる。
package main

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"ccnavi/internal/entry/chrome"
)

// maxLine は 1 要求の大きさの上限（snapshot にブランチの中身が全部入る）。
const maxLine = 256 << 20

func main() {
	if len(os.Args) < 2 || os.Args[1] == "" {
		fmt.Fprintln(os.Stderr, "使い方: ccnavi-chrome <仮のツリーを組む空のディレクトリ>")
		os.Exit(1)
	}
	// 仮のツリーの場所をリンクのない絶対パスにそろえる。答えに入るパスを、拡張の試験が見本と比べるときにぶれないようにする。
	root, err := filepath.EvalSymlinks(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, "ccnavi-chrome:", err)
		os.Exit(1)
	}
	if root, err = filepath.Abs(root); err != nil {
		fmt.Fprintln(os.Stderr, "ccnavi-chrome:", err)
		os.Exit(1)
	}
	// 1 行が 1 要求。1 要求は snapshot を含むので大きく、既定の行の上限（64KB）では足りない。
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 64<<10), maxLine)
	out := bufio.NewWriter(os.Stdout)
	for in.Scan() {
		// 空行は読み飛ばす。
		line := in.Text()
		if strings.TrimSpace(line) == "" {
			continue
		}
		fmt.Fprintln(out, chrome.Handle(line, root))
		// 1 要求ごとに流す。呼び手は答えを待ってから次の要求を送る。
		out.Flush()
	}
}
