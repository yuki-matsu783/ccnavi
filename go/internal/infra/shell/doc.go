// Package shell はコマンド文字列を、シェルが割るとおりに読む。hook の判定がルールを「文字列にその語が
// 含まれるか」ではなく「コマンドとして実行されるか」に適用するための土台で、grep や echo の引数・
// ヒアドキュメントの本文に書いただけの語では止めず、`$( )`・改行・プロセス置換・実行役のコマンド
// （env・sudo・sh -c）・`cd` の先で実行されるものは見逃さない。読み切れない形は Degraded にして、
// 呼び手が生の文字列との一致（厳しい側）に切り替えられるようにする。語の分割は Python の shlex と
// 同じ答えを返し、読み違いの向きは常に止める側にする。
//
// Python からの対応: shellread・shellread_cd・shellread_scan・shellread_words をまとめた。
// shellread.read → Read、placed → PlacedCommands、shellread_words.reads_stdin → ReadsStdin。shlex.split → Split
// （閉じない引用などは ErrSplit）、shlex.quote → Quote。外部のスクリプトを sh で走らせる subprocess.run(["sh", …]) →
// Script（sh は Program、期限は目的ごとの ScriptTimeout・SyntaxTimeout・ConditionTimeout、sh が無ければ ErrNoShell）。
// sh の名前の形 `^[A-Za-z_][A-Za-z0-9_]*$` → IsIdentifier、代入の語の名前 → AssignedName、実行役のオプションが値を取るか →
// RunnerTakesValue。
package shell
