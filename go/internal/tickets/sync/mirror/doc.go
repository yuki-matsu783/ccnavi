// Package mirror は、ワークスペースにしか無い共通のもの（共通レイヤー `.ccnavi/config/common/` と工程スキルの
// `.claude/skills/<工程>/`）を、プロジェクトのツリー（ミラー `.ccnavi/config/common/` と `skills/<工程>/`）へ写す。
//
// 写し方は 1 つである。元の中身と先の中身を比べて変更（Change）の一覧を作る。改行の違いは見ない。書くときは、
// 一時ファイルからの置き換えで書く。1 本でも書けなければ、書いた分を戻す。先かその途中がリンクなら写さない。
// 共通側に無くなったものは、先からも消す。ただし共通側が空なら、何も消さない。消し忘れでミラーを全部失わないため。
//
// 写す契機は 2 つある。1 つめは、ユーザの発言のたびに、着手済みの親のワークツリーへ写す（Sync）。stat だけの指紋を
// 使い、差分が無い発言では何も読まない。2 つめは、ユーザが押す `ccnavi user mirror` で、プロジェクトのチェックアウトへ
// 写す（Prepare・Apply）。後者の JSON（PlanJSON・AppliedJSON）と expect の値は、拡張（ccnavi-board の mirrormodel.ts）
// との契約である。ツール呼び出しの中では書かない。コミットや取り消しの対象にしない見分けと文面は、
// Split・Label・OthersNote・NotWork・Ask が持つ。
package mirror
