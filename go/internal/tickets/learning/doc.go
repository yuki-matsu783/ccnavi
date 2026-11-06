// Package learning は学びの蓄積（learning-wiki）の記録を書いて検査する。子のワークツリーの気づきの記録
// （`wip/handoff/learnings/<子>.md`）と判定ログの要約（`.digest.json`）、チケットに結び付かない作業の
// 受信箱（`docs/wiki/inbox/`）を `ticket learn` が書き、子と親の `finish` が HEAD の記録を読んで閉じてよいかを決める。
//
// 読むのはワークツリーの HEAD のファイルだけで、未コミットの変更は見ない（届くのはコミットしたものだけ）。
// 記録の検査は 2 段に分ける。読み取りの検査（frontmatter・`learnings`）に落ちた記録と、整合の検査（数と節・処理の行）に
// 落ちた記録は、統合では直せないので処理すべきものに数えず、名指しして直させる。
//
// Python からの対応: tickets/learning の notes と records をまとめた。notes.run → Run、applies → Applies、
// record_blocked・parent_blocked → RecordBlocked・ParentBlocked、write_broken → WriteBroken、record_rel → RecordRel、
// records.pending → Find、Pending・Item → Pending・Item（unreadable・inconsistent は Problem の列）、uncommitted → Uncommitted。
// 呼び手の無い stamp14・tracked は移していない。parse・consistency_problem・pattern_sources などは私的にした。
package learning
