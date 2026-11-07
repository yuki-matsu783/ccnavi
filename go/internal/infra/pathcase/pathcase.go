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

// Under は path が base の下（か同じ）なら、base からの相対と真を返す。相対は path の綴りのまま、区切りは機械のもの
// で、同じなら "."。どちらも絶対パスにして（リンクは辿らない）、部分ごとに大文字小文字を区別せずに比べる。
// 文字列の先頭で比べないのは、"..foo" を外と見たり、"/ab" を "/a" の下と見たりしないためで、部分ごとに畳むのは、
// 畳むと長さの変わる字（"ß" は "ss"）でも相対を元の綴りで切り出すためである。
func Under(path, base string) (string, bool) {
	sep := ospath.Sep
	parts := strings.Split(ospath.Abspath(path), sep)
	baseParts := strings.Split(strings.TrimRight(ospath.Abspath(base), sep), sep)
	if len(parts) < len(baseParts) {
		return "", false
	}
	for i, part := range baseParts {
		if Fold(part) != Fold(parts[i]) {
			return "", false
		}
	}
	rest := parts[len(baseParts):]
	if len(rest) == 0 || len(rest) == 1 && rest[0] == "" {
		return ".", true
	}
	return strings.Join(rest, sep), true
}
