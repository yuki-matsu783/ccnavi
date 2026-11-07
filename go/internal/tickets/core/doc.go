// Package core は、取り返しのつかない 2 つの操作の入口になる。1 つめは承認（`--agree`。ターミナル・ボードの `--yes`・
// `--preview`・`--verify`）、2 つめはレビュー済み（`review confirm`）である。どちらも判定（agree.Gather・request の検査）
// を通る。書くものは fsio の溜める段に並べ（agree.PlanBatch・request.SettleAndMark）、ここの書き手（Writer(FS)）が
// ディスクに書く。並べた中で失敗したときの扱い（止める・言って続ける・行を出す）は、書き手の 1 か所で決める。
//
// 承認の JSON の形・ダイジェスト・終了コード（0・1・3・4）は、拡張（extensions/vscode/ccnavi-board の approvemodel.ts）
// と sh との契約である。通らなかった根拠は、文面の頭に `[CORE_*]` を付けて載せる。拡張と試験は、文の言い回しではなく
// この材料で分ける。親のワークツリーが決まらないとき（*approval.NoParentError）は error で返し、入口が受ける。
package core
