// Package agentkind は、サブエージェントがファイルを編集するもの（Write・Edit・MultiEdit・NotebookEdit を使える）かどうかを見分ける。
// 同じワークツリーで動く別の編集するサブエージェントの案内と、親のワークツリーから子を指定せずに編集するサブエージェントを
// 起こす起動の止めが、これを使う。
//
// 読み取り専用と確かめられたものだけを Read にし、見分けられないもの（定義が無い・読めない・種類が空・tools に
// 制限が無い）は Edit に倒す。案内では、読み取りのものに出るノイズより、編集するものに出し損ねるほうが害が大きい。
//
// Python からの対応: hook/agentkind。kind_of → Of、EDIT・READ → Edit・Read（Kind）。
package agentkind
