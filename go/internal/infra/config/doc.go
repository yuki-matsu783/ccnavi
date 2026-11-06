// Package config は ccnavi の設定を 1 か所で持つ。環境変数で渡る値の解決（Settings・Load）、
// enable / dry-run / disable のモードと終了コード、秒数のしきい値（Tunable）、判定が使うツール名の組。
//
// 判定が読む値はプロセスの環境から取り、ワークツリーの中の設定ファイルからは取らない。
// エージェントが書けるファイルから読むと、監視される側が次の呼び出しから監視を緩められる。
// 例外は ccnavi 自身のソースツリーだけ（ownSourceTree）。
package config
