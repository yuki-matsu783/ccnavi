// Package mirror は、ワークスペースにしか無い共通のもの（共通レイヤー `.ccnavi/config/common/` と工程スキルの
// `.claude/skills/<工程>/`）を、プロジェクトのツリー（ミラー `.ccnavi/config/common/` と `skills/<工程>/`）へ写す。
//
// 写し方は 1 つである。元の中身と先の中身を比べて変更（Change）の一覧を作る。改行の違いは見ない。書くときは、
// 一時ファイルからの置き換えで書く。1 本でも書けなければ、書いた分を戻す。先かその途中がリンクなら写さない。
// 共通側に無くなったものは、先からも消す。ただし共通側が空なら、何も消さない。消し忘れでミラーを全部失わないためである。
//
// 写す契機は 2 つある。1 つめは、ユーザの発言のたびに、着手済みの親のワークツリーへ写す（Sync）。stat だけの指紋を
// 使い、差分が無い発言では何も読まない。2 つめは、ユーザが押す `ccnavi user mirror` で、プロジェクトのチェックアウトへ
// 写す（Prepare・Apply）。後者の JSON（PlanJSON・AppliedJSON）と expect の値は、拡張（ccnavi-board の mirrormodel.ts）
// との契約である。ツール呼び出しの中では書かない。コミットや取り消しの対象にしない見分けと文面は、
// Split・Label・OthersNote・NotWork・Ask が持つ。
//
// Python からの対応: tickets/sync の configsync・skillsync・mirrorsync・promptsync・mirror_gap をまとめた。
// configsync.Change → Change（content が None → remove、before が None → !existed）、configsync.plan・apply →
// 私的な commonPlan・apply、skillsync.plan・apply → 私的な skillPlan・apply（2 つの差分は updates・removals・
// scan を共有する）、skillsync.installed → Installed、mirrorsync.prepare・apply → Prepare・Apply、action_of・
// is_skill → Change.Action・Change.IsSkill、expect_of → Plan.Expect・Project.expect、plan_json・applied_json →
// PlanJSON・AppliedJSON、STALE・JSON_VERSION・STATE_*・ACTION_* → stale・JSONVersion・State*・Action*、
// promptsync.sync → Sync、SIGNATURE_PREFIX → signaturePrefix、mirror_gap.split・label・others_note・NOT_WORK・ASK →
// Split・Label・OthersNote・NotWork・Ask。呼び手の無い mirrorsync.relative と skillsync.describe（Sync の行に寄せた）は移さない。
package mirror
