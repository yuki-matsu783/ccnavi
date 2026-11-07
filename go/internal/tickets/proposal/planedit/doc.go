// Package planedit は承認待ちの親の提案の計画まわりを書き換える。計画の番号の振り直しと書き戻し
// （`--plan-order <親>`。Reorder・Save）と、計画が使うフェーズ定義のコピーの差し込み（`--plan-order <親>
// --fill-phases`。Fill）。どちらも frontmatter の決まった欄の値の行だけを差し替え、ほかの行（コメントも）と本文は
// 残す。書く前に組んだ全文を読み直し、ほかの欄が読んだ形で元と同じでなければ何も書かない（値にアンカーがあり、
// ほかの欄が別名で指しているときなど）。値の中に書いたコメントは消える。
//
// 振り直しの決まり（VS Code の計画編集タブが同じ決まりで仮の番号を出す。見本の表は tests/fixtures/plan-reorder.json）:
//   - 先行が全部前に並んだステップのうち、元の番号がいちばん小さいものから番号を振る（Kahn の方法）。線を変えても
//     並びが崩れなければ番号は変わらない
//   - 子が承認された番号（固定した番号）は動かさず、空いた番号に承認されていないステップを詰める。詰められなければ
//     断る。固定した番号へ入る線は変えられない。循環になる線も断る。断ったときは提案のままの答えを返す
//   - after は振り直した番号に付け替え、deferred はステップに付いたまま動く。延期の引き受け手は計算し直す
//   - 全体計画とフィードバック計画は別に振り直す（フィードバック計画の番号は全体計画の続き）
//
// 書くのは親の提案（todo/）と、番号の変わるステップを指す子の提案（識別子・phase:・predecessors を付け替える）
// だけ。開いたときのハッシュ（source_sha）が今と同じで、順序の検査と改版の錠に当たらないときだけ書く。
//
// Python からの対応: tickets/proposal の plan_order と plan_phases をまとめた。plan_order: VERSION → Version、
// REFUSED_* → RefusalKind の定数、Order（dict[str, dict]）→ Order（キーは phases.Part）、parse_order → ParseOrder、
// renumber → Renumber、Source・load → Source・Load、Answer・answer → Answer・Reorder、plans → Answer.Plans、
// body → Answer.Body、preview_plans → PreviewPlans、pick → Pick（cwd は引数）、current_parent → CurrentParent、
// Saved・save → Saved・Save、reread_sha → RereadSHA（cwd は引数）、changed_elsewhere・retarget・rebuilt・
// child_rebuilt・write・digest・mismatched は私的。plan_phases: Filled・fill → Filled・Fill。
package planedit
