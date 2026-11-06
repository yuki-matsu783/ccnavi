// ccnavi の実行ファイルの入口（Python の ccnavi.__main__ と entry.cli）。
// 書き直しの途中で、入口は entry 段を移すときに書く。
package main

import (
	"fmt"
	"os"
)

func main() {
	fmt.Fprintln(os.Stderr, "ccnavi (Go): entry はまだ移していません")
	os.Exit(2)
}
