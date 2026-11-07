// Package treegate は、親のワークツリーの状態を見て操作を止める条件（ゲート）を 1 か所に持つ。見るものは 2 つある。
// 1 つめは、置き場（承認済みチケットと提案）の外にある、追跡中のファイルの未コミットの変更。2 つめは、親のブランチが
// `origin/<ブランチ>` より進んでいるか（未 push）である。どの操作がどちらを見るかは watches に宣言する。
// 操作は Check を呼ぶだけで、条件を自分では並べない。返すのは Refusal で、表示は呼び手がそれを言い換える。
//
// Python からの対応: tickets/lifecycle/treegate。APPROVAL_REQUEST・FINISH・REVIEW_REQUEST・READY → Op の定数（request の
// _moved_detail が直に読んでいた unpushed(…, ignore_places=True) → Reviewed）、
// DIRTY_PARENT・UNPUSHED_CODE → Kind の定数、Refusal（line → Line）、check → Check（branch・root・mirror_guidance
// → Options）、tree_dirty → Dirty、diff_paths → DiffPaths、unpushed → Unpushed、in_place → InPlace、
// dirty_refusal → DirtyRefusal。読めないときの None は ok が偽になる。WATCHES は私的（watches）。
package treegate
