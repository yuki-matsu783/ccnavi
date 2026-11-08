package ruleload

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/audit"
)

const rootMark = "{ROOT}"

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

const (
	commonRules = `version: 1
deny:
  - {id: credentials, match: "Write|Edit", glob: "*/.env*", message: "no credentials"}
  - {id: terraform, match: Bash, glob: "*terraform apply*", message: "no apply"}
allow:
  - {id: ws-src, match: "Write|Edit", glob: "*/src/*"}
  - {id: stop-check, match: Stop, glob: "*", every: 3, additionalContext: "check"}
  - {id: stop-every, match: Stop, glob: "*", additionalContext: "each"}
`
	ownRules = `version: 1
deny:
  - {id: generated, match: "Write|Edit", glob: "*/generated/*", message: "rebuilt"}
  - {id: terraform, match: Bash, glob: "*terraform apply*", message: "no apply"}
  - {id: credentials, match: "Write|Edit", glob: "*/.env", message: "other"}
  - {match: Bash, glob: "*x*", message: "no id"}
allow:
  - {id: stop-check, match: Stop, glob: "*", every: 5, additionalContext: "again"}
  - {id: stop-own, match: Stop, glob: "*", every: 2, additionalContext: "own"}
`
	libRules = `version: 1
deny:
  - {id: schema, match: "Write|Edit", glob: "*/schema/*", message: "schema"}
  - {id: dropdb, match: Bash, glob: "*dropdb*", message: "no dropdb"}
ask:
  - {id: migrate, match: Bash, glob: "*migrate*", message: "ask"}
allow:
  - {id: lib-src, match: "Write|Edit", glob: "{root}/projects/lib/src/*"}
`
	reservedRules = `version: 1
allow:
  - {id: anything, match: "Write|Edit", glob: "*"}
`
	// broken は読めないルールファイル。YAML の構文の誤りは文が libyaml の有無で変わるので、マッピングでない形で壊す。
	broken = "- version: 1\n"
)

// scenario は fixture の組み方。common は共通レイヤーの中身（空なら置かない）、layers は置くかどうか。
type scenario struct {
	Name   string `json:"name"`
	Common string `json:"common"`
	Layers bool   `json:"layers"`
	Root   string `json:"root"`
}

// workspace はワークスペース 1 つとプロジェクト（lib・app・壊れた bad・予約名の self）を組む。
func workspace(t *testing.T, s scenario) string {
	t.Helper()
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	if s.Common != "" {
		write(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), s.Common)
	}
	layer := func(home string) string { return filepath.Join(home, ".ccnavi", "config", "project", "rules.yml") }
	for _, name := range []string{"lib", "app", "bad", "self"} {
		if err := os.MkdirAll(filepath.Join(root, "projects", name, ".git"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if s.Layers {
		write(t, layer(root), ownRules)
		write(t, layer(filepath.Join(root, "projects", "lib")), libRules)
		write(t, layer(filepath.Join(root, "projects", "bad")), broken)
		write(t, layer(filepath.Join(root, "projects", "self")), reservedRules)
	}
	return root
}

var scenarios = []scenario{
	{Name: "full", Common: commonRules, Layers: true},
	{Name: "no-common", Layers: true},
	{Name: "bare"},
	{Name: "broken-common", Common: broken, Layers: true},
}

// calls は (ツール, 行き先かコマンド)。行き先は {ROOT} からの相対で書く。
var calls = [][2]string{
	{"Bash", "ls"},
	{"Write", "{ROOT}/x.py"},
	{"Write", "{ROOT}/projects/lib/schema/a.sql"},
	{"Write", "{ROOT}/projects/self/a"},
	{"Write", "{ROOT}/projects/bad/a"},
	{"Write", "/elsewhere/a"},
	{"Read", "{ROOT}/README.md"},
}

type callResult struct {
	Rules    map[string][][]any `json:"rules"`
	Source   string             `json:"source"`
	Target   []string           `json:"target"`
	Fallback string             `json:"fallback"`
	Detail   string             `json:"detail"`
	Said     string             `json:"said"`
}

type viewResult struct {
	Name       string             `json:"name"`
	Path       string             `json:"path"`
	Rules      map[string][][]any `json:"rules"`
	Unreadable string             `json:"unreadable"`
	Missing    bool               `json:"missing"`
	Problems   []string           `json:"problems"`
}

type result struct {
	Calls      []callResult `json:"calls"`
	Survey     []viewResult `json:"survey"`
	Sums       []viewResult `json:"sums"`
	SurveySaid string       `json:"survey_said"`
	Layers     [][]string   `json:"layers"`
	Files      [][]string   `json:"files"`
	Stop       []string     `json:"stop"`
	DenyIDs    []string     `json:"deny_ids"`
	Unreadable []string     `json:"unreadable"`
}

func idsOf(rs *rules.RuleSet) map[string][][]any {
	out := map[string][][]any{}
	for _, name := range rules.Sections() {
		list := [][]any{}
		for _, r := range *rs.Section(name) {
			list = append(list, []any{r.ID, r.Source, r.File, r.Base})
		}
		out[string(name)] = list
	}
	return out
}

func goResult(t *testing.T, root string) result {
	t.Helper()
	mark := func(s string) string { return strings.ReplaceAll(s, root, rootMark) }
	conf, _ := config.Load(root)
	res := result{Calls: []callResult{}, Stop: []string{}, DenyIDs: []string{}, Unreadable: []string{}}
	for _, c := range calls {
		written := strings.ReplaceAll(c[1], rootMark, root)
		record := &audit.Record{Subject: written}
		var said bytes.Buffer
		rs, source, target := RulesFor(&said, conf, root, hookio.Input{ToolName: c[0]}, record)
		cr := callResult{Rules: idsOf(rs), Source: mark(string(source)), Fallback: record.Fallback, Detail: mark(record.Detail), Said: mark(said.String())}
		if target != nil {
			cr.Target = []string{target.Name, target.Project}
		}
		res.Calls = append(res.Calls, cr)
	}
	view := func(v LayerView) viewResult {
		vr := viewResult{Name: v.Name, Path: mark(v.Path), Rules: idsOf(v.RuleSet), Unreadable: mark(v.Unreadable), Missing: v.Missing, Problems: []string{}}
		for _, p := range v.Problems {
			vr.Problems = append(vr.Problems, mark(p.String()))
		}
		return vr
	}
	var said bytes.Buffer
	for _, v := range Survey(&said, conf, root) {
		res.Survey = append(res.Survey, view(v))
	}
	for _, l := range Layers(conf, root) {
		res.Sums = append(res.Sums, view(SumView(&said, conf, root, l)))
		res.Layers = append(res.Layers, []string{l.Name, mark(l.Home), mark(l.Path)})
	}
	res.SurveySaid = mark(said.String())
	for _, f := range LayerFiles(conf, root) {
		res.Files = append(res.Files, []string{string(f.Origin), f.Layer, string(f.Kind), mark(f.Path)})
	}
	for _, r := range StopRules(conf, root) {
		res.Stop = append(res.Stop, r.ID)
	}
	for id := range DenyIDs(conf, root) {
		res.DenyIDs = append(res.DenyIDs, id)
	}
	slices.Sort(res.DenyIDs)
	res.Unreadable = append(res.Unreadable, Unreadable(conf, root)...)
	return res
}

// レイヤーの重ね方・出所・記録・診断の見え方・守る対象を、答えの見本（testdata）と突き合わせる。
func TestMatchesGolden(t *testing.T) {
	cases := slices.Clone(scenarios)
	for i := range cases {
		cases[i].Root = workspace(t, cases[i])
	}
	want := golden.Load[[]result](t, "ruleload")
	if len(want) != len(cases) {
		t.Fatalf("見本が %d 件（%d 件のはず）", len(want), len(cases))
	}
	for i, s := range cases {
		got := goResult(t, s.Root)
		check := func(name string, g, w any) {
			gj, _ := json.Marshal(g)
			wj, _ := json.Marshal(w)
			if !bytes.Equal(gj, wj) {
				t.Errorf("%s %s:\n go: %s\npy: %s", s.Name, name, gj, wj)
			}
		}
		for j, c := range calls {
			check(c[0]+" "+c[1], got.Calls[j], want[i].Calls[j])
		}
		check("survey", got.Survey, want[i].Survey)
		check("sums", got.Sums, want[i].Sums)
		check("survey said", got.SurveySaid, want[i].SurveySaid)
		check("layers", got.Layers, want[i].Layers)
		check("files", got.Files, want[i].Files)
		check("stop", got.Stop, want[i].Stop)
		check("deny ids", got.DenyIDs, want[i].DenyIDs)
		check("unreadable", got.Unreadable, want[i].Unreadable)
	}
}

func TestUnreadableNamesOnlyALayerThatExistsAndCannotBeRead(t *testing.T) {
	root := t.TempDir()
	conf, _ := config.Load(root)
	path := conf.LayerPath(root, config.KindRules, config.LayerSelf)
	if got := Unreadable(conf, root); len(got) != 0 {
		t.Fatalf("無いレイヤー: %v", got)
	}
	write(t, path, "version: 1\ndeny: [: broken\n")
	if got := Unreadable(conf, root); !slices.Equal(got, []string{config.LayerSelf}) {
		t.Fatalf("壊れたレイヤー: %v", got)
	}
	write(t, path, "version: 1\ndeny: []\n")
	if got := Unreadable(conf, root); len(got) != 0 {
		t.Fatalf("直したレイヤー: %v", got)
	}
}

func parse(t *testing.T, text string) *rules.RuleSet {
	t.Helper()
	data, err := yaml.Load(text)
	if err != nil {
		t.Fatal(err)
	}
	rs, problems := rules.Parse(data.(*yaml.Map), "/r")
	for _, p := range problems {
		if p.Severity == rules.Error {
			t.Fatal(p)
		}
	}
	return rs
}

func severities(problems []rules.Problem) []rules.Severity {
	out := []rules.Severity{}
	for _, p := range problems {
		out = append(out, p.Severity)
	}
	return out
}

// 重ねても except と when は落ちず、それだけが違う定義は別の定義（warn）、全欄が同じなら後ろを捨てる（info）。
func TestMergeKeepsExceptAndWhen(t *testing.T) {
	const plain = `{id: a, match: Write, glob: "*", message: m}`
	const withExcept = `{id: a, match: Write, glob: "*", message: m, except: [{glob: "*.md"}]}`
	const vault = `{id: a, match: Write, message: m, when: {all: [{glob: "*.crt"}, {glob: "*/vault/*"}]}}`
	const vaultAny = `{id: a, match: Write, message: m, when: {any: [{glob: "*.crt"}, {glob: "*/vault/*"}]}}`
	one := func(rule string) *rules.RuleSet { return parse(t, "version: 1\ndeny:\n  - "+rule+"\n") }

	base := one(plain)
	extra := one(`{id: b, match: Write, message: m, except: [{glob: "*/wip/*"}], when: {glob: "*.crt"}}`)
	prefixIDs(extra, "lib")
	merge(base, extra, "lib")
	if got := base.Deny[1]; got.ID != "lib:b" || len(got.Excepts) != 1 || got.When == nil || got.Matches("Write", "/r/wip/a.crt") || !got.Matches("Write", "/r/a.crt") {
		t.Errorf("重ねたルール: %+v", got)
	}

	for _, c := range []struct {
		name, base, extra string
		want              []rules.Severity
		total             int
	}{
		{"except だけが違う", withExcept, plain, []rules.Severity{rules.Warn}, 2},
		{"全欄が同じ", withExcept, withExcept, []rules.Severity{rules.Info}, 1},
		{"when の組み方だけが違う", vault, vaultAny, []rules.Severity{rules.Warn}, 2},
		{"when が同じ", vault, vault, []rules.Severity{rules.Info}, 1},
		{"glob と同じ個別条件だけの when", `{id: a, match: Write, glob: "*.crt", message: m}`, `{id: a, match: Write, when: {glob: "*.crt"}, message: m}`, []rules.Severity{rules.Warn}, 2},
	} {
		base := one(c.base)
		problems := merge(base, one(c.extra), "lib")
		if !slices.Equal(severities(problems), c.want) || len(base.Deny) != c.total {
			t.Errorf("%s: %v, %d 本", c.name, problems, len(base.Deny))
		}
	}
}

// レイヤーの前置きは id に付き、記録名（ルールファイルの相対パスと書いた id）には付かない。
func TestPrefixKeepsTheRecordName(t *testing.T) {
	rs := parse(t, "version: 1\ndeny:\n  - {id: raw-git, match: Bash, glob: \"*git*\", message: m}\n")
	markFile(rs, "projects/lib/.ccnavi/config/project/rules.yml")
	prefixIDs(rs, "lib")
	r := rs.Deny[0]
	if r.ID != "lib:raw-git" || rules.RecordName(r) != "projects/lib/.ccnavi/config/project/rules.yml:raw-git" {
		t.Errorf("%s / %s", r.ID, rules.RecordName(r))
	}
}

func TestRelPathIsSlashedFromTheRoot(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "projects", "lib", ".ccnavi", "config", "project", "rules.yml")
	if got := RelPath(path, root); got != "projects/lib/.ccnavi/config/project/rules.yml" {
		t.Error(got)
	}
}
