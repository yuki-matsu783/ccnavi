package pathcase

import (
	"strings"

	"golang.org/x/text/cases"

	"ccnavi/internal/infra/ospath"
)

var folder = cases.Fold()

// Fold は同じ場所を同じ文字列にする。区切りは機械のもの、大文字小文字はどの機械でも区別しない。
// Python の os.path.normcase(path).casefold() と同じ答えを返す。
func Fold(path string) string {
	if ospath.Windows {
		path = strings.ReplaceAll(path, "/", `\`)
	}
	return folder.String(path)
}
