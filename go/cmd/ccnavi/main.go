// ccnavi の実行ファイルの入口。標準入出力と引数を entry/cli に渡し、終了コードで終える。
package main

import (
	"os"

	"ccnavi/internal/entry/cli"
)

func main() {
	os.Exit(cli.Run(os.Stdin, os.Stdout, os.Stderr, os.Args[1:]))
}
