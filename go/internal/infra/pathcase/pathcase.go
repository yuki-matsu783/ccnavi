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

// Real は path が実際に指す場所の比べのキー（Python の pathcase.fold(os.path.realpath(path))）。リンクは辿る。
func Real(path string) string {
	return Fold(ospath.Realpath(path))
}

// SameReal は a と b が実際に同じ場所を指すか（リンクを辿ってから大文字小文字を区別せずに比べる）。
func SameReal(a, b string) bool {
	return Real(a) == Real(b)
}

// SameNorm は a と b が表記の上で同じパスか（Python の fold(normpath(a)) == fold(normpath(b))。リンクは辿らない）。
func SameNorm(a, b string) bool {
	return Fold(ospath.Normpath(a)) == Fold(ospath.Normpath(b))
}
