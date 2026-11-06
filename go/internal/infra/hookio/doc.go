// Package hookio は、Claude Code が hook の標準入力に書く payload の解釈と、標準出力に返す応答の
// 組み立てを持つ。両方の形を知っているのはここだけにして、上流が変わったときに直す場所を 1 つにする。
// 応答の JSON は Python の実行ファイルが出していたものと同じバイト列（pyjson）で書く。
//
// Python からの対応: write_verdict と write_pre_tool_use → WritePreToolUse、decode → Decode。
package hookio
