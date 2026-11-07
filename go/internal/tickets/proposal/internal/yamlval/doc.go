// Package yamlval は計画のステップの書き戻す形（PlanItem.Raw の JSON の形）を、yaml.Dump と yaml.Equal に渡せる
// YAML の値にする。Raw は JSON のオブジェクトを返すので、yaml に渡せるよう 1 つなら Step、並びなら Steps で写す。
package yamlval
