// Package outcome は、サブエージェントの終わり方（SubagentStop）の記録を持つ。差し戻しの記録（.bounced）とは別に、
// 「終わったときにどうだったか」を Kind の 5 つで残す。記録は state の `subagent-<セッション>-<agent_id>.outcome`（JSON）。
//
// 差し戻して続けさせた回は確定していない記録（Provisional）として書き、次の SubagentStop で確定する。親への知らせは、
// 確定した結果を 1 度だけ出す。出力に書き終えてから「通知済み」の印を付ける（Confirm）ので、書く前に止まったら次の機会に
// 同じ結果を出し直す。Stop が促すのは Outside と Unchecked だけで、促したら印を付ける（同じ結果は 1 回だけ）。
//
// Python からの対応: hook/subagent_outcome。OK・FIXED・OUTSIDE・UNCHECKED・UNATTRIBUTED → OK・Fixed・Outside・
// Unchecked・Unattributed（Kind）、write → Write（引数は Entry。final=False → Entry.Provisional）、of → Of、notices →
// Notices、confirm → Confirm（使わなかった state_dir は受けない）、mark_nudged → MarkNudged、stop_lines → StopLines。
// 上の段が直に読まない path・read・all_of・line・stop_pending・_mark は私的にした。
package outcome
