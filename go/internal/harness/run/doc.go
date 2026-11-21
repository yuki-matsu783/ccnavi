// Package run は、テストから ccnavi と sh を 1 回起こす（tests/inproc.py の写し）。
//
//	r := run.Ccnavi(t, []string{"--root", root, "text", "NEXT_SAMPLE"}, run.Opts{Input: "", Env: map[string]string{...}})
//	r := run.Sh(t, "ccnavi-start.sh", []string{"--help"}, run.Opts{Dir: root})
//
// Opts は省ける。結果は Result{Code, Stdout, Stderr}（CRLF は LF に揃える）。環境は exec.Cmd に明示して渡し、
// テストのプロセスの環境は書き換えない（t.Setenv を使わないので t.Parallel と一緒に使える）。Opts.Env は Main が整えた
// プロセスの環境への差分（意味は Opts.Env のコメント）。
// 実行ファイルは exe が用意する。
package run
