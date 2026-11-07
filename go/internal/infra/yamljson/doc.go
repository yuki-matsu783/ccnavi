// Package yamljson は YAML の値（infra/yaml）と JSON の値（infra/pyjson）の間の写し替え。yaml と pyjson は同じ段で
// 互いを読めないので、両方を読む変換はここに置く。
//
// Python からの対応: json.loads(json.dumps(v, default=str))（YAML の値を JSON で見せる・比べる）→ ToJSON、
// 組んだ dict を yaml.safe_dump に渡す（Go では JSON の形から YAML の形へ）→ FromJSON。
package yamljson
