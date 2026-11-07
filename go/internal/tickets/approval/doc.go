// Package approval は承認済みチケットの置き場を扱う。判定が正とするのは、ここが読んだ承認済みチケットだけである。
//
// 提案はエージェントが書けるため、判定が提案を読むと範囲を書き足して通れてしまう。そこで正とするのは、チケットの欄ではなく
// 置き場にする。`.ccnavi/approved/` は組み込みの保護で、エージェントの書き込みが止まる。
// 提案を手で doing/ へ動かすことも承認の経路になる。この経路は承認の画面を通らないので、承認のときにしか当たらなかった
// 構造の検査は、判定の側で blockingProblems（1 か所）が当て、理由を Ticket.Blocked に入れる。
// 承認はチケットの中身を変えず、提案のバイト列のまま動かす。先行関係はファイルに書かず、読むたびに計画の after から
// 計算する（LoadCopy）。
//
// このパッケージの内容は次のとおり。
//   - 置き場の読み（Scan・ScanReview・ReadRaw・ScanProposals など）
//   - 構造の検査（blockingProblems・FamilyProblems など）
//   - 先行の状態（Pool・UnmetPredecessors・PredecessorProblems）
//   - チケットを動かす・書く操作（Admit・moveFile・SettleReview・Followup など）
//   - 表示だけが読む承認の時刻（ApprovedTimes）
//
// マーカーと記録は marks、write-and-push の見分けは wap が受け持つ。
package approval
