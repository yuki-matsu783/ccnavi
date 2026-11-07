package drafts

import (
	"sort"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/proposal/templates"
	"ccnavi/internal/tickets/sync/handoff"
)

// rootLabel はワークスペースのルートのツリー（名前が空）を人に見せるときの言い方。
const rootLabel = "ワークスペースルート"

func treeLabel(name string) string {
	if name == "" {
		return rootLabel
	}
	return name
}

// Draft は下書きの置き場の <名前>.yml 1 本。layer は取り込み先のレイヤー（検査はこのフェーズ定義で行う）、
// target は取り込み先（ツリーからの相対）、also は同じ中身の下書きがあるほかのツリーの名前（ルートは ""）。
type Draft struct {
	tree   tree.Tree
	layer  string
	Entry  *templates.Entry
	target string
	also   []string
}

// importable は読めて error が無いか（取り込み先の定義での検査は別）。
func (d *Draft) importable() bool {
	if d.Entry.Template == nil {
		return false
	}
	for _, p := range d.Entry.Problems() {
		if p.Severity == rules.Error {
			return false
		}
	}
	return true
}

// Place は置き場そのものの苦情（置き場の相対パスと苦情）。
type Place struct {
	rel     string
	problem rules.Problem
}

// Scanned は全ツリーの下書き。
type Scanned struct {
	Drafts []*Draft
	places []Place
}

func known(conf *config.Settings, root string) []string {
	var out []string
	for _, l := range ruleload.Layers(conf, root) {
		out = append(out, l.Name)
	}
	return out
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// targetLayer は下書きの取り込み先のレイヤー。共通・自身（プロジェクトのツリーならそのプロジェクト）・プロジェクト。
func targetLayer(conf *config.Settings, root string, where tree.Tree, key string) string {
	names := known(conf, root)
	parts := splitKey(key)
	if parts[0] == "projects" {
		for _, n := range names {
			if handoff.FoldPart(n) == parts[1] {
				return n
			}
		}
		return config.LayerSelf
	}
	if parts[2] == config.LayerCommon {
		return config.LayerCommon
	}
	if contains(names, where.Project) {
		return where.Project
	}
	return config.LayerSelf
}

// Scan は全ツリー（ワークスペース・プロジェクト・ワークツリー）のテンプレートの下書きを読んで、取り込み先の
// レイヤーのフェーズ定義（共通は共通だけ、ほかは共通 + レイヤー）で検査する。同じ中身の下書きが複数のツリーに
// 在れば（親のブランチに乗ったものが子のワークツリーにも checkout される）、最初の 1 本にまとめる。
// catalog が nil なら読む。
func Scan(conf *config.Settings, root string, catalog *flow.Catalog) *Scanned {
	if catalog == nil {
		c := flow.LoadCatalog(root)
		catalog = &c
	}
	out := &Scanned{}
	// 同じ名前・同じ中身（SHA）の下書きは 1 本にまとめ、ほかのツリーの名前は also に足す。
	// 中身が読めず SHA が無いものは、同じと言えないのでまとめない。
	type seenKey struct{ name, sha string }
	seen := map[seenKey]*Draft{}
	said := map[string]bool{}
	for _, where := range approval.Trees(conf, root) {
		found, _, refused := handoff.Walk(where.Root)
		if refused != "" && !said[refused] {
			said[refused] = true
			out.places = append(out.places, Place{place.Handoff,
				rules.Problem{Severity: rules.Error, Detail: place.Handoff + ": " + refused}})
		}
		for _, f := range found {
			if k := handoff.KindOf(f.Key); k == nil || k.ID != handoff.Template {
				continue
			}
			entry := templates.ReadEntry(templates.LayerDraft, where.Root, f.Path, true)
			layer := targetLayer(conf, root, where, f.Key)
			if entry.Template != nil {
				entry.Checks = append(entry.Checks, templates.Check(entry.Template, templates.TypesFor(conf, root, layer), catalog)...)
			}
			key := seenKey{entry.Name, entry.SHA}
			if first := seen[key]; entry.SHA != "" && first != nil {
				first.also = append(first.also, where.Name)
				continue
			}
			d := &Draft{tree: where, layer: layer, Entry: entry, target: f.Rel}
			if entry.SHA != "" {
				seen[key] = d
			}
			out.Drafts = append(out.Drafts, d)
		}
	}
	// 表示の順を、名前 → パス → ツリー名で固定する（ツリーの走査順に依らない）。
	sort.SliceStable(out.Drafts, func(i, j int) bool {
		a, b := out.Drafts[i], out.Drafts[j]
		if a.Entry.Name != b.Entry.Name {
			return a.Entry.Name < b.Entry.Name
		}
		if a.Entry.Rel != b.Entry.Rel {
			return a.Entry.Rel < b.Entry.Rel
		}
		return a.tree.Name < b.tree.Name
	})
	return out
}

// layersWith は同じ名前のテンプレートが在るレイヤー（共通を先に）。
func layersWith(scanned *templates.Scan, name string) []any {
	out := []any{}
	for _, view := range scanned.All() {
		for _, e := range view.Entries {
			if e.Name == name {
				out = append(out, view.Name)
				break
			}
		}
	}
	return out
}

// draftKeys は templates.EntryJSON から下書きの答えに残す欄（この順）。
var draftKeys = []string{"name", "path", "sha", "valid", "description", "when", "plan", "feedback",
	"has_flows", "flows", "problems"}

// DraftJSON はボードのテンプレートの画面が下書きを並べる 1 本分の答え。path は下書き（ツリーからの相対）、target は
// 取り込み先、layer は取り込み先のレイヤー、checked_with は検査に使ったフェーズ定義の層、text は下書きの本文そのまま
// （reveal はしない）、exists_in は同じ名前のテンプレートが在るレイヤー。problems の深刻度は下書きの検査のまま。
func DraftJSON(d *Draft, scanned *templates.Scan) *pyjson.Object {
	row := templates.EntryJSON(d.Entry)
	out := pyjson.NewObject()
	for _, k := range draftKeys {
		out.Set(k, row.Value(k))
	}
	also := make([]any, len(d.also))
	for i, a := range d.also {
		also[i] = a
	}
	out.Set("tree", d.tree.Name)
	out.Set("target", d.target)
	out.Set("layer", d.layer)
	out.Set("checked_with", d.layer)
	out.Set("importable", d.importable())
	out.Set("exists_in", layersWith(scanned, d.Entry.Name))
	out.Set("also_in", also)
	out.Set("text", handoff.DraftText(d.Entry.Raw))
	return out
}

// LintProblems は --lint に出す苦情。下書きに効力は無いので、error も warn に下げる。
func LintProblems(found *Scanned) []rules.Problem {
	var out []rules.Problem
	lower := func(s rules.Severity) rules.Severity {
		if s == rules.Error {
			return rules.Warn
		}
		return s
	}
	for _, pl := range found.places {
		detail := pl.problem.Detail
		if detail == "" {
			detail = pl.rel
		}
		out = append(out, rules.Problem{Severity: lower(pl.problem.Severity), Detail: "下書き " + detail})
	}
	for _, d := range found.Drafts {
		for _, p := range d.Entry.Problems() {
			out = append(out, rules.Problem{Severity: lower(p.Severity), Detail: "下書き " + d.Entry.Rel + ": " + p.Detail})
		}
	}
	return out
}
