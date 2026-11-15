package cli

import (
	"fmt"
	"io"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pathcase"
)

// pathUnder は `path under <基準> <パス>`（ccnavi-review.sh の依頼文の後始末用）。パスが基準の真下のどこか（基準
// そのものは含まない）なら `under yes`、違えば `under no` を、頭の行 `path 1` に続けて書く。`.`・`..` を解いて、
// 部分ごとに大文字小文字を区別せずに比べる（pathcase.Under）。リンクは辿らない。
func pathUnder(stdout io.Writer, base, path string) int {
	rel, under := pathcase.Under(path, base)
	fmt.Fprintf(stdout, "path 1\nunder %s\n", yesNo(under && rel != "."))
	return config.ExitOK
}
