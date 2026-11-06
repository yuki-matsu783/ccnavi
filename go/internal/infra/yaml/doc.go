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
// *Error の Class。
package yaml
