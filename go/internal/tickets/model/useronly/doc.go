// Package useronly は、ユーザだけが打つ操作を 1 か所で宣言し、それをエージェントが打った形を見分ける。
// 表（オプション・サブコマンド・`user <操作>` の読み替え）が正本で、実行ファイルの入口の読み替え
// （TakeEntry・Rewrite・Refused）と、hook が止める形（TicketApprovalRule・HumanPathForm・HiddenUserEntry・
// PruneForm・Exempt・Forbidden）は、どちらもこの表から組む。操作を足す・外すときは表の行を直し、式は直さない。
// コマンドの文字列だけを見て、チケットもマーカーも読まない。
//
// Python からの対応: tickets/model の user_only と phase_forms をまとめた。user_only.take_entry・rewrite・
// refused（argparse の結果の代わりに、オプションが立っているかを返す関数を受ける）・direct_refusal・terminal →
// TakeEntry・Rewrite・Refused・DirectRefusal・Terminal（Op の定数）、UserEntryError → EntryError、
// USER_SCRIPT_PREFIX → UserScriptPrefix。phase_forms の exempt・forbidden・human_path_form（種類は HumanKind）・
// hidden_user_entry（inner は []Layer）・prune_form・user_prune_form・ticket_approval_rule → Exempt・Forbidden・
// HumanPathForm・HiddenUserEntry・PruneForm・UserPruneForm・TicketApprovalRule、guard_off_message・
// user_script_message・expanded_option_message・board_form_message → HumanMessage（種類で選ぶ）、
// hidden_entry_message・prune_message → HiddenEntryMessage・PruneMessage、HELD_TOOLS・EXEMPT_NOTE・
// TURN_DEFINED・EXE_OPTIONS → HeldTools・ExemptNote・TurnDefined・ExeOptions、CODE_*・*_RULE_ID → Code*・*RuleID。
// 表から式を組む関数（cli_forms・script_forms など）と表そのもの、commands は私的にした。行の無い SCRIPTS
// （とそれだけが読む Flag の script・op）、どの行も使わない unless の "no-yes"、読み手の無い Op の what・Sub の op は
// 移さなかった。
package useronly
