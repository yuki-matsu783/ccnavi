// Package wap は write-and-push で、sh（.ccnavi/scripts/ccnavi-common-write-and-push.sh の ccnavi_wap_*）が
// 実行ファイルに聞く 2 つの問いに答える。ロック・取り込み・コミット・push は sh が持つ。ここはネットワークに出ず、
// コミットもしない（判定と見分けだけを行う）。git は、ローカルの読み取り（status・diff・ls-tree・cat-file・config）
// だけに使う。
//
//   - `ccnavi write-and-push family <識別子>`（Family）: 親子のチケットの親と、write-and-push の対象か（yes・no・
//     stop）、親のブランチ名（親のワークツリーの HEAD から読む）を答える。止める理由のある親子のチケットは stop になり、
//     sh は何も書かずに止まる。origin の無い親のワークツリーと、絶対パスの置き場は対象外になる。
//   - `ccnavi write-and-push sort <親> [<版>]`（Sort）: 親のワークツリーの置き場の変更を、次の 5 つに分ける。
//     ccnavi が書いたと内容で分かるもの（b）、ユーザの判断が書くもの（c）、見分けられないもの（d）、record-risk の
//     記録（keep）、書きかけの一時ファイル（skip）。版が無ければ未コミットの変更を、版があれば `<版>..HEAD` の変更を見る。
//
// 答えは 1 行 1 項目（`<キー> <値>`。sh は jq を使わない）で、頭の行（Head）が無ければ sh は止まる。行・値・
// 終了コードは sh との契約なので変えない。
//
// Python からの対応: tickets/approval/write_and_push を移した。HEAD → Head、TARGET_* → Target の定数、KIND_* →
// 私的な kind の定数、family_of → FamilyOf、target → TargetOf、family → Family、sort → Sort（分けは classify。
// 呼び手が無いので公開しない）、archived_removal → ArchivedRemoval。
package wap
