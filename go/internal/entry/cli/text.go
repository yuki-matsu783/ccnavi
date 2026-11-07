package cli

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/texts"
)

// `ccnavi text` の終了コード。引けない（識別子が無い・値の不足・文面ファイルが壊れている）は 1、引数の誤りは 2。
// 呼ぶ sh は、引けなくても止める・止めないの判定を変えない。
const (
	textUnavailable = 1
	textUsage       = 2
)

// runText は `ccnavi text <識別子> [名前=値]... [@欄=場合]...`。sh が文面ファイルを直接読まずに文を引く入口。
func runText(stdout, stderr io.Writer, root string, words []string) int {
	if len(words) == 0 || !texts.IsID(words[0]) {
		fmt.Fprintln(stderr, "ccnavi: text <識別子> [名前=値]... [@欄=場合]...")
		return textUsage
	}
	values, variants := texts.Values{}, texts.Variants{}
	for _, word := range words[1:] {
		key, value, ok := strings.Cut(word, "=")
		if !ok || key == "" {
			fmt.Fprintf(stderr, "ccnavi: text の引数 %s は 名前=値 の形ではありません。\n", pyfmt.Repr(word))
			return textUsage
		}
		if name, ok := strings.CutPrefix(key, "@"); ok {
			variants[name] = value
		} else {
			values[key] = value
		}
	}
	entry := texts.Find(root, words[0])
	if entry == nil {
		fmt.Fprintf(stderr, "ccnavi: 指定した識別子のメッセージが見つかりません: %s\n", words[0])
		return textUnavailable
	}
	text, err := texts.Render(entry, values, variants, root)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: メッセージを生成できません: %s\n", err)
		return textUnavailable
	}
	fmt.Fprintln(stdout, text)
	return config.ExitOK
}
