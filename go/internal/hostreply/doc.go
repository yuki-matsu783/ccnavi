// Package hostreply は、GitHub・GitLab の応答（sh が取ってきた JSON）の読み方を置く。MR・スレッド・レビューの
// 読みと取ってきた時点の状態の組み立て、失敗の返事の分類、投稿の応答の読み、書く前の確かめの材料、MR の本文が閉じる
// issue の読みを持つ。ネットワークには出ず、チケットの型も知らない。
// start・branch・review の各操作と、Chrome 拡張の WASM（entry/chrome）が同じ読みを共有する。
package hostreply
