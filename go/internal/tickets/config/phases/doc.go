// Package phases は、フェーズ定義（phases.yml）と親の計画の先行関係を読む。フェーズ定義は、共通レイヤーと、親の
// project: が指すレイヤーの定義の和である。定義はユーザが持つ。置き場は組み込みの保護の内側にあり、エージェントは
// 書けない。組み込みの既定は持たない。ファイルが無ければ、フェーズは番号だけの挙動になる。
//
// 計画を持つ親は、計画が使う定義のコピーを frontmatter の phases: に持つ。承認のあとの判定はそのコピーを読む
// （TypesOf・CopyProblems）。順序は定義に書かず、親の計画のステップの after から都度計算する（Compute）。
//
// Python からの対応: tickets/config の phasetypes と workflow をまとめた。PhaseTypes（dict）→ Types（nil は None）、
// PhaseType.scope の None → Inherit、key・same・differing → Same・Differing、as_copy → PhaseType.AsCopy、
// load・parse（where 付き）・read_copy・types_of・has_copy・origin・copy_problems・scope_problems・
// parent_scope_problems・consolidate_type・_globs → Load・Parse・ReadCopy・TypesOf・HasCopy・Origin・CopyProblems・
// ScopeProblems・ParentScopeProblems・ConsolidateType・globs、types_path・common_types・merge・layer_types・
// load_types → typesPath・CommonTypes・Merge・LayerTypes・LoadTypes、COPY_KEY → ticket.PhasesKey、
// KIND_* → Work・Feedback、LEARNING_* → LearningOn・LearningOff。workflow の compute・waits_of・loose・ready・
// childless_ready・CHILDLESS_MARK・terminal_problems・problems・after_problems・errors・lines → Compute・WaitsOf・
// Loose・Ready・ChildlessReady・ChildlessMark・TerminalProblems・PlanProblems・AfterProblems・Errors・Lines
// （part の文字列 → Part、wf の None → nil）。
package phases
