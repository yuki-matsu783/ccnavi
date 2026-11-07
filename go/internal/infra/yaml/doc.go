// Package yaml は、エージェントが書けるファイル（ルール・チケットの frontmatter・設定・フェーズ・
// フロー）の YAML を、PyYAML の yaml.safe_load と同じ答えで読む。
// 読める・読めないの境界、組み立てる値、エラーの文面を変えると、同じファイルで判定が変わるので、
// PyYAML の純 Python の読み手（YAML 1.1）に沿って実装する。gopkg.in/yaml.v3 は YAML 1.2 寄りで、
// yes/no/on/off の真偽、0 始まりの 8 進、時刻、タブや "?" の受け入れ方が違うため使わない。
// 値は nil・bool・int（範囲を超えれば *big.Int）・float64・string・[]byte・[]any・*Map・Date・
// DateTime・Set・Tuple で返す。上の段は *Map の取り出し（Text・Map など）で型付きの値へ移す。
//
// 読みの入口は Load と、別名を拒む LoadRefusingAliases。読めない文書は errors.Is(err, errUnreadable) で見分け、
// 別名で断ったものは ErrAlias になる。問題と位置は errors.As で *Error の Problem・ProblemMark から読み、
// Problem が空なら Error() を使う。例外の型の名前は *Error の Class。
// md の頭の frontmatter を `---` の行で切り出すのは FrontBody（囲いの読み方の違いは FrontStyle）、囲いの行は FrontFence。
//
// 書き出しは PyYAML の純 Python の書き手（SafeDumper）に合わせる。ccnavi が使う引数の組み合わせ
// （allow_unicode=True・sort_keys=False と width・default_flow_style・default_style='"'）だけを実装する。
// 入口は Dump（引数は DumpOptions）。改行を含む文字列を | で書き別名を付けない書き方は DumpOptions の Literal と NoAliases、
// 引用符つきの 1 値は Quoted、書けない値は errUnrepresentable。
// 値の操作は、キーの代入が Map.Put、欄を除いた写しが Map.Without、読んだ値どうしの比べが Equal。
package yaml
