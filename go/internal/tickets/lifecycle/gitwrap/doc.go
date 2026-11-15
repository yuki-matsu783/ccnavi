// Package gitwrap は、git の入口のラッパー（ccnavi-git.sh）が受けた引数を通すかを決める。
//
// ラッパーは `ccnavi git check <呼び方>...` を起動し、git の引数を標準入力に NUL 区切りで渡す（ccnavi の引数として渡すと、
// `-m` のような語を ccnavi 自身のオプションとして読んでしまうため）。答えは `allow`（通す）・`pass`（integrate・
// integrate-done。続きはラッパーが判定する）・`deny <識別子>` と次の行からの拒否の文、のどれか。拒否の文の中の呼び方は、
// ラッパーが渡した `$0` から作るので、どこから打っても合う。
//
// Check は引数だけで決まる判定を持つ: サブコマンドより前のオプション、どのサブコマンドでも許さない形（設定の一時上書き・
// ファイル書き込みや外部コマンド実行に変えるオプション・判定の起点を動かすオプションと、その略形）、読むだけの
// サブコマンド、cat-file・grep・branch・tag・remote・stash・add・commit の形、通さないサブコマンド。
// Judge はそれに続けて、ラッパーの cwd の git の状態とワークスペースを読む判定を持つ: worktree・rm・restore・merge・
// merge-file・checkout・switch・fetch・pull・push（ネットワークに出る push のゲートは、掛けるかだけを決める）。
// オプションは許可リストで読む（Walk）。git の parse-options は長いオプションの略を受けるので、一覧にそのままの
// 表記がある名前だけを通し、まとめた短いオプションは 1 字ずつ読む。
package gitwrap
