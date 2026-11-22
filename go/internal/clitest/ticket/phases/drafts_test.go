package phases

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 作業・レビュー計画のテンプレートの下書きの受入テスト。
//
//  1. 下書きの置き場: 親のツリーの `wip/handoff/<取り込み先の相対パス>`。取り込み先は `--to`（既定は project）と `--layer`
//     で決まる。取り込みは `ccnavi user handoff`
//  2. `--templates --draft <親>`: 完了した親の計画から下書きを作る。`after` を計画の中の添字に直し、子のフローをコピーし、
//     同名の下書きは上書きせず断る
//  3. 下書きの効力: `--templates` の統合結果にも `templates[]` にも入らない。`--lint` は error にしない。一覧に件数、
//     `--json` に `drafts[]`
//  4. `--draft` と読む側はエージェントが打てる。下書きの置き場への Write は判定が変わらず止まらない

const (
	todoMark   = "TODO"
	guardEnv   = "CCNAVI_GUARD_TICKET_APPROVAL"
	flowIdent  = "i0001"
	flowFolder = "flows"
)

// flowImpl は子のフローの見本（flowText を字下げなしにしたもの）。
var flowImpl = func() string {
	var lines []string
	for _, l := range strings.Split(strings.TrimRight(flowText, "\n"), "\n") {
		lines = append(lines, strings.TrimPrefix(l, "    "))
	}
	return strings.Join(lines, "\n") + "\n"
}()

// drafts は完了した親（done/）を直接置いて、下書きと取り込みを動かす作業場。
type drafts struct {
	*ws
	// to は `--draft` の `--to`（下書きの置き場が決まる）。
	to string
}

func newDrafts(t *testing.T) *drafts {
	w := newWS(t)
	// 共通のフェーズ定義。取り込み先が共通のとき、この定義で検査される。
	ticketkit.Write(t, base.CommonPath(w.root, "phases"), samples.Phases)
	return &drafts{ws: w, to: "project"}
}

// targetRel は下書きの取り込み先（ツリーからの相対）。
func targetRel(name, to, layer string) string {
	if to == "common" {
		return ".ccnavi/config/common/templates/" + name + ".yml"
	}
	if layer != "" {
		return "projects/" + layer + "/.ccnavi/config/project/templates/" + name + ".yml"
	}
	return ".ccnavi/config/project/templates/" + name + ".yml"
}

// tpl は `--templates` と `--lint` を動かす。判定のオプションは付けない。guard はターミナルの確かめ。
func (d *drafts) tpl(guard string, args ...string) run.Result {
	d.t.Helper()
	env := map[string]string{}
	if guard != "" {
		env[guardEnv] = guard
	}
	return d.bare(env, args...)
}

// doneParent は親と子を done/ に置く。flows は {子の識別子: フローの本文}。
// children が無ければ、全体計画の n 番目に子 `<親>-<nn>-01` を 1 つずつ置く。
func (d *drafts) doneParent(plan []samples.Step, feedback []samples.Step, flows map[string]string) {
	d.doneParentAt(parentName, d.parentTree, "done", plan, feedback, flows, nil)
}

func (d *drafts) doneParentAt(parent, tree, place string, plan, feedback []samples.Step, flows map[string]string, children map[string]int) {
	d.t.Helper()
	root := filepath.Join(tree, ".ccnavi", "approved")
	ticketkit.Write(d.t, filepath.Join(root, place, parent+".md"), samples.ParentText(parent, plan, samples.ParentOpts{Feedback: feedback}))
	if children == nil {
		children = map[string]int{}
		for n := 1; n <= len(plan); n++ {
			children[parent+"-"+pad2(n)+"-01"] = n
		}
	}
	for child, phase := range children {
		ticketkit.Write(d.t, filepath.Join(root, place, child+".md"), samples.ChildText(child, parent, phase, []string{"src/*"}))
	}
	for child, body := range flows {
		ticketkit.Write(d.t, filepath.Join(root, flowFolder, child+".yml"), body)
	}
}

func pad2(n int) string {
	if n < 10 {
		return "0" + string(rune('0'+n))
	}
	return itoa(n)
}

// draftPath は下書きの置き場。取り込み先の前に `wip/handoff/` を付けたもの。
func (d *drafts) draftPath(name string, tree ...string) string {
	at := d.parentTree
	if len(tree) > 0 {
		at = tree[0]
	}
	return filepath.Join(at, "wip", "handoff", filepath.FromSlash(targetRel(name, d.to, "")))
}

func (d *drafts) draft(extra ...string) run.Result {
	args := []string{"--templates", "--draft", parentName}
	if d.to != "project" {
		args = append(args, "--to", d.to)
	}
	return d.tpl("", append(args, extra...)...)
}

func (d *drafts) readDraft() string { return ticketkit.Read(d.t, d.draftPath(parentName)) }

// checked は下書きを `--templates --check-file` で読んだ答え。
func (d *drafts) checked() map[string]any {
	d.t.Helper()
	r := d.tpl("", "--templates", "--check-file", d.draftPath(parentName), "--json")
	if r.Code != 0 {
		d.t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	a := ticketkit.ParseJSON(d.t, r.Stdout)
	if a["valid"] != true {
		d.t.Fatalf("下書きが読めない: %v", a)
	}
	return a
}

type draftStep struct {
	Type     string
	After    []int
	Deferred bool
}

func draftSteps(raw any) []draftStep {
	var out []draftStep
	list, _ := raw.([]any)
	for _, item := range list {
		s := draftStep{After: []int{}}
		switch v := item.(type) {
		case string:
			s.Type = v
		case map[string]any:
			s.Type, _ = v["type"].(string)
			s.Deferred, _ = v["deferred"].(bool)
			if a, ok := v["after"].([]any); ok {
				for _, n := range a {
					s.After = append(s.After, int(n.(float64)))
				}
			}
		}
		out = append(out, s)
	}
	return out
}

// generalize は下書きの TODO を直す（エージェントが一般化したあとの形）。直した本文のハッシュを返す。
func (d *drafts) generalize() string {
	d.t.Helper()
	text := d.readDraft()
	fixed := strings.Replace(text, todoMark+" 一般化する: 親", "機能を設計から実装まで進める", 1)
	fixed = strings.Replace(fixed, todoMark+" 一般化する: 「親」と同じ形の作業をするとき", "新しい機能を足すとき", 1)
	if strings.Contains(fixed, todoMark) {
		d.t.Fatalf("TODO が残っている:\n%s", fixed)
	}
	ticketkit.Write(d.t, d.draftPath(parentName), fixed)
	sum := sha256.Sum256([]byte(fixed))
	return hex.EncodeToString(sum[:])
}

// handoff は下書き 1 本を `ccnavi user handoff <rel> --yes --expect <見せたハッシュ>` で取り込む。
func (d *drafts) handoff(rel, tree string) run.Result {
	d.t.Helper()
	env := map[string]string{guardEnv: "disable"}
	shown := run.Ccnavi(d.t, []string{"--root", tree, "user", "handoff", rel, "--json"}, run.Opts{Env: env, Dir: base.Root()})
	if shown.Code != 0 {
		d.t.Fatalf("handoff 表示: code %d\n%s%s", shown.Code, shown.Stdout, shown.Stderr)
	}
	expect := ticketkit.ParseJSON(d.t, shown.Stdout)["expect"].(string)
	return run.Ccnavi(d.t, []string{"--root", tree, "user", "handoff", rel, "--yes", "--expect", expect, "--json"}, run.Opts{Env: env, Dir: base.Root()})
}

func (d *drafts) answer(args ...string) map[string]any {
	d.t.Helper()
	r := d.tpl("", append([]string{"--templates", "--json"}, args...)...)
	if r.Code != 0 {
		d.t.Fatalf("code %d\n%s", r.Code, r.Stderr)
	}
	return ticketkit.ParseJSON(d.t, r.Stdout)
}

func TestTheMadeDraftReadsBackAndTheTextAsksForGeneralizing(t *testing.T) {
	t.Parallel()
	d := newDrafts(t)
	d.doneParent(planOf("research", "design", "implement"), planOf("implement-feedback", "implement-feedback"), map[string]string{"i0001-03-01": flowImpl})
	r := d.draft()
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	contains(t, "stdout", r.Stdout, "wip/handoff/.ccnavi/config/project/templates/i0001.yml",
		"ccnavi-user-handoff.sh .ccnavi/config/project/templates/i0001.yml",
		"全体計画 3 ステップ / フィードバック計画 2 ステップ / フロー 1 本")
	// 説明に TODO の印が残り、検査が warn を言う。次の手は 1 つで、未追跡の下書きが ready を止めることも言う
	contains(t, "stdout", r.Stdout, "warn", "`description` に "+todoMark+" が残っている", "`when` に "+todoMark+" が残っている", "未追跡", "ready が止まる")
	nexts := 0
	for _, l := range strings.Split(r.Stdout, "\n") {
		if strings.HasPrefix(l, "次: ") {
			nexts++
		}
	}
	if nexts != 1 {
		t.Errorf("次: の行が %d 本", nexts)
	}
	data := d.checked()["data"].(map[string]any)
	if data["name"] != "i0001" {
		t.Errorf("name = %v", data["name"])
	}
	plan := draftSteps(data["plan"])
	var types []string
	var afters [][]int
	for _, s := range plan {
		types = append(types, s.Type)
		afters = append(afters, s.After)
	}
	equalStrings(t, "計画の型", types, []string{"research", "design", "implement"})
	if got := fmt.Sprint(afters); got != "[[] [1] [2]]" {
		t.Errorf("after = %s", got)
	}
	flows := data["flows"].(map[string]any)
	if len(flows) != 1 || flows["3"].(map[string]any)["name"] != "実装の手順" {
		t.Errorf("flows = %v", flows)
	}
	if !strings.HasPrefix(data["description"].(string), todoMark) || !strings.HasPrefix(data["when"].(string), todoMark) {
		t.Errorf("description/when に TODO が付いていない: %v", data)
	}
	// 一般化して印を消せば、苦情は消える。
	d.generalize()
	if p := d.answer()["drafts"].([]any)[0].(map[string]any)["problems"].([]any); len(p) != 0 {
		t.Errorf("problems = %v", p)
	}
}

func TestTheDraftIsTakenByHandoff(t *testing.T) {
	t.Parallel()
	d := newDrafts(t)
	d.doneParent(planOf("design", "implement"), nil, map[string]string{"i0001-02-01": flowImpl})
	if d.draft().Code != 0 {
		t.Fatal("--draft が通らない")
	}
	sha := d.generalize()
	taken := d.handoff(targetRel(parentName, "project", ""), d.parentTree)
	if taken.Code != 0 {
		t.Fatalf("code %d\n%s%s", taken.Code, taken.Stdout, taken.Stderr)
	}
	placed := ticketkit.Read(t, filepath.Join(d.parentTree, filepath.FromSlash(targetRel(parentName, "project", ""))))
	if sum := sha256.Sum256([]byte(placed)); hex.EncodeToString(sum[:]) != sha {
		t.Error("取り込んだ中身が下書きと違う")
	}
	if ticketkit.Exists(d.draftPath(parentName)) {
		t.Error("取り込んだのに下書きが残っている")
	}
}

func TestADraftIsListedApartFromTheTemplates(t *testing.T) {
	t.Parallel()
	d := newDrafts(t)
	lacks(t, "一覧", d.tpl("", "--templates").Stdout, "下書き")
	d.doneParent(planOf("design"), nil, nil)
	d.draft()
	// 統合結果にも合計にも入らず、`--show` でも引けない
	a := d.answer()
	if len(a["templates"].([]any)) != 0 {
		t.Errorf("templates = %v", a["templates"])
	}
	for _, v := range a["layers"].([]any) {
		if rows := v.(map[string]any)["templates"].([]any); len(rows) != 0 {
			t.Errorf("layers の templates = %v", rows)
		}
	}
	if sums := a["sums"].([]any)[0].(map[string]any)["templates"].([]any); len(sums) != 0 {
		t.Errorf("sums = %v", sums)
	}
	if d.tpl("", "--templates", "--show", parentName).Code != 1 {
		t.Error("下書きが --show で引けた")
	}
	// 一覧は下書きがあることを 1 行で言う
	out := d.tpl("", "--templates").Stdout
	contains(t, "一覧", out, "下書き: 1 件（wip/handoff/）")
	if n := strings.Count(out, "下書き"); n != 1 {
		t.Errorf("下書きの行が %d 本", n)
	}
	// 語で絞ると下書きも絞られる
	if got := d.answer("--keyword", "親")["drafts"].([]any); len(got) != 1 {
		t.Errorf("drafts = %v", got)
	}
	if got := d.answer("--keyword", "無い語")["drafts"].([]any); len(got) != 0 {
		t.Errorf("drafts = %v", got)
	}
}

func planTemplate(name, plan string) string { return templateText(name, plan, "", "") }

func TestLintNeverMakesADraftAnError(t *testing.T) {
	t.Parallel()
	d := newDrafts(t)
	ticketkit.Write(t, d.draftPath("broken"), "name: [\n")
	ticketkit.Write(t, d.draftPath("ends"), planTemplate("ends", "  - design\n  - acceptance\n"))
	d.doneParent(planOf("design"), nil, nil)
	d.draft()
	r := d.tpl("", "--lint", "--json", "--mode", "enable")
	body := ticketkit.ParseJSON(t, r.Stdout)
	var found []map[string]any
	var others []map[string]any
	for _, p := range body["problems"].([]any) {
		m := p.(map[string]any)
		if m["where"] == "(template-drafts)" {
			found = append(found, m)
		} else if strings.Contains(m["where"].(string), "templates") {
			others = append(others, m)
		}
	}
	if len(found) == 0 {
		t.Fatal("下書きの苦情が無い")
	}
	hasWarn := false
	var details []string
	for _, p := range found {
		switch p["severity"] {
		case "warn":
			hasWarn = true
		case "info":
		default:
			t.Errorf("下書きが %v になった: %v", p["severity"], p)
		}
		details = append(details, p["detail"].(string))
	}
	if !hasWarn {
		t.Error("warn が無い")
	}
	contains(t, "details", strings.Join(details, " "), "wip/handoff/.ccnavi/config/project/templates/broken.yml", todoMark)
	// ほかの苦情（効力のある側）には混ざらない。
	if len(others) != 0 {
		t.Errorf("効力のある側に混ざった: %v", others)
	}
	if body["version"] != float64(1) {
		t.Errorf("version = %v", body["version"])
	}
	// 文の出力でも error にならない
	text := d.tpl("", "--lint", "--mode", "enable").Stdout
	contains(t, "lint", text, "template-drafts")
	lacks(t, "lint", text, "error: (template-drafts)")
}

func TestADraftInAProjectTreeIsFoundAtTheProjectsPlace(t *testing.T) {
	t.Parallel()
	d := newDrafts(t)
	// 下書きの行でないファイルは一覧に出ない
	ticketkit.Write(t, filepath.Join(d.root, "wip", "handoff", "learnings", "x.md"), "x\n")
	ticketkit.Write(t, filepath.Join(d.root, "wip", "handoff", ".ccnavi", "docs", "a.md"), "a\n")
	if got := d.answer()["drafts"].([]any); len(got) != 0 {
		t.Errorf("drafts = %v", got)
	}
	project := filepath.Join(d.root, "projects", "lib")
	ticketkit.Write(t, filepath.Join(project, ".git", "keep"), "")
	ticketkit.Write(t, d.draftPath("lib-draft", project), planTemplate("lib-draft", "  - design\n"))
	rows := d.answer()["drafts"].([]any)
	if len(rows) != 1 {
		t.Fatalf("drafts = %v", rows)
	}
	row := rows[0].(map[string]any)
	if row["checked_with"] != "lib" || row["layer"] != "lib" || row["tree"] != "lib" || row["path"] != "wip/handoff/.ccnavi/config/project/templates/lib-draft.yml" {
		t.Errorf("row = %v", row)
	}
}

func TestTheDraftPlaceIsNotPartOfThePostCheck(t *testing.T) {
	t.Parallel()
	d := newDrafts(t)
	ticketkit.Write(t, d.draftPath("x"), "name: x\n")
	ok := ticketkit.Write(t, filepath.Join(d.parentTree, "wip", "a.md"), "a\n")
	r := d.hook("PostToolUse", "Write", d.parentTree, input{"file_path": ok, "content": "a"})
	lacks(t, "post", r.Stdout+r.Stderr, "POST_TICKET_SCOPE", "POST_VIOLATION")
}

func TestADraftIsNotReadAsATicket(t *testing.T) {
	t.Parallel()
	d := newDrafts(t)
	ticketkit.Write(t, d.draftPath("x"), planTemplate("x", "  - design\n"))
	d.git(d.parentTree, "add", "-A")
	r := d.mustOK(d.ccnavi("--explain", "--json"), "--explain")
	if tickets := ticketkit.ParseJSON(t, r.Stdout)["tickets"].([]any); len(tickets) != 0 {
		t.Errorf("tickets = %v", tickets)
	}
}
