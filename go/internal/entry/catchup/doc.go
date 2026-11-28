// Package catchup は、手元をリモートに合わせる 2 本の sh（ccnavi-sync.sh・ccnavi-fetch.sh）に答える。
//
// sh は外との出入り（ls-remote・fetch・`ccnavi-review.sh merged`）と、ロック・trap・時間の枠を持つ。ここは、
// その間に挟まる判定（親子のチケットの選び方・統合先の決め方・リモートにあるか・クローズしたか）と、ローカルの git の
// 書き込み（早送り・merge と取りやめ・update-ref・origin/HEAD の向け直し）と、出力の文を持つ。ネットワークには出ない。
//
// 答えは 1 行 1 項目で、行の先頭の語で分ける（answer）。sh は out の行を標準出力に、err の行を標準エラーに出し、
// failed があれば止めた親子のチケットに数え、next の語（fetch・ask など）で次の外との出入りを決める。
//
// sync と fetch が共有するのは、親のワークツリーの選び方（familyAt）、早送り（fastForward）、ホストの既定
// ブランチの読み（hostDefault）と origin/HEAD の向け直し（retarget）、ワークツリーの一覧の解析（treeOf）。
package catchup
