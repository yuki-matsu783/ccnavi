package flow

// `ccnavi --lint --flow <パス>` の受入テスト。
//
// VS Code 拡張のフロー編集画面は、開くときと保存の前に編集中の本文を一時ファイルに書いてこれに掛け、
// 正しいかの答えを実行ファイルから受ける。見るのは次のとおり。
//
//  1. SubagentStart と同じ読み手・同じ検査で読み、読めなければ (flow) の error。読めるフローには (flow) の苦情が出ない
//  2. --json の形は lint の JSON のまま（where が (flow)）。形の誤りを SubagentStart と同じ理由で言う
//  3. 診断の外では無視して言う
//  4. --json には読めた中身（flow.data）を載せる。JSON にそのまま載らない値は目印にする（拡張は自分の読みと見比べる）
//
// 構造の検査（線・到達・ループ）と、候補（エージェント・スキル）の読み方は go の flow のテストが見る。
import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// 読まない理由（SubagentStart と同じ文）と、読むファイルの大きさの上限、JSON に載らない値の目印。
const (
	linkedNote     = "ファイルか、ツリーのルートからそこまでの途中がシンボリックリンクなので読まない"
	notRegularNote = "ふつうのファイルではない（名前付きパイプ・デバイスなど）ので読まない"
	hardLinkedNote = "ハードリンク（ほかのパスからも同じ中身を開ける）なので読まない。"
	aliasedNote    = "YAML の別名（`*名前`）があるので読まない。"
	notUTF8Note    = "UTF-8 として読めない"
	fileLimit      = 256 * 1024
	jsonMark       = "$ccnavi"
)

// lintKit は使い捨ての根（root）と、その外の置き場（outside）。
type lintKit struct {
	t             *testing.T
	root, outside string
}

func newLintKit(t *testing.T) lintKit {
	t.Helper()
	return lintKit{t: t, root: base.TempDir(t), outside: base.TempDir(t)}
}

func (k lintKit) run(opts run.Opts, args ...string) run.Result {
	k.t.Helper()
	return run.Ccnavi(k.t, append([]string{"--root", k.root}, args...), opts)
}

func (k lintKit) lint(path string, extra ...string) run.Result {
	k.t.Helper()
	return k.run(run.Opts{}, append([]string{"--lint", "--json", "--flow", path}, extra...)...)
}

func (k lintKit) flowProblems(path string) []any {
	k.t.Helper()
	return flowOnly(jl(ticketkit.ParseJSON(k.t, k.lint(path).Stdout), "problems"))
}

func flowOnly(problems []any) []any {
	var out []any
	for _, p := range problems {
		if js(p, "where") == "(flow)" {
			out = append(out, p)
		}
	}
	return out
}

func (k lintKit) flowErrors(path string) []any {
	var out []any
	for _, p := range k.flowProblems(path) {
		if js(p, "severity") == "error" {
			out = append(out, p)
		}
	}
	return out
}

func (k lintKit) file(text, name string) string {
	if name == "" {
		name = "flow.yml"
	}
	return ticketkit.Write(k.t, filepath.Join(k.outside, name), text)
}

func (k lintKit) assertRefused(path, reason string) {
	k.t.Helper()
	problems := k.flowProblems(path)
	if len(problems) != 1 {
		k.t.Fatalf("%s: (flow) の苦情が %d 件: %v", reason, len(problems), problems)
	}
	if js(problems[0], "severity") != "error" {
		k.t.Errorf("error でない: %v", problems[0])
	}
	ticketkit.Has(k.t, js(problems[0], "detail"), reason)
	// 苦情には渡したパスを出す（画面が対象のファイルのパスに直す）。
	if !strings.HasPrefix(js(problems[0], "detail"), path) {
		k.t.Errorf("detail がパスで始まらない: %s", js(problems[0], "detail"))
	}
}

func TestAReadableFlowHasNoFlowProblem(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	path := k.file(workflowYAML, "")
	if got := k.flowProblems(path); len(got) != 0 {
		t.Errorf("(flow) の苦情 = %v", got)
	}
	// ユーザ向けの本文も、確かめたフローの名前を出す。
	text := k.run(run.Opts{}, "--lint", "--flow", path)
	ticketkit.Has(t, text.Stdout, "フロー: "+path)
	ticketkit.Lacks(t, text.Stdout, "(flow)")
}

func TestTheFlowLintJSONKeepsTheLintShape(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	r := k.lint(k.file("nodes: [\n", ""))
	wantCode(t, r, 1)
	payload := ticketkit.ParseJSON(t, r.Stdout)
	if jf(payload, "version") != 1 {
		t.Errorf("version = %v", payload["version"])
	}
	for _, key := range []string{"root", "rules", "mode", "ticket_control", "projects", "errors", "warns"} {
		if _, ok := payload[key]; !ok {
			t.Errorf("%s が無い", key)
		}
	}
	flows := flowOnly(jl(payload, "problems"))
	if len(flows) != 1 || len(jm(flows[0])) != 3 || jv(flows[0], "severity") == nil || jv(flows[0], "where") == nil || jv(flows[0], "detail") == nil {
		t.Errorf("flows = %v", flows)
	}
	if jf(payload, "errors") < 1 {
		t.Errorf("errors = %v", payload["errors"])
	}
}

func TestBrokenYAMLIsAnError(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	k.assertRefused(k.file("nodes:\n  - id: s\n    type: [start\n", ""), "YAML として読めない")
	k.assertRefused(k.file("nodes: []\n---\nnodes: []\n", ""), "YAML として読めない")
}

func TestAliasesAreAnError(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	k.assertRefused(k.file("x: &a [1]\nnodes: [{id: a, data: *a}]\n", ""), aliasedNote)
}

// 形の誤りを SubagentStart と同じ理由で言う。
func TestShapeErrorsAreTheSameReasonsAsSubagentStart(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	for _, c := range []struct{ text, reason string }{
		{"", "最上位がマッピングではない"},
		{"- 1\n", "最上位がマッピングではない"},
		{"name: x\n", "`nodes` のリストがありません。"},
		{"nodes: [1]\n", "nodes[0] がマッピングではない"},
		{"nodes:\n  - {type: start}\n", "nodes[0] に文字列の id がありません。"},
		{"nodes:\n  - {id: 1, type: start}\n", "nodes[0] に文字列の id がありません。"},
		{"nodes:\n  - {id: a}\n  - {id: a}\n", "ノードの id が重なっている（a）"},
		{"nodes: []\nconnections: {}\n", "`connections` がリストではない"},
		{"nodes: []\nconnections: [1]\n", "connections[0] がマッピングではない"},
	} {
		k.assertRefused(k.file(c.text, ""), c.reason)
	}
	// connections が無いフローは読める（並べるときは線なし）。start・end が無いのは warn。
	if got := k.flowErrors(k.file("nodes:\n  - {id: a, type: prompt}\n", "")); len(got) != 0 {
		t.Errorf("error = %v", got)
	}
}

func TestLinksAndNonRegularFilesAreErrors(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	k := newLintKit(t)
	real := k.file(workflowYAML, "real.yml")
	link := filepath.Join(k.outside, "link.yml")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	k.assertRefused(link, linkedNote)
	// ツリーの中なら、ルートからの途中のリンクも見る（SubagentStart と同じ）。
	ticketkit.Write(t, filepath.Join(k.root, "elsewhere", "b.yml"), workflowYAML)
	if err := os.MkdirAll(filepath.Join(k.root, ".ccnavi", "approved"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(k.root, "elsewhere"), filepath.Join(k.root, ".ccnavi", "approved", "flows")); err != nil {
		t.Fatal(err)
	}
	k.assertRefused(filepath.Join(k.root, ".ccnavi", "approved", "flows", "b.yml"), linkedNote)
	// ディレクトリ（ふつうのファイルでないもの）も読まない。
	k.assertRefused(k.outside, notRegularNote)
	// ハードリンク。
	twin := filepath.Join(k.outside, "twin.yml")
	if err := os.Link(real, twin); err != nil {
		t.Skip("ハードリンクを作れない:", err)
	}
	k.assertRefused(twin, hardLinkedNote)
}

func TestLargeAndMissingFilesAreErrors(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	k.assertRefused(k.file("nodes: []\npad: "+strings.Repeat("x", fileLimit+10)+"\n", ""), "大きすぎる")
	k.assertRefused(filepath.Join(k.outside, "nothing.yml"), "無い")
}

func TestRelativePathsAreReadFromTheWorkingDirectory(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	k.file("nodes: [\n", "")
	r := k.run(run.Opts{Dir: k.outside}, "--lint", "--json", "--flow", "flow.yml")
	flows := flowOnly(jl(ticketkit.ParseJSON(t, r.Stdout), "problems"))
	if len(flows) != 1 {
		t.Fatalf("flows = %v", flows)
	}
	head, _, _ := strings.Cut(js(flows[0], "detail"), ": ")
	if !samePath(t, head, filepath.Join(k.outside, "flow.yml")) && !strings.EqualFold(filepath.Dir(head), k.outside) {
		t.Errorf("detail の先頭 = %q（%s の下のはず）", head, k.outside)
	}
}

func (k lintKit) flowData(text string) any {
	k.t.Helper()
	payload := ticketkit.ParseJSON(k.t, k.lint(k.file(text, "")).Stdout)
	if _, ok := payload["flow"]; !ok {
		k.t.Fatalf("flow の欄が無い: %v", payload)
	}
	return jv(payload, "flow", "data")
}

func TestTheJSONCarriesWhatWasRead(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	path := k.file(workflowYAML, "")
	payload := ticketkit.ParseJSON(t, k.lint(path).Stdout)
	if js(payload, "flow", "path") != path || js(payload, "flow", "data", "nodes", 0, "id") != "s" {
		t.Errorf("flow = %v", payload["flow"])
	}
	// 読めなければ中身は null（苦情は error）。
	broken := ticketkit.ParseJSON(t, k.lint(k.file("nodes: [\n", "")).Stdout)
	if v, ok := jm(broken, "flow")["data"]; !ok || v != nil {
		t.Errorf("読めない flow.data = %v", v)
	}
	// --flow を渡さなければ欄ごと無い。
	plain := ticketkit.ParseJSON(t, k.run(run.Opts{}, "--lint", "--json").Stdout)
	if _, ok := plain["flow"]; ok {
		t.Error("--flow なしで flow の欄がある")
	}
}

// 値は PyYAML と同じに読み、JSON が持てない値には目印を付ける。
func TestValuesAreReadByPyYAMLAndMarkedWhenJSONCannotHoldThem(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	mark := func(kind string, more map[string]any) map[string]any {
		m := map[string]any{jsonMark: kind}
		for key, v := range more {
			m[key] = v
		}
		return m
	}
	head := "nodes:\n  - id: a\n    data:\n"
	for _, c := range []struct {
		value string
		want  any
	}{
		{"0755", 493.0}, {"yes", true}, {"on", true}, {"1:30", 90.0}, {"0o17", "0o17"}, {"1e3", "1e3"}, {"1_000", 1000.0},
		{"1.", mark("float", map[string]any{"value": 1.0})},
		{"1.5", mark("float", map[string]any{"value": 1.5})},
		{"!!float 1", mark("float", map[string]any{"value": 1.0})},
		{".inf", mark("float", map[string]any{"text": "inf"})},
		{".nan", mark("float", map[string]any{"text": "nan"})},
		{"2026-01-01", mark("date", map[string]any{"text": "2026-01-01"})},
		{"!!binary aGk=", mark("bytes", map[string]any{"text": "aGk="})},
		{"123456789012345678901", mark("int", map[string]any{"text": "123456789012345678901"})},
		{"{1: a}", mark("map", map[string]any{"items": []any{[]any{1.0, "a"}}})},
		{`{"$ccnavi": a}`, mark("map", map[string]any{"items": []any{[]any{"$ccnavi", "a"}}})},
		{"~", nil}, {"'yes'", "yes"},
	} {
		data := k.flowData(head + "      v: " + c.value + "\n")
		if got := jv(data, "nodes", 0, "data", "v"); !reflect.DeepEqual(got, c.want) {
			t.Errorf("v: %s → %#v, want %#v", c.value, got, c.want)
		}
	}
}

// 別名を使わないマージキーは PyYAML が展開する。拡張の読み手は展開しないので、画面は食い違いとして断る。
func TestMergeKeysAreReadAsPyYAMLReadsThem(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	data := k.flowData("nodes: [{<<: {id: a}}]\n")
	if nodes := jl(data, "nodes"); len(nodes) != 1 || !reflect.DeepEqual(nodes[0], map[string]any{"id": "a"}) {
		t.Errorf("nodes = %v", nodes)
	}
}

func TestBrokenUTF8IsAnErrorAndABOMIsDropped(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	bad := filepath.Join(k.outside, "bad.yml")
	if err := os.WriteFile(bad, []byte("nodes:\n  - id: a\n    name: \xff\xfe\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	k.assertRefused(bad, notUTF8Note)
	bom := filepath.Join(k.outside, "bom.yml")
	if err := os.WriteFile(bom, []byte("\xef\xbb\xbfnodes:\n  - id: a\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := k.flowErrors(bom); len(got) != 0 {
		t.Errorf("BOM で error = %v", got)
	}
}

func TestOnlyLintReadsTheFlowFlag(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	path := k.file("nodes: [\n", "")
	payload := mustJSONText(map[string]any{"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": map[string]any{"file_path": filepath.Join(k.root, "README.md")}, "cwd": k.root})
	hook := k.run(run.Opts{Input: payload}, "--mode", "enable", "--flow", path)
	ticketkit.Has(t, hook.Stderr, "--flow は診断")
	tried := k.run(run.Opts{}, "--test", "Bash", "ls", "--json", "--flow", path)
	ticketkit.Has(t, tried.Stderr, "--flow は --lint でだけ読む")
	// 渡さなければ (flow) の苦情は出ない。
	plain := k.run(run.Opts{}, "--lint", "--json")
	if got := flowOnly(jl(ticketkit.ParseJSON(t, plain.Stdout), "problems")); len(got) != 0 {
		t.Errorf("(flow) の苦情 = %v", got)
	}
}

// ---- flow.rendered と flow.candidates、構造と表記の warn

// 線の構造の検査に掛ける見本。start → 問い → (小さく) p1 → end。
const structureBase = `nodes:
  - {id: s, type: start, name: 開始}
  - id: q
    type: askUserQuestion
    name: 方針
    data:
      questionText: どちらで
      options: [{label: 小さく}, {label: 大きく}]
  - {id: p1, type: prompt, name: 読む, data: {prompt: 読む}}
  - {id: e, type: end, name: 終了}
  - {id: g, type: group, name: 枠}
connections:
  - {id: c1, from: s, to: q}
  - {id: c2, from: q, to: p1, fromPort: branch-0}
  - {id: c3, from: q, to: p1, fromPort: branch-1}
  - {id: c4, from: p1, to: e}
`

func (k lintKit) lintText(text string) map[string]any {
	k.t.Helper()
	path := ticketkit.Write(k.t, filepath.Join(k.root, "flow.yml"), text)
	return ticketkit.ParseJSON(k.t, run.Ccnavi(k.t, []string{"--root", k.root, "--lint", "--json", "--flow", path}).Stdout)
}

func TestRenderedIsWhatSubagentStartLists(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	payload := k.lintText(workflowYAML)
	rendered := jl(payload, "flow", "rendered")
	if len(rendered) < 3 || !strings.HasPrefix(rendered[0].(string), "1. [start] 開始") || !strings.Contains(rendered[2].(string), "3. [askUserQuestion] 方針") {
		t.Errorf("rendered = %v", rendered)
	}
	broken := k.lintText("nodes: [\n")
	if v, ok := jm(broken, "flow")["rendered"]; !ok || v != nil {
		t.Errorf("読めないときの rendered = %v", v)
	}
	// 候補は読めなくても載る（フローに依らない）。
	if js(broken, "flow", "candidates", "agents", 0, "name") != "general-purpose" {
		t.Errorf("candidates = %v", jv(broken, "flow", "candidates"))
	}
}

func TestCandidatesComeFromTheWorkspace(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	ticketkit.Write(t, filepath.Join(k.root, ".claude", "agents", "r.md"), "---\nname: rev\n---\n")
	payload := k.lintText(workflowYAML)
	found := false
	for _, a := range jl(payload, "flow", "candidates", "agents") {
		found = found || js(a, "name") == "rev"
	}
	if !found {
		t.Errorf("rev が候補に無い: %v", jv(payload, "flow", "candidates"))
	}
	if len(jl(payload, "flow", "candidates", "skills")) != 0 {
		t.Errorf("skills = %v", jv(payload, "flow", "candidates", "skills"))
	}
}

func TestStructureAndNamesAreWarnsNotErrors(t *testing.T) {
	t.Parallel()
	k := newLintKit(t)
	text := structureBase + "  - {id: c5, from: p1, to: nowhere}\n"
	text = strings.Replace(text, "{id: p1, type: prompt, name: 読む, data: {prompt: 読む}}", "{id: p1, type: subAgent, name: 読む, data: {builtInType: Explorer}}", 1)
	payload := k.lintText(text)
	path := filepath.Join(k.root, "flow.yml")
	flows := flowOnly(jl(payload, "problems"))
	if len(flows) != 2 {
		t.Fatalf("flows = %v", flows)
	}
	for _, p := range flows {
		if js(p, "severity") != "warn" || !strings.HasPrefix(js(p, "detail"), path) {
			t.Errorf("problem = %v", p)
		}
	}
	// 読めているので中身と手順は載る。
	if jv(payload, "flow", "data") == nil || jv(payload, "flow", "rendered") == nil {
		t.Errorf("flow = %v", payload["flow"])
	}
}
