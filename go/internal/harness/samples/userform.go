package samples

import (
	"fmt"
	"io"
	"slices"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/model/useronly"
)

// テストが実行ファイルへ渡す引数に、`user` の入口の形を置く（tests/userform.py の写し）。
//
// 実行ファイルの `user` は、--root・--cwd・--record-writes・--record-tree・--record-commit・--record-merge・--entry を
// 読み飛ばした後の最初の語でなければならない。テストの道具は、--guard-ticket-approval など読み飛ばされないオプションを
// 先頭に付けて呼ぶので、そのまま `user …` を後ろに渡すと入口として読まれない。Place は、`user <操作> [値]` を先頭のラッパーの
// オプションのすぐ後ろへ寄せ、道具のオプションをその後ろに置く。
//
// 表（読み飛ばすオプション・操作の語）はテストの側に持つ。実行ファイルの表（tickets/model/useronly）と食い違うと、
// 入口に届かないテストが落ちて気づく。

const userWord = "user"

var wrapperOptions = []string{"--root", "--cwd", "--record-writes", "--record-tree", "--record-commit", "--record-merge", "--entry"}

// verbs は `user` の後ろの操作の語と、値を 1 つ取るか（`user reviewed 3`）。
var verbs = []struct {
	words []string
	value bool
}{
	{[]string{"agree"}, false},
	{[]string{"reviewed"}, true},
	{[]string{"close-early"}, false},
	{[]string{"prune"}, false},
	{[]string{"handoff"}, false},
	{[]string{"mirror"}, false},
}

// Place は prefix（道具が付けるオプション）と args（呼び出し側の引数）を 1 つの引数列にする。
// args が `user` で始まる（先頭のラッパーのオプションの後ろ）ときだけ並べ替える。それ以外は prefix + args のまま。
func Place(prefix, args []string) []string {
	wrapperPrefix, otherPrefix := splitWrapper(prefix)
	at := 0
	for at < len(args) && slices.Contains(wrapperOptions, args[at]) && at+1 < len(args) {
		at += 2
	}
	if at >= len(args) || pyfmt.Casefold(args[at]) != userWord {
		return slices.Concat(prefix, args)
	}
	var words []string
	for _, w := range args[at+1 : min(at+3, len(args))] {
		words = append(words, pyfmt.Casefold(w))
	}
	size := 1
	for _, v := range verbs {
		if len(words) >= len(v.words) && slices.Equal(words[:len(v.words)], v.words) {
			size = 1 + len(v.words)
			if v.value {
				size++
			}
			break
		}
	}
	return slices.Concat(wrapperPrefix, args[:at], args[at:min(at+size, len(args))], otherPrefix, args[min(at+size, len(args)):])
}

func splitWrapper(prefix []string) (wrapper, other []string) {
	for at := 0; at < len(prefix); at += 2 {
		pair := prefix[at:min(at+2, len(prefix))]
		if slices.Contains(wrapperOptions, prefix[at]) {
			wrapper = append(wrapper, pair...)
		} else {
			other = append(other, pair...)
		}
	}
	return wrapper, other
}

// UserformMain は sh のスタブが `teststub userform <引数>…` で起こす口の本体（Python の `python userform.py <引数>…`）。
// 引数は実行ファイルの `--entry` の並び（先頭のラッパーのオプション）で、実行ファイルと同じに検べる（useronly.TakeEntry）。
// 断る並びなら、文を stderr に出して終了コード 2 を返す。通れば何も出さず 0 を返す（標準出力は使わない）。
func UserformMain(args []string, stderr io.Writer) int {
	if _, _, err := useronly.TakeEntry(args); err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	return 0
}
