// Package candidates は、承認の候補を組み、その検査を持つ。提案を承認の対象に入れるものは Candidate、外すものは
// Rejected に分ける。計画・コピー（phases:）・改版・欄・ブランチの食い違いは、ここで並べる。承認（--agree）・見せる
// （preview）・確かめる（verify）・--lint は、同じ Candidates を通る。外れるものを数える経路が 2 本あると、片方が
// 気づかないうちに弱くなるからである。
//
// 子の定義は親のコピーから引き、phases.yml からは引かない。承認のあとに phases.yml を直しても、進行中の親の子の承認が
// 落ちないようにするためである。phases.yml は、コピーが同じかを確かめるためだけに読む。
//
// Python からの対応: tickets/proposal/agree_candidates。Candidate（is_revision・plans_feedback → IsRevision・
// PlansFeedback、types・config の dict|None → *phases.Types）、candidates → Candidates（引数は conf, root の順。
// 外したものは Rejected）、validate → Validate、plan_problems → PlanProblems、copy_config_problems →
// copyConfigProblems、lock_problems → LockProblems、fixed_numbers → FixedNumbers（集合は map[int]bool）、
// types_from・project_of → typesFrom・ProjectOf、parent_elsewhere_text → ParentElsewhereText、
// existing_branch_warnings → ExistingBranchWarnings（copies・closed・review は settled 1 つにまとめた）。
// revision_problems・shorthand_problems・script_field_problems・record_field_problems・unreadable_scope・
// open_children・feedback_notes・own_branch は、上の段に呼び手が無いので私的。
package candidates
