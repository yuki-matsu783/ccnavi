// Package argparse は、Python 3.13 の argparse のうち ccnavi の入口が使う範囲を Go で実装したものである。
// 引数の読み方（`--` の扱い、短いオプションの連結、`--x=v`、位置引数とオプションの交互の読み、余った語）と、
// 誤りの文（`usage: ...` の折り返しと `ccnavi: error: ...`）は、Python の argparse とバイト単位で同じ答えにする。
// 利用者に見える誤りの文面を変えないための約束である。
//
// 作るのは使う範囲だけにする。パーサはいつも add_help=False・allow_abbrev=False で動く（前方一致を受け付けない）。
// action は store・store_true・append、nargs は 1 つ（既定）・`?`・`*`・整数、type は Go の関数（Int）に限る。
// サブコマンド・相互排他グループ・choices・help の本文（format_help）は使わないので作らない。
//
// Parse は誤りを *Error で返す。出力も終了もしない。誤りの文は ErrorText が組む。
// usage の幅は COLUMNS、無ければ標準出力の端末の幅、どちらも無ければ 80 から決める。
package argparse
