package ticketkit

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
)

// PortableText は text の中の root（解いたパスと渡したパス。区切りは `/` にそろえる）を <root> に置き換える。
// 見本（拡張のフィクスチャ）に書く文面を、走らせた機械の一時置き場に依らない形にする。
// 値に対して使う（JSON のテキストに使うと、エスケープの `\\` が先に `/` にそろって壊れる）。
// ワークツリーの根の表記は機械で変わりうる（Windows は小文字）ので、大文字小文字は問わない。
// ccnavi は根を行き着く先まで解いたパスで出す（macOS の /var → /private/var）ので、解いたパスを先に置き換える
// （後にすると、中に含まれる元のパスだけが先に置き換わって /private<root> が残る）。
func PortableText(text, root string) string {
	text = strings.ReplaceAll(text, `\`, "/")
	var spellings []string
	if real, err := filepath.EvalSymlinks(root); err == nil {
		spellings = append(spellings, real)
	}
	if !slices.Contains(spellings, root) {
		spellings = append(spellings, root)
	}
	for _, s := range spellings {
		text = regexp.MustCompile(`(?i)`+regexp.QuoteMeta(strings.ReplaceAll(s, `\`, "/"))).ReplaceAllString(text, "<root>")
	}
	return text
}

// WriteFixture は見本のファイル path を text で書き直す（末尾に改行 1 つ）。見本を書き直すフラグが立っているときだけ呼ぶ。
// 見本を書くのはここだけにする（書き方を変えるときに 1 か所で済む）。
func WriteFixture(t testing.TB, path, text string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(strings.TrimRight(text, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}
