// Package pyfmt は Python の repr()・str()・真偽の判定を Go の値に当てる。YAML や JSON から読んだ値を
// 文に埋めるとき、Python の実行ファイルと同じ文字列にするために使う。値の型は yaml と pyjson が
// 返すもの（nil・bool・int・*big.Int・float64・string・[]byte・[]any・順序つきの map）を受ける。
// 逆向きに、Python の int()・float() が読む綴り（数字の間の "_"、全角などの 10 進の数字、inf・nan）を
// 同じ答えで読む。
//
// Python からの対応: int(s) → ParseInt、int(s, base) → ParseBigInt、float(s) → ParseFloat、s[:n] → Head、
// s.isdigit() → IsDigit、re の `\w` の 1 文字 → IsWord、int(v)（JSON・YAML の値）→ IntOf、f"{n:,}" → Grouped、
// datetime.fromisoformat → FromISOFormat（比べるための UTC の時刻と、時差が書かれていたか）。
package pyfmt
