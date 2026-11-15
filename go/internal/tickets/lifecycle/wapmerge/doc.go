// Package wapmerge は、write-and-push の親のワークツリーへのローカルの書き込みと戻しを持つ。push と ls-remote、
// ロックは sh（ccnavi-common-write-and-push.sh）で、ここはネットワークに出ない。
//
// 合流の前の検査（`ccnavi write-and-push merge <子>`。Plan）は git を読むだけで、答えは 1 行 1 項目（`<キー> <値>`。
// sh は jq を使わない）、終了コードは検査の結果によらず 0 になる。
//
//   - `skip <理由>`: 合流しない（親の finish、作業中でない子など）。sh は `ticket finish` へ進み、止める理由は
//     そちらが言う。
//   - `stop <理由>` と続く `hint <案内>`: 何も変えずに止める。
//   - 合流してよいなら `child`・`tree`・`head`・`base`・`merge yes|no` を答える（子の先頭が親の先頭の祖先なら `no`）。
//
// 書き込みと戻し（local.go）は、途中の操作の確かめ（Busy）、一覧のパスだけのコミット（Commit・Unstage）、合流
// （Begin・Merging の Commit と Abort）、書いたものの戻しと比較つきの戻し（Restore の Written と Undo）。
// 呼ぶのは実行ファイルの `--record-commit` の 1 回の起動と `write-and-push busy`・`commit`・`undo`・`landed`。
package wapmerge
