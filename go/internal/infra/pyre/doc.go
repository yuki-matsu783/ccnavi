// Package pyre は Python の re（3.13 の sre）と同じ答えを返す正規表現。ルールの regex・glob と組み込みの
// 保護の式は、ユーザとこのビルドが Python の書き方で書いたもので、同じ文字列に同じ答えを返さないと
// 同じルールファイルで判定が変わる。
//
// Go の regexp（RE2）を使わないのは、書ける形も答えも違うため。組み込みの保護は所有的な繰り返し（`*+`）と
// アトミックグループ（`(?>`）を使い、ルールは Unicode の `\b`・`\w`・`\s`、末尾の改行の手前にも当たる `$`、
// `\Z` を使う。大文字小文字の無視も Python の表（_casefix）で比べる。そこで Python の _parser を写して読み、
// sre と同じ後戻りの規則（空の繰り返しの打ち切り、所有的な繰り返し、先読み・後読み）で照合する。
// 読めない式の文面と位置（`missing ), unterminated subpattern at position 0`）も Python と同じにする。
// 後戻りなので、式によっては Python と同じく長さの指数で遅くなる。
//
// Python からの対応: re.compile → Compile（読めなければ *Error）、Pattern.search・match・fullmatch →
// Search・Match・FullMatch、finditer → FindAll、groupindex → Group、pattern → Pattern、re.escape → Escape、
// fnmatch.translate → TranslateGlob。入れ子が深すぎて Python が RecursionError で止まる式は ErrTooDeep、
// 繰り返しの数が大きすぎて OverflowError になる式は ErrOverflow。
package pyre
