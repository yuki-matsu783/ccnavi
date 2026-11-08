// Package request は、レビューの依頼と、依頼の記録からレビューの状態を読む部品を置く。依頼の操作は
// `ccnavi review prepare|requested` と `prepare --check` である。ネットワークには出ない。マージリクエストの中身は、
// sh（`.ccnavi/scripts/ccnavi-review.sh`）が取ってきて `--result` の JSON で渡す。その形が sh との契約である。
//
// 依頼は 2 段に分かれる。Prepare が前提をすべて確かめ、投稿する本文を state の置き場に書く。sh が投稿した結果を受けて、
// Requested が依頼のマーカーを置く。前提の検査は gate の 1 か所にあり、本体と `--check` が同じものを通る。
// 残った指摘の決め方（decide）・親を閉じる操作（close）・コア（confirm）は、ここの部品を読む。部品は、依頼の後に動いたか、
// 結果の照合、未解決の数え方、子の決着とレビュー済みのマーカー、wip/ の片付けである。
package request
