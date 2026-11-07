// Package mirror は、ワークスペースにしか無い共通のもの（共通レイヤー `.ccnavi/config/common/` と工程スキルの
// `.claude/skills/<工程>/`）を、プロジェクトのツリー（ミラー `.ccnavi/config/common/` と `skills/<工程>/`）へ写す。
// 写し方は 1 つで、元の中身と先の中身を比べて変更（Change）の一覧を作り（改行の違いは見ない）、一時ファイルからの
// 置き換えで書き、1 本でも書けなければした分を戻す。先かその途中がリンクなら写さない。共通側に無くなったものは
// 先からも消すが、共通側が空なら何も消さない（消し忘れでミラーを全部失わないため）。
//
// 写す契機は 2 つ。ユーザの発言のたびに着手済みの親のワークツリーへ（Sync。stat だけの指紋で、差分が無い発言は
// 何も読まない）と、ユーザが押す `ccnavi user mirror` でプロジェクトのチェックアウトへ（Prepare・Apply）。後者の
// JSON（PlanJSON・AppliedJSON）・expect の値は拡張（ccnavi-board の mirrormodel.ts）との契約。ツール呼び出しの
// 中では書かない。ずれをコミットや取り消しの対象にしない見分けと文面は Split・Label・OthersNote・NotWork・Ask。
//
// Python からの対応: tickets/sync の configsync・skillsync・mirrorsync・promptsync・mirror_gap をまとめた。
// configsync.Change → Change（content が None → Remove、before が None → !Existed）、configsync.plan・apply →
// 私的な commonPlan・apply、skillsync.plan・apply → 私的な skillPlan・apply（2 つの差分は updates・removals・
// scan を共有する）、skillsync.installed → Installed、mirrorsync.prepare・apply → Prepare・Apply、action_of・
// is_skill → Change.Action・Change.IsSkill、expect_of → Plan.Expect・Project.Expect、plan_json・applied_json →
// PlanJSON・AppliedJSON、STALE・JSON_VERSION・STATE_*・ACTION_* → Stale・JSONVersion・State*・Action*、
// promptsync.sync → Sync、SIGNATURE_PREFIX → SignaturePrefix、mirror_gap.split・label・others_note・NOT_WORK・ASK →
// Split・Label・OthersNote・NotWork・Ask。呼び手の無い mirrorsync.relative と skillsync.describe（Sync の行に寄せた）は移さない。
package mirror
