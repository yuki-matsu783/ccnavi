// Package shell はコマンド文字列を、シェルが割るとおりに読む。hook の判定がルールを「文字列にその語が
// 含まれるか」ではなく「コマンドとして実行されるか」に適用するための土台で、grep や echo の引数・
// ヒアドキュメントの本文に書いただけの語では止めず、`$( )`・改行・プロセス置換・実行役のコマンド
// （env・sudo・sh -c）・`cd` の先で実行されるものは見逃さない。読み切れない形は Degraded にして、
// 呼び手が生の文字列との一致（厳しい側）に切り替えられるようにする。語の分割は Python の shlex と
// 同じ答えを返し、読み違いの向きは常に止める側にする。
//
// 入口は Read（コマンドの読み）と PlacedCommands、標準入力を読むかの判定 ReadsStdin。語の分割は Split
// （閉じない引用などは errSplit）、引用は Quote。外部のスクリプトを sh で走らせるのは Script
// （sh は Program、期限は目的ごとの ScriptTimeout・SyntaxTimeout・ConditionTimeout、sh が無ければ ErrNoShell）。
// 代入の語や実行役のオプションの判定は IsIdentifier・AssignedName・RunnerTakesValue。
package shell
