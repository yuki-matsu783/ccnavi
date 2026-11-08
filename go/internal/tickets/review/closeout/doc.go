// Package closeout は、親を閉じる操作を置く。含むものは次の 2 つである。`ccnavi review ready`（と `--check`）は、
// Draft を外す前に行う。`ccnavi --close-early` は、ユーザがターミナルで打つ早めのクローズである（sh は
// ccnavi-user-close-early.sh）。ネットワークには出ない。Draft を外す・返信して解決する・issue を作るのは sh が行う。
// ここは、その前提を確かめ、マーカーと下書きを置く。
//
// sh との契約（変えない）は次のとおり。ready の標準出力は、1 行目がコメントの下書きのパス、2 行目が
// `tree <退避したツリーのルート>` である。wip/ から消したものがあれば、`wip-from <消す前の HEAD>` と
// `wip <消したパス>`（1 行 1 本）が続く。足すのは後ろの行だけにする。下書きのファイル名（readyFile・earlyIssueFile・
// earlyNoteFile）と目印も、sh が拾う。ready の前提は readyGate の 1 か所にあり、本体と `--check` が同じものを通る。
package closeout
