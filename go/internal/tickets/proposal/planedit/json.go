package planedit

import (
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ticket"
)

// 答えの JSON は拡張（計画編集タブ・承認の画面）との契約。欄の順も Python の dict と同じにする。

type jsonObject = pyjson.Object

func obj(kv ...any) *jsonObject { return pyjson.NewObject(kv...) }

func ints(ns []int) []any {
	out := make([]any, len(ns))
	for i, n := range ns {
		out[i] = n
	}
	return out
}

func strs(ss []string) []any {
	out := make([]any, len(ss))
	for i, s := range ss {
		out[i] = s
	}
	return out
}

func problemTexts(ps []rules.Problem) []any {
	out := make([]any, len(ps))
	for i, p := range ps {
		out[i] = p.String()
	}
	return out
}

// needJSON は bool|None の宣言。
func needJSON(n ticket.Need) any {
	if !n.Said() {
		return nil
	}
	return n == ticket.Needed
}

// optional は Python の dict.get（無ければ None）。
func optional(n int, ok bool) any {
	if !ok {
		return nil
	}
	return n
}
