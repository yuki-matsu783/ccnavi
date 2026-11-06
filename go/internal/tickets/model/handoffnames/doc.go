// Package handoffnames は下書きの置き場（`wip/handoff/`）での階層名の読み替え。Claude Code 本体は `.claude`
// という名前の階層（深さを問わない）への書き込みを非対話では断るので、下書きの相対パスの中のその階層を
// 表で別の名前に読み替えて置き、取り込むときに戻す（`.claude/settings.json` ⇔ `handoff-.claude/settings.json`）。
//
// 表の元は `.ccnavi/scripts/ccnavi-handoff-names.txt` の 1 か所で、sh（ccnavi-stage.sh）も読む。実行ファイルは
// 同じ中身の写し（このディレクトリの同名のファイル）を埋め込む。写しが元と違えばテストが落ちる。
// 表の名前は ASCII だけで書き、要素ごとに大文字小文字を区別せずに比べる。sh は ASCII しか畳めないので、
// 非 ASCII を含む要素は畳んで一致しても表に当てない。
//
// Python からの対応: ALIASES・load() → Default（*Table。テストは Parse で作った表に差し替える）、parse → Parse、
// to_draft・from_draft・reserved_problem → Table の ToDraft・FromDraft・ReservedProblem、NAMES_FILE → NamesFile。
// 表のファイルの探し方（_names_path）は埋め込みにしたので無い。
package handoffnames
