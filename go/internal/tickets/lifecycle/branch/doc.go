// Package branch は、ブランチ・ワークツリーの名前とチケットの結び付きを読む。読むだけで、何も書かず、ネットワークにも
// 出ない。ホストの結果は sh が読んで JSON で渡す。
//
//   - 依頼文にある issue・MR の指定（`#152`・`issue 152`・`.../issues/152`・`!5`・`MR 5`・`.../pull/5` など）を見つけ、
//     着手の前に紐づくブランチを確かめる指示を足す（PromptRefs・PromptContext。UserPromptSubmit の hook）。
//   - `ccnavi branches <issue|mr> <番号> --result <json>`: 手元の候補（名前に番号を含むブランチ・ワークツリー、
//     issue: か mr: を持つチケット）を集め、sh が取ってきたホストの結果と合わせて出す（Report）。
//   - `ccnavi tree base-bound`: 直接作業の取り込み先のブランチが、チケットに結び付くかを答える（BaseBound）。
//   - `ccnavi ticket next-id`: 直接作業のワークツリー名に付ける次の通し番号を答える（RunNextID・SerialNames）。
//
// 置き場を探すツリーの根と名前の照合は integrate の TreeRoots・NamedFile を使う（Python も stop_integrate の
// _tree_roots・_named_file を使っていた）。依頼文の式は Python の re と同じ答えが要るので（後読みと Unicode の \s・\w）、
// pyre で読む。
//
// Python からの対応: tickets/lifecycle の branchfind・base_bound・next_id をまとめた。branchfind: Ref・label →
// Ref・Label、prompt_refs・prompt_context → PromptRefs・PromptContext、report → Report、names_number・
// project_of_repo・collect・render・read_host は私的（Host・Candidate・TicketLink も私的な型）。base_bound:
// base_bound → BaseBound（YES・NO・UNKNOWN → Bound の定数）。next_id: run → RunNextID、serial_names →
// SerialNames（scanned の組 → *Known）、ASK_USER・serial_now・identifier_problem は私的。
package branch
