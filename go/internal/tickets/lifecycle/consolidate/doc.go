// Package consolidate は統合の子を作る（`ccnavi ticket consolidate <親>`）。親の finish が「判定されていない
// 気づきがある」と止めたとき、メインが打つ。処理すべきものを処理する子 `<親>-00-<連番>`（phase: 0、
// maintenance: learning-consolidate）を doing/ に作る。学びの蓄積が有効なときにシステムが事前に許す限られた保守
// 操作で、ユーザの承認の代わりではない。書いたパスは sh が write-and-push でコミットして push する。開いている統合の
// 子がすでにあれば新しく作らず、書いたパスの一覧に載せる（前回の push が済んでいなければ送り直す）。
//
// Python からの対応: tickets/lifecycle/consolidate を移した。run → Run、check → Check。gate・build・_body は私的。
package consolidate
