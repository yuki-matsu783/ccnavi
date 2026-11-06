package pathcase

import (
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// Fold は同じ場所を同じ文字列にする。区切りは機械のもの、大文字小文字はどの機械でも区別しない。
// Python の os.path.normcase(path).casefold() と同じ答えを返す。
func Fold(path string) string {
	if ospath.Windows {
		path = strings.ReplaceAll(path, "/", `\`)
	}
	return pyfmt.Casefold(path)
}

// HasExt は name が拡張子 ext で終わるか。大文字小文字を区別しない（"A.MD" も ".md"）。
// ospath は pathcase より下の段なので、Fold を通す比べはここに置く。
func HasExt(name, ext string) bool {
	return strings.HasSuffix(Fold(name), Fold(ext))
}
