// Package wapmerge は write-and-push の finish が子を親のブランチへ合流する前に、sh
// （ccnavi-common-write-and-push.sh）が聞く検査（`ccnavi write-and-push merge <子>`）を持つ。git を動かすのは sh で、
// ここは git を読むだけで、ネットワークに出ずコミットもしない。答えは 1 行 1 項目（`<キー> <値>`。sh は jq を
// 使わない）で、終了コードは検査の結果によらず 0。
//
//   - `skip <理由>`: 合流しない（親の finish、作業中でない子など）。sh は `ticket finish` へ進み、止める理由はそちらが言う
//   - `stop <理由>` と続く `hint <案内>`: 何も変えずに止める
//   - 合流してよいなら `child`・`tree`・`head`・`base`・`merge yes|no`（子の先頭が親の先頭の祖先なら `no`）
//
// Python からの対応: tickets/lifecycle/write_and_push_merge を移した。plan → Plan。_tree_problem・_ignored_in_parent・
// _unrestorable などは私的。
package wapmerge
