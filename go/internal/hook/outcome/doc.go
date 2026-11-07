// Package outcome は、サブエージェントの終わり方（SubagentStop）を記録する。差し戻しの記録（.bounced）とは別に、
// 「終わったときにどうだったか」を Kind の 5 つで残す。記録は state の `subagent-<セッション>-<agent_id>.outcome`（JSON）に置く。
//
// 差し戻して続けさせた回は、確定していない記録（Provisional）として書き、次の SubagentStop で確定する。親への知らせは、
// 確定した結果を 1 度だけ出す。出力に書き終えてから「通知済み」の印を付ける（Confirm）ので、書く前に止まったら次の機会に
// 同じ結果を出し直す。Stop が促すのは Outside と Unchecked だけで、促したら印を付ける（同じ結果は 1 回だけ）。
package outcome
