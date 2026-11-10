// Package fault は、読み手に届くエラーの共通の形（Detail）を持つ。
//
// エラーを作る側は文を組まず、文面ファイルの識別子と差し込む値を運ぶ。文にするのは出力の境界で、
// texts.SayFault が識別子の文（what・why・next）を引く。識別子が空のエラーと、文面を引けないときは
// What をそのまま出す。infra は policy を読めないので、文面ファイルは引かない。
// 設計は docs/21_design/messages.md の「エラー」。
package fault
