// Package tree はパスがどのツリー（ワークスペースルート・プロジェクト・ワークツリー）に属するかを決め、
// ツリーの git の状態をファイルだけから読む。判定の中から呼ばれるので git は起こさない。
//
// ツリーは cwd ではなくファイルの行き先で決める。サブエージェントが親と cwd を共有したまま子の
// ワークツリーへ書いても、その場所のチケットで判定されるようにするため。候補は最長一致で選ぶ
// （ワークツリーはワークスペースルートの中にあるので、短い側を先に採ると子のチケットが使われない）。
//
// `.claude/worktrees/<名前>/` に在るだけではワークツリーと呼ばない。`.git` ファイルの gitdir が元の
// `.git/worktrees/<名前>` を指し、そこの gitdir が候補を指し返す相互参照が成り立つものだけを数える。
//
// Python からの対応: tree と directrec をまとめた。head_text → HeadText（読めなければ ok が偽）、
// directrec.parse → ParseDirect、records_of → DirectRecords、common_dir → CommonDir。
package tree
