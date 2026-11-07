// Package yamlval は計画のステップの書き戻す形（PlanItem.Raw の JSON の形）を、yaml.Dump と yaml.Equal に渡せる
// YAML の値にする。
//
// Python からの対応: Python は PlanItem.raw() の dict をそのまま yaml に渡していた。Go の Raw は JSON の
// オブジェクトを返すので、ここで YAML の値に写す（1 つ → Step、並び → Steps）。
package yamlval
