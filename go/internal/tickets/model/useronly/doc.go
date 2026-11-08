// Package useronly は、ユーザだけが打つ操作を 1 か所で宣言し、それをエージェントが打った形を見分ける。
//
// 表（オプション・サブコマンド・`user <操作>` の読み替え）が正本である。実行ファイルの入口の読み替え
// （TakeEntry・Rewrite・Refused）と、hook が止める形（TicketApprovalRule・HumanPathForm・HiddenUserEntry・PruneForm・
// Exempt・Forbidden）は、どちらもこの表から組む。操作を足す・外すときは、表の行を直す。式は直さない。
// コマンドの文字列だけを見て、チケットもマーカーも読まない。
package useronly
