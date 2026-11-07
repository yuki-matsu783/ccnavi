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
	root, err := filepath.EvalSymlinks(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, "ccnavi-chrome:", err)
		os.Exit(1)
	}
	if root, err = filepath.Abs(root); err != nil {
		fmt.Fprintln(os.Stderr, "ccnavi-chrome:", err)
		os.Exit(1)
	}
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 64<<10), maxLine)
	out := bufio.NewWriter(os.Stdout)
	for in.Scan() {
		line := in.Text()
		if strings.TrimSpace(line) == "" {
			continue
		}
		fmt.Fprintln(out, chrome.Handle(line, root))
		out.Flush()
	}
}
