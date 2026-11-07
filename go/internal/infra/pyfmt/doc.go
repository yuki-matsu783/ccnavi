// Package pyfmt は Python の repr()・str()・真偽の判定と同じ答えを Go の値で返す。YAML や JSON から読んだ値を
// 文に埋めるとき、文字列が機械によらず安定し、ルールの書き手が Python の書き方で想定した形になるよう、ここを通す。
// 値の型は yaml と pyjson が返すもの（nil・bool・int・*big.Int・float64・string・[]byte・[]any・順序つきの map）を受ける。
// 逆向きに、Python の int()・float() が読む綴り（数字の間の "_"、全角などの 10 進の数字、inf・nan）も
// 同じ答えで読む。
//
// 数の読みは ParseInt・ParseBigInt・ParseFloat・IntOf・IsDigit・IsWord、書式は Grouped・FormatG、
// 日時は FromISOFormat（比べるための UTC の時刻と、時差が書かれていたか）。
// 文字の扱いは Lower・Upper・Casefold・NFKC・NFC・NFD・Combining・EastAsianWidth。
// そのほか、先頭 n 字の Head、読んだ値と整数の比べ EqualsInt、空でない文の連結 JoinNonEmpty と NonEmpty（Repeats）がある。
package pyfmt
