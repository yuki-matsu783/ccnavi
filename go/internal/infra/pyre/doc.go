// Package pyre は、Python の re（3.13 の sre）と同じ答えを返す正規表現を提供する。ルールの regex・glob と
// 組み込みの保護の式は、ユーザとこのビルドが Python の書き方で書いたものである。同じ文字列に同じ答えを
// 返さないと、同じルールファイルで判定が変わってしまう。
//
// Go の regexp（RE2）を使わないのは、書ける形も答えも違うからである。組み込みの保護は所有的な繰り返し（`*+`）と
// アトミックグループ（`(?>`）を使う。ルールは Unicode の `\b`・`\w`・`\s`、末尾の改行の手前にも当たる `$`、
// `\Z` を使う。大文字小文字の無視も Python の表（_casefix）で比べる。そのため Python の _parser を写して読み、
// sre と同じ後戻りの規則（空の繰り返しの打ち切り、所有的な繰り返し、先読み・後読み）で照合する。
// 読めない式の文面と位置（`missing ), unterminated subpattern at position 0`）も Python と同じにする。
// 後戻りで照合するので、式によっては Python と同じく、長さに対して指数的に遅くなる。
//
// Python からの対応: re.compile → Compile（読めなければ *Error。このビルドに書いた固定の式は MustCompile）、Pattern.search・match・fullmatch の真偽 →
// Search・Match・FullMatch、search が返す Match → Find、search(s, pos, endpos)・match(s, pos, endpos)・
// finditer(s, pos, endpos) → FindIn・MatchIn・FindAllIn（位置は符号位置）、finditer → FindAll、re.sub（repl に関数を
// 渡す形）→ Sub、Match の start・end・group(0)・group(name) → Found の Start・End・Text・Named、groupindex → Group、pattern → Pattern、re.escape → Escape、
// globmatch.translate（fnmatch.translate の "/" を両方の区切りに当てる）→ TranslateGlob、fnmatch.translate → TranslateFnmatch、
// 区切りの式 `[\\/]` → AnySeparator。入れ子が深すぎて Python が RecursionError で止まる式は ErrTooDeep、
// 繰り返しの数が大きすぎて OverflowError になる式は ErrOverflow になる。
package pyre
