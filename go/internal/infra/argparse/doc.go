// Package argparse は Python 3.13 の argparse のうち、ccnavi の入口が使う範囲の写しである。引数の読み方
// （`--` の扱い、短いオプションの連結、`--x=v`、位置引数とオプションの交互の読み、余った語）と、
// 誤りの文（`usage: ...` の折り返しと `ccnavi: error: ...`）を Python とバイト単位で同じにする。
//
// 作るのは使う範囲だけ。パーサはいつも add_help=False・allow_abbrev=False で動く（前方一致を受けない）。
// action は store・store_true・append、nargs は 1 つ（既定）・`?`・`*`・整数、type は Go の関数（Int）。
// サブコマンド・相互排他グループ・choices・help の本文（format_help）は使わないので無い。
//
// Parse は誤りを *Error で返し、出力も終了もしない。Python の parser.error に当たる文は ErrorText が組む
// （usage の幅は COLUMNS、無ければ標準出力の端末の幅、どちらも無ければ 80 から決める）。
//
// Python からの対応: ArgumentParser(prog=…) → New、add_argument → Add（引数は Arg）、parse_args → Parse、
// ArgumentError の str → Error.Message、parser.error が標準エラーに書く文 → ErrorText、
// parser._actions → Actions、parser._option_string_actions に在るか → HasOption、type=int → Int、
// getattr(args, dest, default) != default → Namespace.Changed、nargs の "?"・"*" → Optional・ZeroOrMore。
package argparse
