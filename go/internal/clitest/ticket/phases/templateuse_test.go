package phases

import (
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/yaml"
)

// テンプレートを使う側（`ccnavi ticket new <識別子> --template <名前>`）の受入テスト（tests/ticket/test_template_use.py）。
//
//  1. 親の提案: テンプレートのステップを `plan:` / `feedback:` にコピーする。`after` は計画の中の添字から提案の絶対番号に
//     付け直す（フィードバック計画は全体のステップ数を足す）。`phases:` のコピーが入り、`--lint` と承認の確認が error にならない
//  2. 往復: 完了した親から `--templates --draft` で作った下書きを `user handoff` で取り込み、`--template` で使うと、
//     元の計画と同じ形・同じフローになる
//  3. 子のフローの下書き: 全体計画の n 番目のステップの最初の子 `<親>-<NN>-01` の `wip/proposals/flows/<子>.yml` に書く
//  4. 断る: 無い名前、使えない名前、定義が phases.yml に無い、既にある提案・フロー下書き。断ったときは何も書かない
//  5. 統合結果: 共通とレイヤーで衝突した名前は共通が引かれ、そのことを言う

const useIdent = "feature-9-from-template"

const useFeature = `name: feature
description: 機能を設計から実装まで進める
when: 新しい機能を足すとき
plan:
  - research
  - {type: design, deferred: true, after: [1]}
  - {type: implement, after: [2]}
feedback:
  - implement-feedback
  - {type: implement-feedback, after: [1]}
flows:
  3:
` + flowText

const useSmall = `name: small
description: 設計して実装する
plan:
  - design
  - {type: implement, after: [1]}
`

// placeholder は雛形の空欄の目印（`（ここに…を書く）`）。
var placeholder = regexp.MustCompile(`（ここに[^）\n]*を書く）`)

// variant は useSmall の名前を変え、文字列の置き換えを重ねた本文。
func variant(name string, pairs ...[2]string) string {
	text := strings.Replace(useSmall, "name: small", "name: "+name, 1)
	for _, p := range pairs {
		text = strings.ReplaceAll(text, p[0], p[1])
	}
	return text
}

// use はテンプレートを置いて `ticket new --template` を打つ作業場。
type use struct {
	*drafts
	tree, todo string
}

func newUse(t *testing.T) *use {
	d := newDrafts(t)
	tree := d.worktree(useIdent, "main")
	return &use{drafts: d, tree: tree, todo: filepath.Join(tree, "wip", "proposals", "todo", useIdent+".md")}
}

// put はテンプレートを共通（common）かプロジェクトのレイヤー（それ以外）に置く。
func (u *use) put(name, text, layer string) string {
	where := "common"
	if layer != "common" {
		where = "project"
	}
	return write(u.t, filepath.Join(u.root, ".ccnavi", "config", where, "templates", name+".yml"), text)
}

// newTicket は `ticket new` を打つ。issue と MR は渡さなければ既定を足す。
func (u *use) newTicket(template, ident, cwd string, extra ...string) run.Result {
	u.t.Helper()
	if ident == "" {
		ident = useIdent
	}
	if cwd == "" {
		cwd = u.tree
	}
	args := []string{"ticket", "new", ident, "--title", "試す"}
	if template != "" {
		args = append(args, "--template", template)
	}
	for _, f := range [][2]string{{"--issue", "9"}, {"--mr", "5"}} {
		have := false
		for _, e := range extra {
			have = have || e == f[0]
		}
		if !have {
			args = append(args, f[0], f[1])
		}
	}
	return u.ccnavi(append(append(args, extra...), "--cwd", cwd)...)
}

func (u *use) newT(extra ...string) run.Result { return u.newTicket("feature", "", "", extra...) }

func (u *use) written() string { return read(u.t, u.todo) }

func (u *use) flowsDir() string { return filepath.Join(u.tree, "wip", "proposals", "flows") }

func child(n int) string { return useIdent + "-" + pad2(n) + "-01" }

func (u *use) draftOf(n int) string { return filepath.Join(u.flowsDir(), child(n)+".yml") }

// assertApprovable は空欄の目印を埋めてコミットすれば、`--lint` に error が無く、承認の確認が通ること。
func (u *use) assertApprovable() {
	u.t.Helper()
	write(u.t, u.todo, placeholder.ReplaceAllString(u.written(), "書いた内容"))
	u.git(u.tree, "add", "-A")
	u.git(u.tree, "commit", "--quiet", "-m", "scaffold")
	lint := u.ccnavi("--lint", "--mode", "enable")
	if lint.Code != 0 || !strings.Contains(lint.Stdout, "error 0 件") {
		u.t.Errorf("lint: code %d\n%s%s", lint.Code, lint.Stdout, lint.Stderr)
	}
	if verify := u.ccnavi("--agree", "--preview", "--verify"); verify.Code != 0 {
		u.t.Errorf("--verify: code %d\n%s%s", verify.Code, verify.Stdout, verify.Stderr)
	}
}

func (u *use) nothingWritten() {
	u.t.Helper()
	if exists(u.todo) || exists(u.flowsDir()) {
		u.t.Error("断ったのに何かを書いた")
	}
}

func TestTheTemplateProposalPassesTheApprovalChecks(t *testing.T) {
	t.Parallel()
	u := newUse(t)
	u.put("feature", useFeature, "common")
	u.mustOK(u.newT("--mr", "5"), "ticket new")
	// 雛形の空欄の目印は、書き換えるまで承認の検査が止める
	if !placeholder.MatchString(u.written()) {
		t.Error("空欄の目印が無い")
	}
	// 全体計画だけの提案は、承認のあとで全体計画を改版できる形のまま（検査を通る）
	u.assertApprovable()
}

func TestATemplateWithoutFeedbackOrFlowsAndAWarningThatDoesNotStop(t *testing.T) {
	t.Parallel()
	u := newUse(t)
	u.put("feature", useFeature, "common")
	u.put("small", useSmall, "common")
	r := u.mustOK(u.newTicket("small", "", ""), "ticket new")
	lacks(t, "stdout", r.Stdout, "--with-feedback")
	contains(t, "stdout", r.Stdout, "フローの下書きはありません")
	if exists(u.flowsDir()) {
		t.Error("フローが無いのに下書きの置き場ができた")
	}
	plan := stepsOf(frontOf(t, u.written()), "plan")
	if !reflect.DeepEqual(plan, []step{{Type: "design", After: []int{}}, {Type: "implement", After: []int{1}}}) {
		t.Errorf("plan = %+v", plan)
	}
	// フィードバック計画の無いテンプレートに --with-feedback を付けても、何も足さない
	remove(t, u.todo)
	u.mustOK(u.newTicket("small", "", "", "--with-feedback"), "with-feedback")
	if frontOf(t, u.written()).Has("feedback") {
		t.Error("フィードバック計画が足された")
	}
	// テンプレートの warn は出すが、止めない
	remove(t, u.todo)
	u.put("todo", variant("todo", [2]string{"設計して", "TODO 設計して"}), "common")
	r = u.mustOK(u.newTicket("todo", "", ""), "todo")
	contains(t, "stdout", r.Stdout, "warn:", "TODO")
}

func TestAnExistingFlowDraftOrChildProposalStopsItAndNothingIsWritten(t *testing.T) {
	t.Parallel()
	u := newUse(t)
	u.put("feature", useFeature, "common")
	write(t, u.draftOf(3), "keep\n")
	r := u.newT()
	if r.Code == 0 {
		t.Fatal("既にあるフロー下書きがあるのに通った")
	}
	contains(t, "stderr", r.Stderr, "上書きしない", child(3))
	if exists(u.todo) {
		t.Error("親の提案を書いた")
	}
	if read(t, u.draftOf(3)) != "keep\n" {
		t.Error("既にあるフロー下書きを上書きした")
	}
	remove(t, u.draftOf(3))
	write(t, filepath.Join(u.tree, "wip", "proposals", "todo", child(3)+".md"), "x\n")
	r = u.newT()
	if r.Code == 0 || !strings.Contains(r.Stderr, "上書きしない") || exists(u.todo) {
		t.Errorf("既にある子の提案: code %d\n%s", r.Code, r.Stderr)
	}
	// 子の識別子が長さの上限に収まらない親は断る
	long := "feature-9-" + strings.Repeat("x", 50)
	tree := u.worktree(long, "main")
	r = u.newTicket("feature", long, tree)
	if r.Code == 0 || !strings.Contains(r.Stderr, "子の識別子") {
		t.Errorf("長すぎる識別子: code %d\n%s", r.Code, r.Stderr)
	}
	if exists(filepath.Join(tree, "wip", "proposals")) {
		t.Error("断ったのに何かを書いた")
	}
}

func TestATemplateThatCannotBeUsedIsRefusedWithoutWriting(t *testing.T) {
	t.Parallel()
	u := newUse(t)
	r := u.newT()
	if r.Code == 0 || !strings.Contains(r.Stderr, "無い") {
		t.Errorf("無い名前: code %d\n%s", r.Code, r.Stderr)
	}
	u.nothingWritten()
	cases := []struct {
		name, text string
		needles    []string
	}{
		{"bad", variant("bad", [2]string{"after: [1]", "after: [2]"}), []string{"error", "bad"}},                // after が自分以降を指す
		{"odd", variant("other"), []string{"利用できない"}},                                                           // 名前とファイル名が合わない
		{"ghost", variant("ghost", [2]string{"implement", "ghost-type"}), []string{"ghost-type", "phases.yml"}}, // phases.yml に無い型
	}
	for _, c := range cases {
		u.put(c.name, c.text, "common")
		r := u.newTicket(c.name, "", "")
		if r.Code == 0 {
			t.Errorf("%s: 通った", c.name)
		}
		contains(t, c.name+" の stderr", r.Stderr, c.needles...)
		u.nothingWritten()
	}
	// phases.yml が無い
	u.put("small", useSmall, "common")
	remove(t, filepath.Join(u.root, ".ccnavi", "config", "common", "phases.yml"))
	remove(t, u.phases)
	r = u.newTicket("small", "", "")
	if r.Code == 0 || !strings.Contains(r.Stderr, "phases.yml") {
		t.Errorf("phases.yml が無い: code %d\n%s", r.Code, r.Stderr)
	}
	u.nothingWritten()
}

func TestANameThatClashesUsesTheCommonOneAndSaysSo(t *testing.T) {
	t.Parallel()
	u := newUse(t)
	u.put("small", useSmall, "common")
	u.put("small", strings.Replace(useSmall, "設計して実装する", "別の中身", 1), "self")
	r := u.mustOK(u.newTicket("small", "", ""), "ticket new")
	contains(t, "stdout", r.Stdout, "note:", "共通と同じ名前で中身が違う", ".ccnavi/config/common/templates/small.yml")
	// レイヤーにだけあるテンプレートも使える
	remove(t, u.todo)
	remove(t, filepath.Join(u.root, ".ccnavi", "config", "common", "templates", "small.yml"))
	r = u.mustOK(u.newTicket("small", "", ""), "layer only")
	contains(t, "stdout", r.Stdout, ".ccnavi/config/project/templates/small.yml")
}

// ---- 往復: 完了した親 -> --draft -> user handoff -> --template

func (u *use) take() run.Result {
	rel := targetRel(parentName, "common", "")
	write(u.t, filepath.Join(u.root, "wip", "handoff", filepath.FromSlash(rel)), u.readDraft())
	remove(u.t, u.draftPath(parentName))
	return u.handoff(rel, u.root)
}

// roundtrip は `--draft` → 取り込み → `--template ... --with-feedback`。元の親の計画と、作った提案の frontmatter を返す。
func (u *use) roundtrip(plan, feedback []samples.Step, flowChild string, withFeedback bool) (original, made *yaml.Map, result run.Result) {
	u.t.Helper()
	u.to = "common"
	u.doneParent(plan, feedback, map[string]string{flowChild: flowImpl})
	if u.draft().Code != 0 {
		u.t.Fatal("--draft が通らない")
	}
	u.generalize()
	if taken := u.take(); taken.Code != 0 {
		u.t.Fatalf("取り込み: code %d\n%s%s", taken.Code, taken.Stdout, taken.Stderr)
	}
	extra := []string{}
	if withFeedback {
		extra = append(extra, "--with-feedback")
	}
	result = u.mustOK(u.newTicket(parentName, "", "", extra...), "ticket new")
	original = frontOf(u.t, samples.ParentText(parentName, plan, samples.ParentOpts{Feedback: feedback}))
	return original, frontOf(u.t, u.written()), result
}

func assertSamePlan(t *testing.T, original, made *yaml.Map) {
	t.Helper()
	for _, key := range []string{"plan", "feedback"} {
		if got, want := stepsOf(made, key), stepsOf(original, key); !reflect.DeepEqual(got, want) {
			t.Errorf("%s = %+v, want %+v", key, got, want)
		}
	}
}

func (u *use) readFlow(n int) *yaml.Map {
	u.t.Helper()
	data, err := yaml.Load(read(u.t, u.draftOf(n)))
	m, _ := data.(*yaml.Map)
	if err != nil || m == nil {
		u.t.Fatalf("フローの下書きが読めない: %v", err)
	}
	return m
}

func TestATemplateDraftedFromADoneParentGivesTheSamePlanAndFlow(t *testing.T) {
	t.Parallel()
	u := newUse(t)
	plan := []samples.Step{after("research"), {Type: "design", Deferred: true, After: []int{1}}, after("implement", 1, 2)}
	feedback := []samples.Step{after("implement-feedback"), after("implement-feedback", 4)}
	original, made, _ := u.roundtrip(plan, feedback, "i0001-03-01", true)
	assertSamePlan(t, original, made)
	want, _ := yaml.Load(flowImpl)
	if !yaml.Equal(u.readFlow(3), want) {
		t.Error("フローの下書きが元のフローと違う")
	}
}

func TestDeferMultipleAfterRepeatedTypesAndFeedbackAfterRoundTrip(t *testing.T) {
	t.Parallel()
	// defer・複数の after・同じ型の繰り返し、フィードバック計画の after が複数。
	u := newUse(t)
	plan := []samples.Step{after("research"), {Type: "design", Deferred: true, After: []int{1}}, after("design", 1), after("implement", 2, 3)}
	feedback := []samples.Step{after("implement-feedback"), after("implement-feedback", 5), {Type: "implement-feedback", Deferred: true, After: []int{5, 6}}, after("implement-feedback", 5, 7)}
	original, made, _ := u.roundtrip(plan, feedback, "i0001-04-01", true)
	assertSamePlan(t, original, made)
	var afters [][]int
	for _, s := range stepsOf(made, "feedback") {
		afters = append(afters, s.After)
	}
	if !reflect.DeepEqual(afters, [][]int{{}, {5}, {5, 6}, {5, 7}}) {
		t.Errorf("feedback の after = %v", afters)
	}
	if name := u.readFlow(4).Text("name"); name != "実装の手順" {
		t.Errorf("フローの name = %q", name)
	}
}

func TestWithoutWithFeedbackTheMainPlanAloneRoundTrips(t *testing.T) {
	t.Parallel()
	u := newUse(t)
	plan := []samples.Step{after("research"), {Type: "design", Deferred: true, After: []int{1}}, after("implement", 1, 2)}
	feedback := []samples.Step{after("implement-feedback"), after("implement-feedback", 4)}
	original, made, result := u.roundtrip(plan, feedback, "i0001-03-01", false)
	if got, want := stepsOf(made, "plan"), stepsOf(original, "plan"); !reflect.DeepEqual(got, want) {
		t.Errorf("plan = %+v, want %+v", got, want)
	}
	if made.Get("feedback") != nil {
		t.Errorf("feedback = %v", made.Get("feedback"))
	}
	contains(t, "stdout", result.Stdout, "フィードバック計画（2 ステップ）がある")
}
