// Package chrome は Chrome 拡張（extensions/chrome/ccnavi-approval）が WASM で呼ぶ入口。要求の JSON を 1 つ受けて
// 答えの JSON を返す（Handle）。拡張はホストの API でブランチの置き場を読み、その中身（snapshot）を渡す。ここは
// ブランチごとの仮のツリーを root に組み、手元と同じ判定（`--agree --preview --json` と tickets/core）を通す。
// 承認先行の一覧も、親子のチケットの見分けも、参照の閉包も、互換の比べもここが出し、拡張は並べるだけにする。
// 拡張で判定し直すと、hook・lint と答えが 2 か所に分かれる。
//
// 書く操作（plan・confirm・decide）は、書くもの（Changes）を値で返すだけで、ホストにもディスクの外にも書かない。
// 拡張がそれを親のブランチ P への 1 コミットにする。同梱の互換の版が統合先の CCNAVI_COMPAT と違えば受けない
// （互換の違う判定をリモートに乗せると、手元で判定し直したときに答えが分かれる）。通らなかった答えは理由コード
// （CORE_*）をキー code に持つ。
//
// 取り込み状態（logs/state/sync/）は組まない。判定は仮のツリーのワークツリーとチケットだけから親子を読む。判定の
// 入力に本文を読めないファイル（バイナリ・シンボリックリンク）があれば、何も判定せず「決まらない」で止める。
//
// 要求と snapshot の形、答えの形は拡張の src/core/ccnavi.ts と対で、版は Schema。
package chrome
