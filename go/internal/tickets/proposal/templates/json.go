package templates

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/infra/yamljson"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/flow"
)

// JSON の答えの形は拡張（ccnavi-board の templatesmodel.ts）との契約。欄を変えるときは拡張も直す。

// JSONVersion は --templates の JSON の版。
const JSONVersion = 1

func problemJSON(p rules.Problem) *pyjson.Object {
	return pyjson.NewObject("severity", string(p.Severity), "detail", p.Detail)
}

func problemsJSON(list []rules.Problem) []any {
	out := []any{}
	for _, p := range list {
		out = append(out, problemJSON(p))
	}
	return out
}

func itemsJSON(list []Item, first int) []any {
	out := []any{}
	for i, item := range list {
		after := make([]any, len(item.After))
		for k, n := range item.After {
			after[k] = n
		}
		out = append(out, pyjson.NewObject("index", i+1, "number", first+i, "type", item.Type,
			"review", needJSON(item.Review), "deferred", item.Deferred, "after", after))
	}
	return out
}

func flowJSON(index int, body *yaml.Map) *pyjson.Object {
	nodes, _ := body.List("nodes")
	kinds := map[string]int{}
	for _, node := range nodes {
		kind := ""
		if m, ok := node.(*yaml.Map); ok {
			if v, has := m.Lookup("type"); has {
				kind = pyfmt.Str(v)
			}
		}
		kinds[kind]++
	}
	names := make([]string, 0, len(kinds))
	for k := range kinds {
		names = append(names, k)
	}
	slices.Sort(names)
	types := pyjson.NewObject()
	for _, k := range names {
		types.Set(k, kinds[k])
	}
	connections, _ := body.List("connections")
	name, _ := body.String("name")
	return pyjson.NewObject("index", index, "name", name, "nodes", len(nodes),
		"connections", len(connections), "node_types", types)
}

func droppedJSON(e *Entry) any {
	if e.Dropped == DropNone {
		return nil
	}
	return pyjson.NewObject("reason", string(e.Dropped), "detail", e.DroppedDetail)
}

// EntryJSON は画面がそのまま描ける 1 本分の答え。
func EntryJSON(e *Entry) *pyjson.Object {
	t := e.Template
	description, when := "", ""
	plan, feedback, flows := []any{}, []any{}, []any{}
	if t != nil {
		description, when = t.Description, t.When
		plan, feedback = itemsJSON(t.Plan, 1), itemsJSON(t.Feedback, len(t.Plan)+1)
		for _, i := range t.FlowIndexes() {
			flows = append(flows, flowJSON(i, t.Flows[i]))
		}
	}
	return pyjson.NewObject("name", e.Name, "layer", e.Layer, "path", e.Rel, "sha", e.SHA, "valid", t != nil,
		"description", description, "when", when, "plan", plan, "feedback", feedback,
		"has_flows", t != nil && len(t.Flows) > 0, "flows", flows,
		"effective", e.Effective, "usable", e.Usable(), "dropped", droppedJSON(e), "writable", e.Writable,
		"problems", problemsJSON(e.Problems()))
}

func layerJSON(l *LayerRead) *pyjson.Object {
	entries := []any{}
	for _, e := range l.Entries {
		entries = append(entries, EntryJSON(e))
	}
	return pyjson.NewObject("name", l.Name, "path", l.Rel, "exists", l.Exists, "unreadable", l.Unreadable,
		"writable", l.Writable, "problems", problemsJSON(l.Problems), "templates", entries)
}

// AsJSON は ccnavi --templates --json の答え。templates は共通 + layer の統合結果（共通の側は統合結果のフェーズ
// 定義で検査し直す。keywords で絞る）、layers は各レイヤーの生の中身、sums はレイヤーごとの統合結果の索引。
func AsJSON(conf *config.Settings, root string, s *Scan, layer string, keywords []string) *pyjson.Object {
	var types *phases.Types
	if s.Layer(layer) != nil {
		types = TypesFor(conf, root, layer)
	}
	chosen := []any{}
	for _, e := range s.Adopted(layer) {
		if e.Layer == config.LayerCommon {
			e = Recheck(e, types, &s.Catalog)
		}
		if Matches(e, keywords) {
			chosen = append(chosen, EntryJSON(e))
		}
	}
	layers := []any{}
	for _, l := range s.All() {
		layers = append(layers, layerJSON(l))
	}
	sums := []any{}
	for _, l := range s.Layers {
		rows := []any{}
		for _, e := range s.Adopted(l.Name) {
			rows = append(rows, pyjson.NewObject("name", e.Name, "layer", e.Layer, "effective", e.Effective,
				"dropped", droppedJSON(e)))
		}
		sums = append(sums, pyjson.NewObject("name", l.Name, "layers", []any{config.LayerCommon, l.Name},
			"templates", rows))
	}
	counts := map[rules.Severity]int{}
	for _, p := range s.Problems() {
		counts[p.Problem.Severity]++
	}
	words := make([]any, len(keywords))
	for i, k := range keywords {
		words[i] = k
	}
	return pyjson.NewObject("version", JSONVersion, "root", root, "layer", layer, "keywords", words,
		"templates", chosen, "layers", layers, "sums", sums,
		"errors", counts[rules.Error], "warns", counts[rules.Warn], "infos", counts[rules.Info])
}

// CheckFile は任意のパスのテンプレートを検査した答え（--templates --check-file）。書かない。名前の期待値は
// ファイル名から拡張子を除いたもの。data は読んだ中身の写し（読めなければ null）で、呼び手が「書こうとした
// 中身を実行ファイルがそのまま読んだか」を見るのに使う。types はこのレイヤーで使えるフェーズ定義。
func CheckFile(conf *config.Settings, root, path, layer string) *pyjson.Object {
	base, stem := stemOf(path)
	types := TypesFor(conf, root, layer)
	var defs []*phases.PhaseType
	for p := range types.All() {
		defs = append(defs, p)
	}
	slices.SortFunc(defs, func(a, b *phases.PhaseType) int { return strings.Compare(a.ID, b.ID) })
	listed := []any{}
	for _, p := range defs {
		listed = append(listed, pyjson.NewObject("id", p.ID, "title", p.Title, "kind", string(p.Kind), "review", p.Review))
	}
	answer := pyjson.NewObject("version", JSONVersion, "ok", true, "layer", layer, "name", stem, "sha", "",
		"valid", false, "problems", []any{}, "data", nil, "plans", []any{}, "types", listed)
	raw, why := flow.ReadBytes(path, "")
	if raw == nil {
		answer.Set("problems", problemsJSON([]rules.Problem{{Severity: rules.Error, Rule: stem, Detail: why}}))
		return answer
	}
	sha := SHA(raw)
	answer.Set("sha", sha)
	var found []rules.Problem
	if !validName(stem) {
		found = append(found, rules.Problem{Severity: rules.Error, Rule: stem, Detail: badFileName(base)})
	}
	t, notes := Parse(raw, stem)
	found = append(found, notes...)
	if t != nil {
		found = append(found, Check(t, types, nil)...)
		answer.Set("valid", true)
		answer.Set("data", yamljson.ToJSON(t.AsData()))
		answer.Set("plans", Graph(t, types, sha))
	}
	answer.Set("problems", problemsJSON(found))
	return answer
}
