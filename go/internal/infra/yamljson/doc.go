// Package yamljson は YAML の値（infra/yaml）と JSON の値（infra/pyjson）の間の写し替え。yaml と pyjson は同じ層で
// 互いを読めないので、両方を読む変換はここに置く。
//
// ToJSON は YAML の値を JSON で見せる・比べるための変換（文字列にできない値は str() にする）、
// FromJSON は JSON の形から YAML の形へ戻す変換。
package yamljson
