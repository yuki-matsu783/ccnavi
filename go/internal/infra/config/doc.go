// Package config は ccnavi の設定を 1 か所に集める。環境変数で渡る値の解決（Settings・Load）、
// enable / dry-run / disable のモードと終了コード、秒数のしきい値（Tunable）、判定が使うツール名（Tool*）とその組、
// 上の段が state に書き records/prune が消す記録の名前（SelfguardDir・OncePrefix など）、
// 上の段の tickets が置き下の段も名前で見る置き場の名前（OpsDir・DirectPrefix・ArchiveName・WipRoot・HandoffName・
// TodoDir・ReservedBranchIDs）を置く。
//
// 判定が読む値はプロセスの環境から取り、ワークツリーの中の設定ファイルからは取らない。
// エージェントが書けるファイルから読むと、監視される側が次の呼び出しから監視を緩められるからである。
// 例外は ccnavi 自身のソースツリーだけ（ownSourceTree）。
//
// 上の段が直書きしがちな名前もここに置く。sh の名前は ReviewWrapper・StartWrapper・WorktreeWrapper・StageWrapper・
// LauncherScript、Artifact のツール名は ToolArtifact、dry-run の報告の頭（`[ccnavi dry-run] enable would have …`）は
// DryRunWould、`logs/direct-tmp` は DirectTmpDir。common 層の置き場は Settings.CommonLayerDir、
// 秒数や整数の設定値の読みは Tunable.Value（time.Duration）と Int・Finite、
// 止める側に倒す判定は FailClosedEvent・FailClosed。
package config
