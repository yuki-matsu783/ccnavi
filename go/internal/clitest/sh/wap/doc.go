// Package wap は、write-and-push（取り込み → 書く → コミット → push を 1 操作にする sh）と、承認の push（ccnavi-user-push-approved.sh）を
// 外から起こして確かめるテスト（clitest/sh/wap）。
//
// 使い捨てのワークスペース（main）・bare のリモート・親のワークツリー（.claude/worktrees/i0001。承認済みで、送ってある）をテストごとに
// 組む（kit_test.go）。組む手順は実行ファイルを 2 回起こすだけなので、作り置きの写し（treecache）は使わず、並行に回す。実行ファイルの
// 代わりの sh・git の代わりの sh は stubs_test.go にあり、GitLab の代わりの HTTP は gitlab_test.go にある。
//
// 見るのは終了コード・出力と、git とファイルに残ったものだけ。合流の前の検査と衝突の文は Go の単体テスト（tickets/lifecycle/wapmerge・
// phase/scope）が見る。ここでは、止まったときに sh が何も変えないことを代表で見る。
package wap
