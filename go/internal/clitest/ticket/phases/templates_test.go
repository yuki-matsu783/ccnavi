package phases

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// 作業・レビュー計画のテンプレート（読む側）の受入テスト（tests/ticket/test_plan_templates.py）。
//
// 読み込み・2 層の統合結果・検査・`--templates` の一覧・検索・`--check-file` の答えの中身は、
// go/internal/tickets/proposal/templates の見本（TestMatchesGolden）が 1 字単位で見る。リンク・ハードリンクを読まない保護は
// links_test.go（TestLinksAreNotRead）が見る。ここで見るのは見本が持たない次の 3 つ。
//  1. `ccnavi --templates` のテキストの出方、`--json` の答えの形（tests/fixtures/templates-json.json と
//     templates-check-json.json。拡張と共有する）、使い方の誤りの拒否
//  2. `ccnavi --lint` への出方（レイヤーの名前と重さ）
//  3. 型の定義が変わったときの扱い

const templatesJSONVersion = 1

const flowText = `    id: wf-impl
    name: 実装の手順
    version: 1.0.0
    nodes:
      - {id: s, type: start, name: 開始, position: {x: 0, y: 0}, data: {}}
      - {id: p, type: prompt, name: 実装, position: {x: 1, y: 0}, data: {prompt: 先にテスト}}
      - {id: e, type: end, name: 終了, position: {x: 2, y: 0}, data: {}}
    connections:
      - {id: c1, from: s, to: p, fromPort: output, toPort: input}
      - {id: c2, from: p, to: e, fromPort: output, toPort: input}
`

const featureTemplate = `name: feature
description: 機能を設計から実装まで進める
when: 新しい機能を足すとき
plan:
  - design
  - {type: implement, after: [1]}
feedback:
  - implement-feedback
flows:
  2:
` + flowText

const researchTemplate = `name: research
description: 調べてから設計する
plan:
  - research
  - {type: design, after: [1]}
`

// templateText は `plan` を `plan:` の下の行（2 字下げ済み）で渡して書くテンプレート。
func templateText(name, plan, description, extra string) string {
	if description == "" {
		description = "試し"
	}
	return "name: " + name + "\ndescription: " + description + "\n" + extra + "plan:\n" + plan
}

// tpl はテンプレートを置いて --templates を打つ作業場（親子のリポジトリは持たない）。
type tpl struct {
	t    *testing.T
	root string
}

func newTpl(t *testing.T) *tpl {
	r := &tpl{t: t, root: base.TempDir(t)}
	write(t, base.CommonPath(r.root, "phases"), samples.Phases)
	return r
}

func (r *tpl) path(layer, name string) string {
	var dir string
	switch layer {
	case "common":
		dir = filepath.Dir(base.CommonPath(r.root, "rules"))
	case "self":
		dir = filepath.Dir(base.ConfigPath(r.root, "rules"))
	default:
		dir = filepath.Join(r.root, "projects", layer, ".ccnavi", "config", "project")
		if err := os.MkdirAll(filepath.Join(r.root, "projects", layer, ".git"), 0o755); err != nil {
			r.t.Fatal(err)
		}
	}
	return filepath.Join(dir, "templates", name+".yml")
}

func (r *tpl) common(name, text string) string { return write(r.t, r.path("common", name), text) }
func (r *tpl) own(name, text string) string    { return write(r.t, r.path("self", name), text) }

func (r *tpl) ccnavi(args ...string) run.Result {
	r.t.Helper()
	return run.Ccnavi(r.t, append([]string{"--root", r.root}, args...), run.Opts{Dir: base.Root()})
}

func (r *tpl) answer(args ...string) map[string]any {
	r.t.Helper()
	res := r.ccnavi(append([]string{"--templates", "--json"}, args...)...)
	if res.Code != 0 {
		r.t.Fatalf("code %d\n%s", res.Code, res.Stderr)
	}
	return jsonOf(r.t, res.Stdout)
}

func (r *tpl) entry(layer, name string) map[string]any {
	r.t.Helper()
	for _, row := range r.answer()["templates"].([]any) {
		m := row.(map[string]any)
		if m["name"] == name && m["layer"] == layer {
			return m
		}
	}
	r.t.Fatalf("%s/%s が答えに無い", layer, name)
	return nil
}

func problemsOf(row map[string]any, severity string) []string {
	var out []string
	ps, _ := row["problems"].([]any)
	for _, p := range ps {
		m := p.(map[string]any)
		if m["severity"] == severity {
			out = append(out, m["detail"].(string))
		}
	}
	return out
}

func TestNoTemplatesIsNormal(t *testing.T) {
	t.Parallel()
	answer := newTpl(t).answer()
	if got := answer["templates"].([]any); len(got) != 0 {
		t.Errorf("templates = %v", got)
	}
	if answer["layers"].([]any)[0].(map[string]any)["exists"] != false {
		t.Error("置き場が無いのに exists が false でない")
	}
}

func TestAReviewFalseIsAllowedAndAChangedDefinitionOnlyWarns(t *testing.T) {
	t.Parallel()
	r := newTpl(t)
	r.own("t", templateText("t", "  - {type: design, review: false}\n", "", ""))
	if got := problemsOf(r.entry("self", "t"), "error"); len(got) != 0 {
		t.Errorf("review:false が error: %v", got)
	}
	// 型が phases.yml から消えても、読むのも一覧も止まらない（使うときの承認で error になる）。
	r.own("feature", featureTemplate)
	write(t, base.CommonPath(r.root, "phases"), strings.Replace(samples.Phases, "  design:", "  design2:", 1))
	row := r.entry("self", "feature")
	if got := problemsOf(row, "error"); len(got) != 0 {
		t.Errorf("型が消えて error: %v", got)
	}
	if len(problemsOf(row, "warn")) == 0 {
		t.Error("型が消えても warn が出ない")
	}
}

func TestTheTextOutputs(t *testing.T) {
	t.Parallel()
	r := newTpl(t)
	r.common("research", researchTemplate)
	r.own("feature", featureTemplate)
	out := r.ccnavi("--templates")
	if out.Code != 0 {
		t.Fatalf("code %d\n%s", out.Code, out.Stderr)
	}
	contains(t, "一覧", out.Stdout, "research  [common]  調べてから設計する", "feature  [self]  機能を設計から実装まで進める", "いつ: 新しい機能を足すとき")
	// 複数の --keyword は和。ヒットしないときの文。
	var names []string
	for _, row := range r.answer("--keyword", "調べ", "--keyword", "feat")["templates"].([]any) {
		names = append(names, row.(map[string]any)["name"].(string))
	}
	sort.Strings(names)
	equalStrings(t, "keyword の和", names, []string{"feature", "research"})
	contains(t, "ヒットなし", r.ccnavi("--templates", "--keyword", "無い語").Stdout, "該当するテンプレートはありません。")
	// --show は中身を出す。
	shown := r.ccnavi("--templates", "--show", "feature")
	if shown.Code != 0 {
		t.Fatalf("code %d\n%s", shown.Code, shown.Stderr)
	}
	contains(t, "--show", shown.Stdout, "テンプレート: feature（self）", "2. implement（after 1）", "name: feature")
	tmpl := jsonOf(t, r.ccnavi("--templates", "--show", "feature", "--json").Stdout)["template"].(map[string]any)
	sum := sha256.Sum256([]byte(featureTemplate))
	if tmpl["name"] != "feature" || tmpl["text"] != featureTemplate || tmpl["sha"] != hex.EncodeToString(sum[:]) {
		t.Errorf("--show --json の template = %v", tmpl)
	}
	// 使わない理由を言う。統合結果の相手でないレイヤーの置き場の苦情は混ぜない。
	r.common("broken", "name: [\n")
	write(t, filepath.Join(filepath.Dir(r.path("self", "x")), "notes.txt"), "x")
	write(t, r.path("lib", "lib"), templateText("lib", "  - design\n", "", ""))
	text := r.ccnavi("--templates").Stdout
	contains(t, "一覧", text, "broken  [common]（使わない: invalid）  YAML として読めない", "notes.txt")
	other := r.ccnavi("--templates", "--layer", "lib").Stdout
	lacks(t, "lib の一覧", other, "notes.txt")
	contains(t, "lib の一覧", other, "lib  [lib]")
	// 書かずに検査する --check-file のテキスト。
	path := write(t, filepath.Join(r.root, "tmp-check", "feature.yml"), featureTemplate)
	checked := r.ccnavi("--templates", "--check-file", path)
	if checked.Code != 0 {
		t.Fatalf("code %d\n%s", checked.Code, checked.Stderr)
	}
	contains(t, "--check-file", checked.Stdout, "読めた: はい")
}

func TestFailuresAndWrongCombinationsAreRefused(t *testing.T) {
	t.Parallel()
	r := newTpl(t)
	r.common("research", researchTemplate)
	r.own("feature", featureTemplate)
	out := r.ccnavi("--templates", "--show", "nothing")
	if out.Code != 1 || !strings.Contains(out.Stderr, "nothing") {
		t.Errorf("code %d\n%s", out.Code, out.Stderr)
	}
	for _, args := range [][]string{
		{"--templates", "--show", "nothing", "--json"},
		{"--templates", "--layer", "nowhere", "--json"},
		{"--templates", "--show", "feature", "--layer", "nowhere", "--json"},
	} {
		out := r.ccnavi(args...)
		answer := jsonOf(t, out.Stdout)
		if out.Code != 1 || answer["ok"] != false || answer["error"] == "" || answer["version"] != float64(templatesJSONVersion) || out.Stderr != "" {
			t.Errorf("%v: code %d, answer %v, stderr %q", args, out.Code, answer, out.Stderr)
		}
	}
	for _, c := range []struct {
		args   []string
		needle string
	}{
		{[]string{"--show", "feature"}, "--templates"},
		{[]string{"--layer", "self"}, "--templates"},
		{[]string{"--templates", "--docs"}, "--docs"},
		{[]string{"--templates", "--type", "adr"}, "--docs でだけ"},
		{[]string{"--templates", "--lint"}, "--lint"},
		{[]string{"--templates", "--show", "a", "--keyword", "b"}, "--keyword"},
		{[]string{"--templates", "ticket"}, "フラグ以外の語"},
		{[]string{"--templates", "--show", " "}, "名前"},
		{[]string{"--lint", "--keyword", "x"}, "--docs でだけ"},
	} {
		out := r.ccnavi(c.args...)
		if out.Code != 1 || !strings.Contains(out.Stderr, c.needle) || out.Stdout != "" {
			t.Errorf("%v: code %d, stderr %q, stdout %q", c.args, out.Code, out.Stderr, out.Stdout)
		}
	}
	path := write(t, filepath.Join(r.root, "tmp-check", "feature.yml"), featureTemplate)
	for _, extra := range [][]string{{"--show", "x"}, {"--draft", "p"}, {"--keyword", "k"}} {
		out := r.ccnavi(append([]string{"--templates", "--check-file", path}, extra...)...)
		if out.Code == 0 || !strings.Contains(out.Stderr, "--check-file") {
			t.Errorf("%v: code %d\n%s", extra, out.Code, out.Stderr)
		}
	}
	alone := r.ccnavi("--check-file", path)
	if alone.Code == 0 || !strings.Contains(alone.Stderr, "--templates と一緒に使う") {
		t.Errorf("code %d\n%s", alone.Code, alone.Stderr)
	}
	res := r.ccnavi("--templates", "--check-file", path, "--layer", "nothing", "--json")
	if res.Code != 1 || jsonOf(t, res.Stdout)["ok"] != false {
		t.Errorf("code %d\n%s", res.Code, res.Stdout)
	}
}

func TestProblemsAreReportedWithTheirLayer(t *testing.T) {
	t.Parallel()
	r := newTpl(t)
	lint := func() []map[string]any {
		var out []map[string]any
		res := r.ccnavi("--lint", "--json", "--mode", "enable")
		for _, p := range jsonOf(t, res.Stdout)["problems"].([]any) {
			m := p.(map[string]any)
			if strings.Contains(m["where"].(string), "templates") {
				out = append(out, m)
			}
		}
		return out
	}
	if got := lint(); len(got) != 0 {
		t.Errorf("何も置かないのに苦情: %v", got)
	}
	r.common("research", researchTemplate)
	r.own("feature", featureTemplate)
	if got := lint(); len(got) != 0 {
		t.Errorf("正しいテンプレートに苦情: %v", got)
	}
	r.own("research", strings.Replace(researchTemplate, "調べてから", "調べずに", 1))
	r.own("ends", templateText("ends", "  - design\n  - acceptance\n", "", ""))
	r.own("unknown", templateText("unknown", "  - docs\n", "", ""))
	write(t, r.path("lib", "broken"), "name: [\n")
	found := map[string]bool{}
	var details []string
	for _, p := range lint() {
		found[p["where"].(string)+"|"+p["severity"].(string)] = true
		details = append(details, p["detail"].(string))
	}
	want := map[string]bool{"(templates/self)|error": true, "(templates/self)|warn": true, "(templates/lib)|error": true}
	if !reflect.DeepEqual(found, want) {
		t.Errorf("found = %v, want %v", found, want)
	}
	contains(t, "details", strings.Join(details, " "), "research: ", "ends: ")
	res := r.ccnavi("--lint", "--mode", "enable")
	if res.Code != 1 || !strings.Contains(res.Stdout, "templates/self") {
		t.Errorf("code %d\n%s", res.Code, res.Stdout)
	}
}

func (r *tpl) check(text, name string, args ...string) map[string]any {
	r.t.Helper()
	path := write(r.t, filepath.Join(r.root, "tmp-check", name+".yml"), text)
	res := r.ccnavi(append([]string{"--templates", "--check-file", path, "--json"}, args...)...)
	if res.Code != 0 {
		r.t.Fatalf("code %d\n%s", res.Code, res.Stderr)
	}
	return jsonOf(r.t, res.Stdout)
}

func TestAPlanOnlyTemplateHasOneGraphAndUnreadableYAMLIsInvalid(t *testing.T) {
	t.Parallel()
	r := newTpl(t)
	got := r.check(researchTemplate, "research")
	plans := got["plans"].([]any)
	if len(plans) != 1 || plans[0].(map[string]any)["part"] != "plan" {
		t.Fatalf("plans = %v", plans)
	}
	if plans[0].(map[string]any)["items"].([]any)[0].(map[string]any)["review"] != false {
		t.Error("research の review が false でない")
	}
	got = r.check("name: [\n", "feature")
	if got["valid"] != false || got["data"] != nil || len(got["plans"].([]any)) != 0 || len(problemsOf(got, "error")) == 0 {
		t.Errorf("読めない YAML の答え = %v", got)
	}
}

func TestTheCheckFileAnswerMatchesTheFixture(t *testing.T) {
	t.Parallel()
	r := newTpl(t)
	got := r.check(featureTemplate, "feature")
	got["sha"] = "<sha>"
	for _, p := range got["plans"].([]any) {
		p.(map[string]any)["source_sha"] = "<sha>"
	}
	fixture := jsonOf(t, read(t, filepath.Join(base.Root(), "tests", "fixtures", "templates-check-json.json")))
	sameJSON(t, "答え", got, fixture["expect"])
	if s, _ := fixture["about"].(string); s == "" {
		t.Error("見本に about が無い")
	}
}

// showcase は答えの形の見本にするワークスペース。統合結果・衝突・壊れたファイル・検査の苦情が一通り出る。
func showcase(t testing.TB, root string) {
	write(t, base.CommonPath(root, "phases"), samples.Phases)
	common := filepath.Dir(base.CommonPath(root, "rules")) + "/templates/"
	own := filepath.Dir(base.ConfigPath(root, "rules")) + "/templates/"
	write(t, common+"feature.yml", featureTemplate)
	write(t, common+"research.yml", researchTemplate)
	write(t, common+"broken.yml", "name: [\n")
	write(t, own+"feature.yml", featureTemplate)
	write(t, own+"research.yml", strings.Replace(researchTemplate, "調べてから", "調べずに", 1))
	write(t, own+"local.yml", templateText("local", "  - docs\n", "このプロジェクトだけの作業", ""))
	write(t, own+"ends.yml", templateText("ends", "  - design\n  - acceptance\n", "終端が 2 つ", ""))
	write(t, own+"notes.txt", "x")
	if err := os.MkdirAll(filepath.Join(root, "projects", "lib", ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(root, "projects", "lib", ".ccnavi/config/project/templates/lib.yml"), templateText("lib", "  - research\n", "lib の調べもの", ""))
	// 下書き（効力は無い）。TODO の残り・同名の既存・読めないもの。
	drafts := filepath.Join(root, "wip", "handoff", ".ccnavi", "config", "project", "templates") + "/"
	login := strings.Replace(strings.Replace(featureTemplate, "feature", "login", -1), "機能を設計から", "TODO ログインを設計から", 1)
	write(t, drafts+"login.yml", login)
	write(t, drafts+"research.yml", researchTemplate)
	write(t, drafts+"broken.yml", "name: [\n")
	// 同じ中身の下書きがもう 1 つのツリー（lib）にもある（also_in に値が入る）。
	write(t, filepath.Join(root, "projects", "lib", "wip", "handoff", ".ccnavi/config/project/templates/login.yml"), login)
}

var yamlReason = regexp.MustCompile(`(YAML として読めない) \(.*\)`)

// plainShape は YAML ライブラリの版で変わる、読めない理由の文言を外す。
func plainShape(v any) any {
	switch x := v.(type) {
	case string:
		return yamlReason.ReplaceAllString(x, "$1 (...)")
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = plainShape(e)
		}
		return out
	case map[string]any:
		out := map[string]any{}
		for k, e := range x {
			out[k] = plainShape(e)
		}
		return out
	}
	return v
}

func TestTheTemplatesJSONAnswerMatchesTheFixture(t *testing.T) {
	t.Parallel()
	// `--templates --json` の答えの形。拡張と共有する見本（templates-json.json）。
	root := base.TempDir(t)
	showcase(t, root)
	res := run.Ccnavi(t, []string{"--root", root, "--templates", "--json"}, run.Opts{Dir: base.Root()})
	if res.Code != 0 {
		t.Fatalf("code %d\n%s", res.Code, res.Stderr)
	}
	quoted, _ := json.Marshal(root)
	answer := jsonOf(t, strings.ReplaceAll(res.Stdout, string(quoted[1:len(quoted)-1]), "<root>"))
	fixture := jsonOf(t, read(t, filepath.Join(base.Root(), "tests", "fixtures", "templates-json.json")))
	sameJSON(t, "答え", plainShape(answer), plainShape(fixture["expect"]))
	if s, _ := fixture["about"].(string); s == "" {
		t.Error("見本に about が無い")
	}
}
