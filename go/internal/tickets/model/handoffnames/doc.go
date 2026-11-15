// Package handoffnames は、下書きの置き場（`wip/handoff/`）での階層名の読み替えを扱う。Claude Code 本体は、`.claude`
// という名前の階層（深さを問わない）への書き込みを非対話では断る。そこで、下書きの相対パスの中のその階層を、
// 表で別の名前に読み替えて置く。取り込むときには元に戻す（`.claude/settings.json` ⇔ `handoff-.claude/settings.json`）。
//
// 表の元は `.ccnavi/scripts/ccnavi-handoff-names.txt` の 1 か所。実行ファイルは同じ中身の写し（このディレクトリの
// 同名のファイル）を埋め込み、下書きを作る側（ccnavi-stage.sh の本体の stage）と取り込む側（handoff）の両方がそれを使う。
// 写しが元と違えばテストが落ちる。表の名前は ASCII だけで書き、要素ごとに大文字小文字を区別せずに比べる。非 ASCII を
// 含む要素は、畳んで一致しても表に当てない（畳み方が機械で割れる字で、下書きと元が食い違わないように）。
package handoffnames
