// Package pathcase はパスと名前を比べるためのキーを作る（Python の infra.pathcase）。
//
// 大文字小文字だけが違う名前は同じパスとして扱い、両立させない。どの機械でも同じ答えにするため、
// 比べるときは必ずここを通す。返すのは比べるための文字列で、ファイルを開く表記には使わない。
package pathcase

import (
	"runtime"
	"strings"

	"golang.org/x/text/cases"
)

var folder = cases.Fold()

// Fold は同じ場所を同じ文字列にする。区切りは機械のもの、大文字小文字はどの機械でも区別しない。
// Python の os.path.normcase(path).casefold() と同じ答えを返す。
func Fold(path string) string {
	if runtime.GOOS == "windows" {
		path = strings.ReplaceAll(path, "/", `\`)
	}
	return folder.String(path)
}
