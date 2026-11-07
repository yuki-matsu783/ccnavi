// Package yaml は、エージェントが書けるファイル（ルール・チケットの frontmatter・設定・フェーズ・
// フロー）の YAML を、Python の実行ファイルが使っていた PyYAML の yaml.safe_load と同じ答えで読む。
// 読める・読めないの境界、組み立てる値、エラーの文面を変えると、同じファイルで判定が変わるので、
// PyYAML の純 Python の読み手（YAML 1.1）を写して持つ。gopkg.in/yaml.v3 は YAML 1.2 寄りで、
// yes/no/on/off の真偽、0 始まりの 8 進、時刻、タブや "?" の受け入れ方が違うため使わない。
// 値は nil・bool・int（範囲を超えれば *big.Int）・float64・string・[]byte・[]any・*Map・Date・
// DateTime・Set・Tuple で返す。上の段は *Map の取り出し（Text・Strings・Map など）で型付きの値へ移す。
//
// Python からの対応: yamlread.load・safe_load → Load、別名を拒む読み手（flow._Loader）→ LoadRefusingAliases。
// 読めない文書は errors.Is(err, ErrUnreadable)（yaml.YAMLError と LoadError に当たる）、別名で断ったものは
// ErrAlias（flow._AliasRefused）。問題と位置は errors.As で *Error の Problem・ProblemMark から読み、
// Problem が空なら Error() を使う（flow._yaml_problem と同じ）。例外の型の名前（type(exc).__name__）は
// *Error の Class。md の頭の frontmatter を `---` の行で切り出す各所の手書きの走査 → FrontBody（囲いの読み方の違いは
// FrontStyle）、ticket.Fence → FrontFence。
//
// 書き出しは PyYAML の純 Python の書き手（SafeDumper）を写した。ccnavi が使う引数の組み合わせ
// （allow_unicode=True・sort_keys=False と width・default_flow_style・default_style='"'）だけを持つ。
// yaml.safe_dump → Dump（引数は DumpOptions）、plan_templates の _Dumper（改行を含む文字列を | で書き、別名を付けない）→
// DumpOptions の Literal と NoAliases、ticket_fields._yaml_scalar → Quoted、RepresenterError → ErrUnrepresentable、
// 上の段が組む dict の代入 → Map.Put、欄を除いた dict の内包 → Map.Without、読んだ値どうしの == → Equal。
package yaml
