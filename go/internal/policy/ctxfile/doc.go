// Package ctxfile は、当たったルールがモデルへ渡す文（additionalContext）を組む。文、ファイルの本文
// （additionalContextFile）、1 度だけ渡す文（…Once）の 3 つを並べる。どの回に渡すかを決める every と、
// その数えの記録（state の `once-*.json`・`stop-*.json`）も持つ。文脈はセッションと、サブエージェントならその 1 回の起動で分ける。
//
// 読むのはワークスペースの中だけにする。絶対パスと、`..` で上に出るパスは lint が止め、実行時も読まない。
// ルールから任意のファイルをモデルに渡せる形にしないためである。本文は固定の上限で切り、切ったことを末尾に書く。
// 黙って切ると、モデルは途中で終わる文を全部だと思って読んでしまう。
//
// Python からの対応: deliver と for_rules → Deliver（for_rules は 2 つ目の戻り値を捨てる呼び方）、
// unsure_speaks=False → Options.QuietWhenUnsure、load → Body、bad_path・locate・over_limit・bases・
// stop_path・forget → BadPath・Locate・OverLimit・Bases・StopPath・Forget、told_refs・remember_refs・
// told_peers・remember_peers → ToldRefs・RememberRefs・ToldPeers・RememberPeers（set は map[string]bool）、
// MAX_CHARS → MaxChars、ONCE_KEEP_DAYS → onceKeep。`once-`・`stop-` の頭は config.OncePrefix・StopPrefix
// （records/prune が同じ定数で消す）。fsio.safe_name(x) or 既定 → fsio.SafeNameOr。組み込みの
// 案内を additionalContextOnce の入れ物に包んで渡す形（hint・skills・guard が書いていた）→ DeliverOnce（Once）。
package ctxfile
