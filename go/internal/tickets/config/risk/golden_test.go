package risk

import (
	"encoding/json"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/rules"
)

// 見本と突き合わせる。対象は parse・merge・evaluate・judge_prompt。配点は承認の判定（HIGH 以上は宣言に
// 関わらずレビューが要る）に効くので、読める・読めないの境界、苦情の文、点と内訳を表で厚く見る。

type riskDefinition struct {
	Levels    map[string]int `json:"levels"`
	Source    string         `json:"source"`
	Dropped   []string       `json:"dropped"`
	Fallback  string         `json:"fallback"`
	Factors   [][]any        `json:"factors"`
	LevelOf   []string       `json:"levelOf"`
	Effective map[string]int `json:"effective"`
}

type riskDefRow struct {
	Problems   [][]string      `json:"problems"`
	Definition *riskDefinition `json:"definition"`
}

type riskScoreRow struct {
	Record  any      `json:"record"`
	Lines   []string `json:"lines"`
	Pending []string `json:"pending"`
	Summary string   `json:"summary"`
	Prompt  string   `json:"prompt"`
}

type riskAnswers struct {
	Defs   []riskDefRow   `json:"defs"`
	Merges []riskDefRow   `json:"merges"`
	Scores []riskScoreRow `json:"scores"`
}

func factors(lines ...string) string {
	return "version: 1\nfactors:\n" + strings.Join(lines, "\n") + "\n"
}

const sampleRisk = `version: 1
levels: {medium: 20, high: 40, critical: 70}
factors:
  - {id: big-diff, points: 25, lines_over: 5, message: 行数が多い}
  - {id: many-files, points: 15, files_over: 2, message: ファイルが多い}
  - {id: ci, points: 35, glob: ".github/**", max: 35, message: CI に触った}
  - {id: deletes, points: 20, deleted_over: 0, message: 消したファイルがある}
  - {id: untested, points: 30, judge: " テストの無い振る舞いの変更を含むか ", message: テスト無し}
  - {id: src, points: 10, lines_over: 3, include: ['src/'], exclude: ['src/gen/**', '*.lock']}
`

var layerHomes = []string{".ccnavi/scripts/"}

var riskDefCases = []string{
	sampleRisk,
	"version: 2\n",
	"version: 1.0\n",
	"version: true\nfactors: []\n",
	"version: '1'\n",
	"- 1\n",
	"version: 1\nlevels: [1]\n",
	"version: 1\nlevels: {medium: 50, high: 40}\n",
	"version: 1\nlevels: {medium: 5, high: 10, severe: 99, low: 1, a b: 2}\n",
	"version: 1\nlevels: {medium: -1, high: true, critical: 1.5}\n",
	"version: 1\nlevels: {critical: 10}\n",
	"version: 1\nlevels: {3: x, 1: y}\n",
	"version: 1\nlevels: null\nfactors: null\n",
	"version: 1\nfactors: {a: 1}\n",
	"version: 1\nfactors: [\n",
	factors("  - {id: a, points: 1, lines_over: 1}", "  - {id: a, points: 1, files_over: 1}"),
	factors("  - 3", "  - {points: 1, lines_over: 1}", "  - {id: 'x:y', points: 1, lines_over: 1}", "  - {id: -x, points: 1, lines_over: 1}",
		"  - {id: ' ok ', points: 1, lines_over: 1}"),
	factors("  - {id: a, points: -1, lines_over: 1}", "  - {id: b, points: true, lines_over: 1}", "  - {id: c, points: 1.0, lines_over: 1}",
		"  - {id: d, lines_over: 1}"),
	factors("  - {id: a, points: 1}", "  - {id: b, points: 1, lines_over: 1, glob: x}", "  - {id: c, points: 1, lines_over: x}",
		"  - {id: d, points: 1, files_over: -2}", "  - {id: e, points: 1, deleted_over: false}"),
	factors("  - {id: a, points: 1, lines_over: 1, include: x}", "  - {id: b, points: 1, files_over: 1, exclude: ['']}",
		"  - {id: c, points: 1, glob: x, include: [y]}", "  - {id: d, points: 1, judge: q, exclude: [y]}",
		"  - {id: e, points: 1, lines_over: 1, include: [3]}", "  - {id: f, points: 1, lines_over: 1, include: null, exclude: []}"),
	factors("  - {id: a, points: 1, lines_over: 0, include: [src]}", "  - {id: b, points: 1, lines_over: 0, exclude: [x]}",
		"  - {id: c, points: 1, lines_over: 0, include: ['src/', '\\\\docs\\\\**', ' /x/ ', '/']}"),
	factors("  - {id: a, points: 1, glob: ''}", "  - {id: b, points: 1, glob: 3}", "  - {id: c, points: 1, glob: ' \\\\.github\\\\ '}",
		"  - {id: d, points: 1, glob: '*.md', max: -1}", "  - {id: e, points: 1, glob: '*.md', max: 2.5}", "  - {id: f, points: 2, glob: '*.md', max: 0}"),
	factors("  - {id: a, points: 1, script: tools/x.sh}", "  - {id: b, points: 1, script: .ccnavi/config/common/scripts/x.sh}",
		"  - {id: c, points: 1, script: '.ccnavi/scripts/../x.sh'}", "  - {id: d, points: 1, script: '/.ccnavi/scripts/x.sh'}",
		"  - {id: e, points: 1, script: ' .ccnavi\\\\scripts\\\\ok.sh '}", "  - {id: f, points: 1, script: 3}"),
	factors("  - {id: a, points: 1, judge: ''}", "  - {id: b, points: 1, judge: [q]}", "  - {id: c, points: 0, judge: ok, message: ''}",
		"  - {id: d, points: 1, judge: ok, message: 0}", "  - {id: e, points: 12345678901234567890123, judge: big}"),
	"",
}

var riskMergeCases = [][2]string{
	{sampleRisk, sampleRisk},
	{sampleRisk, factors("  - {id: big-diff, points: 99, lines_over: 5, message: 行数が多い}", "  - {id: own, points: 5, files_over: 1}",
		"  - {id: src, points: 10, lines_over: 3, include: ['src/', 'src/'], exclude: ['*.lock', 'src/gen/**']}") +
		"levels: {medium: 10, critical: 90}\n"},
	{sampleRisk, "version: 1\nlevels: {high: 25}\n"},
	{"version: 1\nlevels: {medium: 30}\n", "version: 1\nlevels: {high: 25, critical: 26}\n"},
	{"version: 1\n", "version: 1\nlevels: {medium: 1}\n"},
}

var riskPoints = []int{0, 19, 20, 39, 40, 69, 70, 1000}

type diffSpec struct {
	Changes [][]any `json:"changes"`
	Base    string  `json:"base"`
	Head    string  `json:"head"`
}

type scoreCase struct {
	Definition string         `json:"definition"`
	Diff       diffSpec       `json:"diff"`
	Judgements map[string]any `json:"judgements"`
}

func change(path string, added, deleted int, status, old string) []any {
	return []any{path, added, deleted, status, old}
}

var bigDiff = diffSpec{Changes: [][]any{
	change("src/a.py", 2, 1, "M", ""), change("src/gen/b.py", 0, 9, "D", ""), change("uv.lock", 100, 0, "M", ""),
	change(".github/workflows/ci.yml", 1, 1, "M", ""), change(".GitHub/x.yml", 1, 0, "A", ""),
	change("vendor/core.py", 60, 60, "R", "src/core.py"), change("docs/x.md", 0, 0, "D", ""),
}, Base: "0123456789abcdef0123", Head: "fedcba9876543210fedc"}

var scoreCases = []scoreCase{
	{Definition: sampleRisk, Diff: bigDiff, Judgements: map[string]any{}},
	{Definition: sampleRisk, Diff: bigDiff, Judgements: map[string]any{
		"untested": map[string]any{"head": "fedcba9876543210fedc", "hit": true, "reason": "  テストが無い  "}}},
	{Definition: sampleRisk, Diff: bigDiff, Judgements: map[string]any{
		"untested": map[string]any{"head": "old", "hit": true}}},
	{Definition: sampleRisk, Diff: bigDiff, Judgements: map[string]any{
		"untested": map[string]any{"head": "fedcba9876543210fedc", "hit": false, "reason": "x"}}},
	{Definition: sampleRisk, Diff: bigDiff, Judgements: map[string]any{
		"untested": map[string]any{"head": "fedcba9876543210fedc", "hit": 1, "reason": nil}}},
	{Definition: sampleRisk, Diff: bigDiff, Judgements: map[string]any{"untested": "yes"}},
	{Definition: "", Diff: bigDiff, Judgements: map[string]any{}},
	{Definition: "", Diff: diffSpec{Changes: [][]any{change("a.txt", 1, 0, "M", "")}}, Judgements: map[string]any{}},
	{Definition: factors("  - {id: all, points: 1, lines_over: 0}", "  - {id: inc, points: 2, files_over: 0, include: ['src/**']}",
		"  - {id: exc, points: 4, lines_over: 3, exclude: ['*.lock', 'docs/**']}",
		"  - {id: both, points: 8, deleted_over: 0, include: ['src/**'], exclude: ['src/gen/**']}",
		"  - {id: moved-in, points: 16, lines_over: 0, include: ['SRC/**'], exclude: ['vendor/**']}",
		"  - {id: g, points: 7, glob: '*.py'}", "  - {id: g2, points: 7, glob: '**/*.py', max: 10}"),
		Diff: bigDiff, Judgements: map[string]any{}},
	{Definition: factors("  - {id: q, points: 3, judge: 1 つ目}", "  - {id: r, points: 4, judge: 2 つ目}") + "levels: {medium: 1, high: 3, critical: 7}\n",
		Diff: diffSpec{Head: ""}, Judgements: map[string]any{"q": map[string]any{"head": "", "hit": "y"}, "r": map[string]any{"head": 0, "hit": true}}},
}

func probs(ps []rules.Problem) [][]string {
	out := [][]string{}
	for _, p := range ps {
		out = append(out, []string{string(p.Severity), p.Rule, p.Detail})
	}
	return out
}

func show(d *Definition) *riskDefinition {
	if d == nil {
		return nil
	}
	out := &riskDefinition{Levels: d.Levels, Source: d.Source, Dropped: d.Dropped, Fallback: d.Fallback}
	for _, f := range d.Factors {
		var value, max any = f.Text, nil
		if f.Kind.counts() {
			value = f.Limit
		}
		if f.hasMax {
			max = f.max
		}
		out.Factors = append(out.Factors, []any{f.ID, f.Points, f.Message, string(f.Kind), value, max,
			orEmpty(f.Include), orEmpty(f.Exclude), f.Source})
	}
	for _, p := range riskPoints {
		out.LevelOf = append(out.LevelOf, string(d.levelOf(p)))
	}
	t := EffectiveLevels(d.Levels)
	out.Effective = map[string]int{"medium": t.Medium, "high": t.High, "critical": t.Critical}
	return out
}

func orEmpty(list []string) []string {
	if list == nil {
		return []string{}
	}
	return list
}

func same(t *testing.T, label string, got, want any) {
	t.Helper()
	g, _ := json.Marshal(got)
	w, _ := json.Marshal(want)
	var gv, wv any
	_ = json.Unmarshal(g, &gv)
	_ = json.Unmarshal(w, &wv)
	if !reflect.DeepEqual(emptyToNil(gv), emptyToNil(wv)) {
		t.Errorf("%s:\n got %s\nwant %s", label, g, w)
	}
}

// emptyToNil は空の列・空のオブジェクトを null にそろえる（nil と空の列の差は見ない）。
func emptyToNil(v any) any {
	switch x := v.(type) {
	case []any:
		if len(x) == 0 {
			return nil
		}
		for i := range x {
			x[i] = emptyToNil(x[i])
		}
	case map[string]any:
		if len(x) == 0 {
			return nil
		}
		for k := range x {
			x[k] = emptyToNil(x[k])
		}
	}
	return v
}

func toDiff(spec diffSpec) *Diff {
	d := &Diff{Base: spec.Base, Head: spec.Head}
	for _, c := range spec.Changes {
		d.changes = append(d.changes, Change{path: c[0].(string), added: c[1].(int), deleted: c[2].(int),
			status: c[3].(string), old: c[4].(string)})
	}
	return d
}

func judgementsOf(t *testing.T, raw map[string]any) *pyjson.Object {
	text, _ := json.Marshal(raw)
	v, err := pyjson.Loads(string(text))
	if err != nil {
		t.Fatal(err)
	}
	return v.(*pyjson.Object)
}

func TestRiskMatchesGolden(t *testing.T) {
	want := golden.Load[riskAnswers](t, "risk")
	for i, text := range riskDefCases {
		d, problems := Parse(text, where, layerHomes)
		same(t, "def "+strconv.Itoa(i), riskDefRow{probs(problems), show(d)}, want.Defs[i])
	}
	for i, pair := range riskMergeCases {
		common, _ := Parse(pair[0], where, scriptHomes)
		common.markLayer("common", "")
		extra, _ := Parse(pair[1], where, layerHomes)
		extra.markLayer("web", "")
		merged, problems := merge(common, extra, "web")
		same(t, "merge "+strconv.Itoa(i), riskDefRow{probs(problems), show(merged)}, want.Merges[i])
	}
	for i, c := range scoreCases {
		d := builtin()
		if c.Definition != "" {
			var problems []rules.Problem
			if d, problems = Parse(c.Definition, where, scriptHomes); d == nil {
				t.Fatal(problems)
			}
		}
		d.markLayer("common", "")
		diff := toDiff(c.Diff)
		s := Evaluate(d, diff, "", "", nil, judgementsOf(t, c.Judgements))
		record, err := pyjson.Dumps(s.Object(), pyjson.Options{})
		if err != nil {
			t.Fatal(err)
		}
		var decoded any
		_ = json.Unmarshal([]byte(record), &decoded)
		var pending []string
		for _, f := range s.Pending {
			pending = append(pending, f.ID)
		}
		got := riskScoreRow{Record: decoded, Lines: s.Lines(), Pending: pending, Summary: diff.Summary(),
			Prompt: JudgePrompt("i0001-01-01", "i0001", diff, s.Pending, "/w/tree", "/w")}
		same(t, "score "+strconv.Itoa(i), got, want.Scores[i])
	}
}
