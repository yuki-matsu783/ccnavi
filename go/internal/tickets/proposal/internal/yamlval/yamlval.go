package yamlval

import (
	"ccnavi/internal/infra/yamljson"
	"ccnavi/internal/tickets/model/ticket"
)

// Step は計画のステップの書き戻す形（PlanItem.Raw）を YAML の値にする（Raw は JSON のオブジェクトを返す）。
func Step(item *ticket.PlanItem) any {
	return yamljson.FromJSON(item.Raw())
}

// Steps は Step の並び。
func Steps(items []ticket.PlanItem) []any {
	out := make([]any, len(items))
	for i := range items {
		out[i] = Step(&items[i])
	}
	return out
}
