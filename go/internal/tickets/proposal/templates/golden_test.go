package templates

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yamljson"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/testkit"
)

// 記録した答え（testdata/templates.json）と突き合わせる。ワークスペースを読ませ、--templates の JSON
// （一覧・統合結果・検査の苦情）、--check-file の答え、テンプレートの読みと書き戻し、使う側の組み方が 1 字も
// 違わないことを見る。JSON の形は拡張との契約なので、記録した出力のまま比べる。

type resolveRow struct {
	Why          string   `json:"why"`
	Name         string   `json:"name"`
	Layer        string   `json:"layer"`
	Warns        []string `json:"warns"`
	Notes        []string `json:"notes"`
	Plan         []any    `json:"plan"`
	Feedback     []any    `json:"feedback"`
	Drafts       [][3]any `json:"drafts"`
	DraftsWhy    string   `json:"draftsWhy"`
	ChildProblem string   `json:"childProblem"`
}

type parseRow struct {
	Problems [][3]string `json:"problems"`
	Dump     *string     `json:"dump"`
	Check    [][3]string `json:"check"`
}

type templatesAnswers struct {
	Lists    []string     `json:"lists"`
	Checks   []string     `json:"checks"`
	Parses   []parseRow   `json:"parses"`
	Names    []string     `json:"names"`
	Resolves []resolveRow `json:"resolves"`
	Counts   [2]any       `json:"counts"`
	Bare     [2]string    `json:"bare"`
}

const phasesYML = `
version: 1
phases:
  research:
    kind: work
    title: 調査
    review: false
    scope: ["wip/research/*"]
    deliverables: ["wip/research/summary.md"]
    agent: explorer
    skill: process-research
    when: 既存の振る舞いが分からないとき
  design:
    kind: work
    title: 設計
    review: true
    scope: ["wip/design/*"]
  acceptance:
    kind: work
    title: 受入テスト作成
    review: true
    scope: ["tests/*"]
  implement:
    kind: work
    title: 実装とテスト
    review: true
    scope: ["src/*", "tests/*"]
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: true
    scope: inherit
`

const flowBody = `    id: wf-impl
    name: 実装の手順
    version: 1.0.0
    nodes:
      - {id: s, type: start, name: 開始, position: {x: 0, y: 0}, data: {}}
      - {id: p, type: prompt, name: 実装, position: {x: 1, y: 0}, data: {prompt: "先にテスト\n次に実装\n"}}
      - {id: a, type: subAgent, name: 調べ, position: {x: 1, y: 1}, data: {builtInType: nobody}}
      - {id: e, type: end, name: 終了, position: {x: 2, y: 0}, data: {}}
    connections:
      - {id: c1, from: s, to: p, fromPort: output, toPort: input}
      - {id: c2, from: p, to: e, fromPort: output, toPort: input}
`

const featureYML = `name: feature
description: 機能を設計から実装まで進める
when: 新しい機能を足すとき
plan:
  - research
  - {type: design, deferred: true, after: [1]}
  - {type: implement, after: [2]}
feedback:
  - implement-feedback
  - {type: implement-feedback, after: [1, 1]}
flows:
  "3":
` + flowBody + `owner: someone
`

const researchYML = `name: research
description: 調べてから設計する
plan:
  - research
  - {type: design, after: [1]}
`

func tmpl(name, plan string) string {
	return "name: " + name + "\ndescription: 試し\nplan:\n" + plan
}

func templatesWorkspace(t *testing.T, phases bool) string {
	t.Helper()
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	common := filepath.Join(root, ".ccnavi", "config", "common")
	own := filepath.Join(root, ".ccnavi", "config", "project", "templates")
	lib := filepath.Join(root, "projects", "lib", ".ccnavi", "config", "project", "templates")
	if err := os.MkdirAll(filepath.Join(root, "projects", "lib", ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	if phases {
		testkit.Put(t, filepath.Join(common, "phases.yml"), phasesYML)
	}
	files := map[string]string{
		filepath.Join(common, "templates", "research.yml"): researchYML,
		filepath.Join(common, "templates", "broken.yml"):   "name: [\n",
		filepath.Join(common, "templates", "dup.yml"):      strings.ReplaceAll(researchYML, "research\ndesc", "dup\ndesc"),
		filepath.Join(common, "templates", "clash.yml"):    tmpl("clash", "  - design\n"),
		filepath.Join(common, "templates", "notes.txt"):    "x",
		filepath.Join(common, "templates", ".swap.yml"):    "x",
		filepath.Join(own, "feature.yml"):                  featureYML,
		filepath.Join(own, "dup.yml"):                      strings.ReplaceAll(researchYML, "research\ndesc", "dup\ndesc"),
		filepath.Join(own, "clash.yml"):                    tmpl("clash", "  - {type: design, review: false}\n") + "when: 別\n",
		filepath.Join(own, "bad name.yml"):                 researchYML,
		filepath.Join(own, "other.yml"):                    researchYML,
		filepath.Join(own, "two-ends.yml"):                 tmpl("two-ends", "  - design\n  - acceptance\n"),
		filepath.Join(own, "after.yml"):                    tmpl("after", "  - design\n  - {type: implement, after: [2]}\n  - {type: implement, after: [0, 5]}\n"),
		filepath.Join(own, "defer.yml"):                    tmpl("defer", "  - {type: design, deferred: true}\n  - implement\n"),
		filepath.Join(own, "defer-norev.yml"):              tmpl("defer-norev", "  - {type: design, deferred: true}\n  - {type: research, after: [1]}\n"),
		filepath.Join(own, "last-defer.yml"):               tmpl("last-defer", "  - design\n  - {type: implement, after: [1], deferred: true}\n"),
		filepath.Join(own, "both.yml"):                     tmpl("both", "  - {type: design, review: false, deferred: true}\n  - {type: implement}\n"),
		filepath.Join(own, "unknown.yml"):                  tmpl("unknown", "  - docs\n"),
		filepath.Join(own, "kind.yml"):                     tmpl("kind", "  - implement-feedback\n") + "feedback:\n  - design\n",
		filepath.Join(own, "flows-out.yml"):                tmpl("flows-out", "  - design\n") + "flows:\n  0: {nodes: []}\n  3: {nodes: []}\n",
		filepath.Join(own, "flows-bad.yml"):                tmpl("flows-bad", "  - design\n") + "flows:\n  1:\n    id: x\n    name: y\n",
		filepath.Join(own, "flows-lint.yml"):               tmpl("flows-lint", "  - design\n") + "flows:\n  1:\n    id: x\n    nodes:\n      - {id: s, type: start, name: a, position: {x: 0, y: 0}, data: {}}\n",
		filepath.Join(own, "hidden.yml"):                   tmpl("hidden", "  - design\n") + "when: \"x\\u202ey\\u200b\"\n",
		filepath.Join(own, "todo.yml"):                     strings.Replace(tmpl("todo", "  - design\n"), "試し", "TODO 試し", 1),
		filepath.Join(own, "big.yml"):                      tmpl("big", "  - design\n") + "# " + strings.Repeat("x", 300000) + "\n",
		filepath.Join(lib, "libonly.yml"):                  tmpl("libonly", "  - design\n  - {type: implement, after: [1]}\n"),
	}
	for path, text := range files {
		testkit.Put(t, path, text)
	}
	return root
}

var parseCases = []struct{ text, stem string }{
	{researchYML, "research"},
	{researchYML, "other"},
	{"\ufeff" + researchYML, "research"},
	{"name: a\nplan: [x]\n", "a"},
	{"name: a\ndescription: d\n", "a"},
	{"name: a\ndescription: d\nplan: []\n", "a"},
	{"name: a\ndescription: d\nplan: [x]\nflows: [1]\n", "a"},
	{"name: a\ndescription: d\nplan: [x]\nflows: {a: {}, 1: 2, \"01\": {}, true: {}}\n", "a"},
	{"name: a\ndescription: d\nplan: [{type: x, after: 1}]\n", "a"},
	{"name: a\ndescription: d\nplan: [{type: x, after: [true]}]\n", "a"},
	{"name: a\ndescription: d\nplan: [{type: x, after: [1.0]}]\n", "a"},
	{"name: a\ndescription: d\nplan: [\"  \", 3, {type: \"\"}, {type: y, extra: 1, 7: 2}]\n", "a"},
	{"name: a\ndescription: 7\nwhen: [1]\nplan: [x]\n", "a"},
	{"name: \"-bad\"\ndescription: d\nplan: [x]\n", ""},
	{"name: 3\nplan: [x]\n", ""},
	{"- a\n", "a"},
	{"", "a"},
	{"name: a\ndescription: &d x\nplan: [a]\nwhen: *d\n", "a"},
	{"name: \xff\xfe\n", "a"},
	{"name: [\n", "a"},
	{"name: a\ndescription: \"  d  \"\nwhen: w\nplan: [x, {type: y, review: true, after: [1]}]\nfeedback: [{type: f, after: [3]}]\nz: 1\n1: [2]\n", "a"},
	{"name: t\ndescription: d\nplan: [design]\nflows:\n  1:\n    nodes:\n      - {id: p, data: {prompt: \"一行目\\n二行目\\n\"}}\n      - {id: q, data: {prompt: \"行末 \\n次\\n\"}}\n      - {id: r, data: {prompt: \"tab\\tx\\n次\\n\"}}\n      - {id: s, data: {prompt: \"末尾\\n\\n\"}}\n      - {id: u, data: {prompt: \"a\\r\\nb\\n\"}}\n", "t"},
}

var nameCases = []string{"feature", "a", "v1.2_x", "console", "com10", strings.Repeat("A", 64),
	"con", "CON", "COM9", "lpt1", "con.x", "nul.tar.gz", "a.", "", "-x", "a b", "a/b", strings.Repeat("A", 65), "abc\n"}

func TestMatchesGolden(t *testing.T) {
	root := templatesWorkspace(t, true)
	bare := templatesWorkspace(t, false)
	own := filepath.Join(root, ".ccnavi", "config", "project", "templates")
	lists := [][2]any{{"self", []string{}}, {"lib", []string{}}, {"self", []string{"機能", "RESEARCH", ""}}, {"nothing", []string{}}}
	var checks [][2]string
	for _, name := range []string{"feature", "after", "unknown", "kind", "flows-bad", "flows-lint", "hidden", "bad name", "big", "missing"} {
		checks = append(checks, [2]string{filepath.Join(own, name+".yml"), "self"})
	}
	checks = append(checks, [2]string{filepath.Join(own, "feature.yml"), "common"},
		[2]string{filepath.Join(own, "feature.yml"), "lib"})
	resolves := [][2]string{{"self", "feature"}, {"self", "research"}, {"self", "clash"}, {"self", "dup"},
		{"self", "broken"}, {"self", "two-ends"}, {"self", "none"}, {"nope", "feature"}, {"lib", "libonly"}, {"lib", "feature"}}
	var parses [][2]string
	for _, c := range parseCases {
		parses = append(parses, [2]string{base64.StdEncoding.EncodeToString([]byte(c.text)), c.stem})
	}
	bareFile := filepath.Join(bare, ".ccnavi", "config", "project", "templates", "feature.yml")
	want := golden.Load[templatesAnswers](t, "templates")

	conf, _ := config.Load(root)
	s := Load(conf, root)
	dump := func(v any) string {
		text, err := pyjson.Dumps(v, pyjson.Options{Indent: 1})
		if err != nil {
			t.Fatal(err)
		}
		return strings.ReplaceAll(text, root, "<root>")
	}
	for i, l := range lists {
		if got := dump(AsJSON(conf, root, s, l[0].(string), l[1].([]string))); got != want.Lists[i] {
			t.Errorf("AsJSON(%v):\n%s\nwant:\n%s", l, got, want.Lists[i])
		}
	}
	for i, c := range checks {
		if got := dump(CheckFile(conf, root, c[0], c[1])); got != want.Checks[i] {
			t.Errorf("CheckFile(%v):\n%s\nwant:\n%s", c, got, want.Checks[i])
		}
	}
	for i, c := range parseCases {
		tm, notes := Parse([]byte(c.text), c.stem)
		row := parseRow{Problems: probs(notes)}
		if tm != nil {
			d := Dump(tm)
			row.Dump = &d
			row.Check = probs(Check(tm, nil, nil))
		}
		if got, w := jsonOf(t, row), jsonOf(t, want.Parses[i]); got != w {
			t.Errorf("Parse(%q):\n%s\nwant:\n%s", c.text, got, w)
		}
	}
	for i, name := range nameCases {
		if got := NewNameProblem(name); got != want.Names[i] {
			t.Errorf("NewNameProblem(%q) = %q, want %q", name, got, want.Names[i])
		}
	}
	for i, r := range resolves {
		found, why := Resolve(conf, root, r[0], r[1])
		row := resolveRow{Why: strings.ReplaceAll(why, root, "<root>")}
		if found != nil {
			plan, feedback := Absolute(found.Template)
			drafts, dwhy := FlowDrafts("feature-9", found.Template)
			row.Name, row.Layer = found.Template.Name, found.Entry.Layer
			row.Warns, row.Notes, row.Drafts = append([]string{}, found.Warns...), append([]string{}, found.Notes...), [][3]any{}
			row.Plan, row.Feedback = itemData(t, plan), itemData(t, feedback)
			for _, d := range drafts {
				row.Drafts = append(row.Drafts, [3]any{d.Child, d.Index, d.Text})
			}
			row.DraftsWhy, row.ChildProblem = dwhy, ChildProblem(strings.Repeat("x", 60), found.Template)
		}
		if got, w := jsonOf(t, row), jsonOf(t, want.Resolves[i]); got != w {
			t.Errorf("Resolve(%v):\n%s\nwant:\n%s", r, got, w)
		}
	}
	if got := jsonOf(t, [2]any{s.HasAny(), len(s.Problems())}); got != jsonOf(t, want.Counts) {
		t.Errorf("HasAny・Problems = %s, want %v", got, want.Counts)
	}

	// フェーズ定義が無いワークスペース: 型は確かめず、warn を 1 つ言う。
	bareConf, _ := config.Load(bare)
	bareDump := func(v any) string { return strings.ReplaceAll(dump(v), bare, "<root>") }
	if got := bareDump(CheckFile(bareConf, bare, bareFile, "self")); got != want.Bare[0] {
		t.Errorf("phases.yml が無いときの CheckFile:\n%s\nwant:\n%s", got, want.Bare[0])
	}
	if got := bareDump(AsJSON(bareConf, bare, Load(bareConf, bare), "self", nil)); got != want.Bare[1] {
		t.Errorf("phases.yml が無いときの AsJSON:\n%s\nwant:\n%s", got, want.Bare[1])
	}
}

func probs(list []rules.Problem) [][3]string {
	out := [][3]string{}
	for _, p := range list {
		out = append(out, [3]string{string(p.Severity), p.Rule, p.Detail})
	}
	return out
}

// itemData は項目の一覧を、記録と比べられるよう JSON で読み直した形。
func itemData(t *testing.T, list []Item) []any {
	out := []any{}
	for _, item := range list {
		text, err := pyjson.Dumps(yamljson.ToJSON(item.asData(0)), pyjson.Options{})
		if err != nil {
			t.Fatal(err)
		}
		var v any
		if err := json.Unmarshal([]byte(text), &v); err != nil {
			t.Fatal(err)
		}
		out = append(out, v)
	}
	return out
}

func jsonOf(t *testing.T, v any) string {
	t.Helper()
	text, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(text)
}
