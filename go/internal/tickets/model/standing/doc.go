// Package standing は親子のチケットの立ち位置（どのツリーのチケットを正とするか）を決める。正とする側は、全部の
// 親子のチケットで親のブランチに固定する。手元では .claude/worktrees/<P>（HEAD のブランチ名の "/" を "-" に
// 置き換えたものが P になるツリー）で、ほかのツリーのコピーでは代用しない。判定に使うのは親のワークツリーの
// 有無・HEAD とフォルダ名の一致・途中の操作というファイルだけで、git もネットワークも起こさない
// （別の PC でも同じ結果になる）。決まらなければ、その親子のチケットの承認も状態の操作も止める。
//
// Python からの対応: standing を移した。Standing → Standing（kind は StopKind）、Families → Families
// （standing・standing_any・home_tree・branch → Standing・StandingAny・HomeTree・Branch）、head_branch →
// HeadBranch、same_tree → SameTree、guidance → Guidance。1 回だけ引く standing()・standing_any() は
// NewFamilies(...).Standing… で引く。branch_any は呼び手が無いので移していない。
package standing
