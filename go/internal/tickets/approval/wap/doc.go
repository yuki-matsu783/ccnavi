// Package wap は write-and-push で sh（.ccnavi/scripts/ccnavi-common-write-and-push.sh の ccnavi_wap_*）が実行
// ファイルに聞く 2 つに答える。ロック・取り込み・コミット・push は sh が持ち、ここはネットワークに出ずコミットも
// しない（判定と見分けだけ）。git はローカルの読み取り（status・diff・ls-tree・cat-file・config）だけ。
//
//   - `ccnavi write-and-push family <識別子>`（Family）: 親子のチケットの親と、write-and-push の対象か（yes・no・
//     stop）、親のブランチ名（親のワークツリーの HEAD から読む）。止める理由のある親子のチケットは stop で、sh は
//     何も書かずに止まる。origin の無い親のワークツリーと、絶対パスの置き場は対象外。
//   - `ccnavi write-and-push sort <親> [<版>]`（Sort）: 親のワークツリーの置き場の変更を、ccnavi が書いたと内容で
//     分かるもの（b）・ユーザの判断が書くもの（c）・見分けられないもの（d）・record-risk の記録（keep）・書きかけの
//     一時ファイル（skip）に分ける。版が無ければ未コミット、あれば `<版>..HEAD`。
//
// 答えは 1 行 1 項目（`<キー> <値>`。sh は jq を使わない）で、頭の行（Head）が無ければ sh は止まる。行・値・
// 終了コードは sh との契約なので変えない。
//
// Python からの対応: tickets/approval/write_and_push を移した。HEAD → Head、TARGET_* → Target の定数、KIND_* → 私的な kind の
// 定数、family_of → FamilyOf、target → TargetOf、family → Family、sort → Sort（分けは classify。呼び手が
// 無いので公開しない）、archived_removal → ArchivedRemoval。
package wap
