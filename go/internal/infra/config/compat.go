package config

import (
	"regexp"
	"strconv"

	"ccnavi/internal/infra/env"
)

// CompatFile は sh が互換の版を宣言する場所（ワークスペースルートからの相対、"/" 区切り）。`.ccnavi/scripts/` の
// sh はどれもこれを `.` で読む。Chrome 拡張は統合先のこのファイルを読んで、同梱の版と比べる。
const CompatFile = DefaultProjectHome + "/" + ScriptsDir + "/ccnavi-common.sh"

var compatLine = regexp.MustCompile(`(?m)^` + regexp.QuoteMeta(env.Compat) + `=([0-9]+)[ \t]*$`)

// DeclaredCompat は sh の本文に書かれた互換の版。書かれていなければ ok が偽。
func DeclaredCompat(text string) (int, bool) {
	found := compatLine.FindStringSubmatch(text)
	if found == nil {
		return 0, false
	}
	n, err := strconv.Atoi(found[1])
	return n, err == nil
}
