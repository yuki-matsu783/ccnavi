// Package learning は、学びの蓄積（learning-wiki）の記録を書いて検査する。書くものは、子のワークツリーの気づきの
// 記録（`wip/handoff/learnings/<子>.md`）、判定ログの要約（`.digest.json`）、チケットに結び付かない作業の受信箱
// （`docs/wiki/inbox/`）である。`ticket learn` が書き、子と親の `finish` が HEAD の記録を読んで閉じてよいかを決める。
//
// 読むのはワークツリーの HEAD のファイルだけで、未コミットの変更は見ない。親に届くのはコミットしたものだけのため。
// 記録の検査は 2 段に分ける。読み取りの検査（frontmatter・`learnings`）に落ちた記録と、整合の検査（数と節・処理の行）に
// 落ちた記録は、統合では直せない。そのため、処理すべきものに数えず、名指しして直させる。
package learning
