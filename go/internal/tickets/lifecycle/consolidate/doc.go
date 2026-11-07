// Package consolidate は、統合の子を作る（`ccnavi ticket consolidate <親>`）。親の finish が「判定されていない気づき
// がある」と止めたときに、メインが打つ。処理すべきものを処理する子 `<親>-00-<連番>`（phase: 0、
// maintenance: learning-consolidate）を doing/ に作る。
//
// これは、学びの蓄積が有効なときにシステムが事前に許す、限られた保守操作である。ユーザの承認の代わりではない。
// 書いたパスは sh が write-and-push でコミットして push する。開いている統合の子がすでにあれば、新しく作らず、
// 書いたパスの一覧に載せる。前回の push が済んでいなければ、送り直す。
package consolidate
