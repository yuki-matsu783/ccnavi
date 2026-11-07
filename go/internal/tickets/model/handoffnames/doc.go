// Package handoffnames は、下書きの置き場（`wip/handoff/`）での階層名の読み替えを扱う。Claude Code 本体は、`.claude`
// という名前の階層（深さを問わない）への書き込みを非対話では断る。そこで、下書きの相対パスの中のその階層を、
// 表で別の名前に読み替えて置く。取り込むときには元に戻す（`.claude/settings.json` ⇔ `handoff-.claude/settings.json`）。
//
// 表の元は `.ccnavi/scripts/ccnavi-handoff-names.txt` の 1 か所で、sh（ccnavi-stage.sh）も読む。実行ファイルは同じ
// 中身の写し（このディレクトリの同名のファイル）を埋め込む。写しが元と違えばテストが落ちる。表の名前は ASCII だけで
// 書き、要素ごとに大文字小文字を区別せずに比べる。sh は ASCII しか畳めない。そのため、非 ASCII を含む要素は、畳んで
// 一致しても表に当てない。
package handoffnames
