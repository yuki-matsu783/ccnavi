// Package status は `ccnavi ticket status [<親>]` を持つ。承認済みチケットの状態を、
// 置き場と git を読んで ccnavi が言い切る。エージェントがファイルと `git status` を読んで推すと、
// 手で置いた承認（欄が無く未コミット）を「承認が途中で止まった」と読み違える。承認はチケットの中身を変えないので、
// どの経路で承認したかは置き場と git にしか出ない。
//
// 読むだけで、何も書かず、ネットワークにも出ない。push 済みかは手元のリモート追跡の ref で見るので、
// 古いかもしれないと出力に添える。サブエージェントも打てる。実行ファイルは呼び手を知らないので、
// 状態を動かすコマンドの行には「メインエージェントのみ実行可能」と書く。
// 未コミットの承認済みチケットを運ぶのはユーザで、エージェントが運ぶコマンドは出さない。
//
// Python からの対応は次のとおり。
//   - entry/status を移した。run → Run（cli の「親は 1 件のみ」の照合と family_of も Run が持つ）。
//   - FileState・FILE_* → 私的な fileState と fileKind の定数、Entry・_Family → 私的な entry・familyView。
//   - TIMEOUT_SECONDS → git.ReadTimeout。
package status
