package phases

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/yaml"
)

// 親に固定するフェーズ定義（親の `phases:`）の受入テスト（tests/ticket/test_parent_phases.py）。
// 計画を持つ親は、計画が使う定義だけのコピーを `phases:` に持つ。見るのは 5 つ。
//  1. 承認の検査。コピーは承認したときの phases.yml の同じ名前の定義と、読んだ形で同じでなければ落ちる。
//  2. 承認のあとの判定はコピーを読み、phases.yml を読まない
//  3. コピーの無い計画付きの親と、コピーを書いた子は、手で動かしても判定で止まる
//  4. 改版の規則。コピーだけの違いも改版になり、子が承認された番号の定義は変えられない
//  5. 補助のフラグ `--plan-order <親> --fill-phases`

// defs は phases.yml の本文から、使う定義だけを写した `phases:` の行。
func defs(from string, names ...string) string { return samples.PhasesBlock(from, names) }

// withBlock は `phases:` の無い親の本文の `plan:` の前に、渡した `phases:` の行を差し込む。
func withBlock(text, block string) string { return strings.Replace(text, "plan:", block+"plan:", 1) }

// noCopy はコピー（phases:）の無い親の提案。
func (w *ws) noCopy(plan []samples.Step) string {
	return samples.ParentText(parentName, plan, samples.ParentOpts{NoCopy: true})
}

// joinDefs は 2 つの `phases:` の行を 1 つにまとめる。
func joinDefs(a, b string) string { return a + strings.TrimPrefix(b, "phases:\n") }

func (w *ws) proposeParent(text string) (r run.Result) {
	w.t.Helper()
	w.propose(parentName, text)
	w.commitParent()
	return w.approve()
}

func TestAMatchingCopyIsApprovedAsIsAndTheScreenSortsTheDefinitions(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	text := w.parent(planOf("research", "design"), samples.ParentOpts{})
	r := w.proposeParent(text)
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	// 承認は中身を変えない。コピーは承認の前に提案へ書いてある。
	if got := read(t, w.doing(parentName)); got != text {
		t.Errorf("承認が中身を変えた:\n%s", got)
	}
	screen := r.Stdout
	contains(t, "承認画面", screen, "■ この親に固定するフェーズ定義")
	_, afterFixed, _ := strings.Cut(screen, "■ この親に固定するフェーズ定義")
	fixed, _, _ := strings.Cut(afterFixed, "■")
	contains(t, "固定する定義", fixed, "調査 / research", "wip/research/*", "wip/research/summary.md", "使う番号: 1", "使う番号: 2")
	// phases.yml にあってこの親では使わない定義は、名前とタイトルを 1 行に並べる。止めない。
	contains(t, "承認画面", screen, "■ この親では使わない定義")
	_, afterUnused, _ := strings.Cut(screen, "■ この親では使わない定義")
	unused, _, _ := strings.Cut(afterUnused, "■")
	contains(t, "使わない定義", unused, "受入テスト作成")
	lacks(t, "使わない定義", unused, "調査")
	lacks(t, "承認画面", screen, "使われていない定義")
}

func TestACopyWhoseValuesDifferFromPhasesYmlIsRefused(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	block := strings.Replace(defs(samples.Phases, "research", "design"), "review: true", "review: false", 1)
	r := w.proposeParent(withBlock(w.noCopy(planOf("research", "design")), block))
	if r.Code == 0 {
		t.Fatal("定義と違うコピーが承認された")
	}
	contains(t, "stderr", r.Stderr, "phases.yml の定義と違う", "review", "--plan-order i0001 --fill-phases")
	if w.isApproved(parentName) {
		t.Error("承認済みチケットが置かれた")
	}
}

func TestWritingStyleDoesNotCountAsADifference(t *testing.T) {
	t.Parallel()
	// 読んだ形で比べる。キーの順・書き方・既定値を省いた書き方の違いは同じとみなす。
	w := newWS(t)
	block := `phases:
  research:
    when: 既存の振る舞いが分からないとき
    skill: process-research
    agent: explorer
    deliverables: ["wip/research/summary.md"]
    scope: ["wip/research/*/"]
    review: false
    title: 調査
    kind: work
  design:
    kind: work
    title: 設計
    scope: ["wip/design/*"]
`
	if r := w.proposeParent(withBlock(w.noCopy(planOf("research", "design")), block)); r.Code != 0 {
		t.Errorf("書き方だけ違うコピーが拒まれた: code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func TestAnInvalidCopyIsRefused(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name  string
		plan  []samples.Step
		block string // 空なら phases: を書かない
		want  []string
	}{
		{"phases.yml に無い名前のコピー", planOf("research", "extra"),
			defs(samples.Phases, "research") + "  extra:\n    kind: work\n    title: 余分\n    review: false\n",
			[]string{"`extra` は phases.yml にありません。"}},
		{"計画のステップの定義がコピーに無い", planOf("research", "design"), defs(samples.Phases, "research"),
			[]string{"`design` が `phases:` にありません"}},
		{"計画があってコピーが無い", planOf("research", "design"), "",
			[]string{"`phases:` がありません。", "--fill-phases"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t)
			text := w.noCopy(c.plan)
			if c.block != "" {
				text = withBlock(text, c.block)
			}
			r := w.proposeParent(text)
			if r.Code == 0 {
				t.Fatalf("承認された\n%s", r.Stdout)
			}
			contains(t, "stderr", r.Stderr, c.want...)
		})
	}
}

func TestAnUnusedDefinitionIsAWarningAndShownOnTheScreen(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	text := withBlock(w.noCopy(planOf("research", "design")), defs(samples.Phases, "research", "design", "acceptance"))
	r := w.proposeParent(text)
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	contains(t, "承認画面", r.Stdout, "■ 使われていない定義")
	_, after, _ := strings.Cut(r.Stdout, "■ 使われていない定義")
	unused, _, _ := strings.Cut(after, "■")
	contains(t, "使われていない定義", unused, "受入テスト作成 / acceptance")
}

func TestAChildWithPhasesIsRefused(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", strings.Replace(childText("i0001-01-01", 1, "wip/research/*"), "phase: 1\n", "phase: 1\nphases: {}\n", 1))
	w.commitParent()
	r := w.approve()
	if r.Code == 0 || !strings.Contains(r.Stderr, "親だけの欄") {
		t.Errorf("phases を持つ子が通る: code %d\n%s", r.Code, r.Stderr)
	}
	if w.isApproved("i0001-01-01") {
		t.Error("承認済みチケットが置かれた")
	}
}

// ---- 2・3. 承認のあとの判定はコピーを読む

func (w *ws) writeToTree(tree, rel string) run.Result {
	return w.hook("PreToolUse", "Write", tree, input{"file_path": filepath.Join(tree, filepath.FromSlash(rel)), "content": "x\n"})
}

func (w *ws) approvedResearchChild(review samples.Flag) string {
	w.t.Helper()
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, review, "wip/research/*", "src/a/*"))
	w.commitParent("approval-request child")
	w.mustApprove()
	return w.runChild("i0001-01-01")
}

func TestEditingPhasesYmlAfterApprovalDoesNotChangeARunningParent(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	tree := w.approvedResearchChild(samples.On)
	cases := []struct{ label, text string }{
		{"定義を広げた", strings.Replace(samples.Phases, `scope: ["wip/research/*"]`, `scope: ["src/*"]`, 1)},
		{"定義を消した", phasesWithoutResearch()},
		{"phases.yml が壊れた", "version: 1\nphases: [\n"},
	}
	for _, c := range cases {
		write(t, w.phases, c.text)
		// コピーした定義の上限（wip/research/*）で切り詰めたまま。
		beyond := w.writeToTree(tree, "src/a/x.py")
		if w.decision(beyond) != "deny" {
			t.Errorf("%s: 定義の外が止まらない: %s", c.label, beyond.Stdout)
		}
		contains(t, c.label+" の理由", w.reason(beyond), "limit: phase type 調査 (research)")
		if inside := w.writeToTree(tree, "wip/research/note.md"); w.decision(inside) == "deny" {
			t.Errorf("%s: 定義の中が止まった: %s", c.label, w.reason(inside))
		}
	}
}

func TestPhasesTitlesAndReviewsComeFromTheCopyAfterApproval(t *testing.T) {
	t.Parallel()
	// 承認のあとに phases.yml の定義を直しても、フェーズのタイトル・レビューの要否・止め方と SubagentStart の案内はコピーのまま。
	w := newWS(t)
	tree := w.approvedResearchChild(samples.Off)
	write(t, w.phases, strings.Replace(strings.Replace(samples.Phases,
		"    title: 調査\n    review: false\n", "    title: 調べ\n    review: true\n", 1), "agent: explorer", "agent: planner", 1))
	board := jsonOf(t, w.ccnavi("--explain", "--json").Stdout)
	var shown []string
	for _, p := range board["parents"].([]any) {
		owner := p.(map[string]any)
		if owner["ticket"] != parentName {
			continue
		}
		for _, ph := range owner["phases"].([]any) {
			m := ph.(map[string]any)
			shown = append(shown, strings.Join([]string{jsonString(m["number"]), jsonString(m["review_required"]), m["title"].(string)}, "|"))
		}
	}
	equalStrings(t, "フェーズ", shown, []string{"1|false|調査", "2|true|設計"})
	said := w.reason(w.hookWith(hookOpts{AgentID: "sub-1"}, "SubagentStart", "", tree, nil))
	contains(t, "SubagentStart", said, "1: 調査", "explorer")
	lacks(t, "SubagentStart", said, "調べ", "planner")
	// コピーの research はレビュー不要。閉じても止めない（phases.yml の mr なら止まる）。
	write(t, filepath.Join(tree, "wip", "research", "summary.md"), "まとめ\n")
	w.git(tree, "add", "-A")
	w.git(tree, "commit", "--quiet", "-m", "summary")
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	if gate, _, _ := w.boardPhase(1); gate {
		t.Error("レビュー不要のフェーズが止まっている")
	}
}

func TestAChildIsApprovedFromTheCopyAfterPhasesYmlLostItsDefinition(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	write(t, w.phases, phasesWithoutResearch())
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/research/*"))
	w.commitParent()
	if r := w.approve(); r.Code != 0 || !w.isApproved("i0001-01-01") {
		t.Errorf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func TestAParentMovedByHandWithoutACopyIsBlockedAndSoIsItsChild(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.moveByHand(w.noCopy(planOf("research", "design")))
	w.moveByHand(childText("i0001-01-01", 1, "wip/research/*"), "i0001-01-01")
	tree := w.worktree("i0001-01-01", parentName)
	r := w.writeToTree(tree, "wip/research/note.md")
	if w.decision(r) != "deny" {
		t.Errorf("コピーの無い親の子の書き込みが止まらない: %s", r.Stdout)
	}
	contains(t, "reason", w.reason(r), "DENY_TICKET_BLOCKED", "phases:")
	lint := w.ccnavi("--lint", "--mode", "enable")
	if lint.Code == 0 {
		t.Error("lint が通った")
	}
	contains(t, "lint", lint.Stdout, "`phases:` がありません。")
}

func TestAChildMovedByHandWithPhasesIsBlocked(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.moveByHand(strings.Replace(childText("i0001-01-01", 1, "wip/research/*"), "phase: 1\n", "phase: 1\nphases: {}\n", 1), "i0001-01-01")
	tree := w.worktree("i0001-01-01", parentName)
	r := w.writeToTree(tree, "wip/research/note.md")
	if w.decision(r) != "deny" {
		t.Errorf("phases を持つ子の書き込みが止まらない: %s", r.Stdout)
	}
	contains(t, "reason", w.reason(r), "DENY_TICKET_BLOCKED", "親だけの欄")
}

func TestACopyMovedByHandIsTrustedAndLintSaysItDrifted(t *testing.T) {
	t.Parallel()
	// 手で動かした承認では、コピーが phases.yml と違ってもそのまま効く。`--lint` が warn。
	w := newWS(t)
	narrow := strings.Replace(defs(samples.Phases, "research", "design"), "    - wip/research/*\n", "    - wip/research/a/*\n", 1)
	w.moveByHand(withBlock(w.noCopy(planOf("research", "design")), narrow))
	w.moveByHand(childText("i0001-01-01", 1, "wip/research/*"), "i0001-01-01")
	tree := w.worktree("i0001-01-01", parentName)
	if inside := w.writeToTree(tree, "wip/research/a/x.md"); w.decision(inside) == "deny" {
		t.Errorf("コピーの中が止まった: %s", w.reason(inside))
	}
	beyond := w.writeToTree(tree, "wip/research/b/x.md")
	if w.decision(beyond) != "deny" {
		t.Errorf("コピーの外が止まらない: %s", beyond.Stdout)
	}
	contains(t, "reason", w.reason(beyond), "limit: phase type")
	lint := w.ccnavi("--lint", "--mode", "enable")
	contains(t, "lint", lint.Stdout, "今の phases.yml と違う", "進行中の親には反映されない")
}

func TestLintSaysALostPhasesYmlOnlyAsAWarningForAnApprovedParent(t *testing.T) {
	t.Parallel()
	// 承認済みの親はコピーを読むので、phases.yml が無くなっても --lint は warn だけで言う。
	w := newWS(t)
	w.family(planOf("research", "design"))
	remove(t, w.phases)
	lint := w.ccnavi("--lint", "--mode", "enable", "--json")
	body := struct {
		Problems []struct {
			Severity string `json:"severity"`
			Detail   string `json:"detail"`
		} `json:"problems"`
	}{}
	if err := jsonUnmarshal(lint.Stdout, &body); err != nil {
		t.Fatal(err)
	}
	found := 0
	for _, p := range body.Problems {
		if strings.Contains(p.Detail, parentName) {
			found++
			if p.Severity != "warn" {
				t.Errorf("%s は warn のはず: %s", p.Severity, p.Detail)
			}
		}
	}
	if found == 0 {
		t.Errorf("親を名指す苦情が無い: %s", lint.Stdout)
	}
}

func TestDeliverablesComeFromTheCopy(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
	// 承認のあとに phases.yml の成果物を変えても、閉じるときに見るのはコピーの成果物。
	write(t, w.phases, strings.Replace(samples.Phases, `["wip/research/summary.md"]`, `["wip/research/other.md"]`, 1))
	w.runChild("i0001-01-01", file{"wip/research/summary.md", "まとめ\n"})
	w.mustClose("i0001-01-01")
}

// ---- 4. 改版の規則

func TestAProposalThatOnlyChangesTheCopyIsARevision(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	changed := strings.Replace(samples.Phases, "title: 設計\n", "title: 設計と方針\n", 1)
	write(t, w.phases, changed)
	w.propose(parentName, samples.ParentText(parentName, planOf("research", "design"), samples.ParentOpts{Copy: changed}))
	w.commitParent()
	r := w.approve()
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	contains(t, "承認画面", r.Stdout, "親の改版", "■ phases: の差分")
	_, after, _ := strings.Cut(r.Stdout, "■ phases: の差分")
	diff, _, _ := strings.Cut(after, "■")
	contains(t, "差分", diff, "design", "title")
	title := frontOf(t, read(t, w.doing(parentName))).Map("phases").Map("design").Text("title")
	if title != "設計と方針" {
		t.Errorf("design の title = %q", title)
	}
}

func (w *ws) approveFirstChild() {
	w.t.Helper()
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
}

func TestAFixedNumberKeepsItsDefinitionEvenWhenPhasesYmlChanged(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.approveFirstChild()
	changed := strings.Replace(samples.Phases, `scope: ["wip/research/*"]`, `scope: ["wip/research/*", "x/*"]`, 1)
	write(t, w.phases, changed)
	// 子が承認された 1 番（research）の定義は承認済みチケットの値のまま、ほかは今の phases.yml。
	kept := withBlock(w.noCopy(planOf("research", "design", "acceptance")),
		joinDefs(defs(samples.Phases, "research", "design"), defs(changed, "acceptance")))
	if r := w.proposeParent(kept); r.Code != 0 {
		t.Errorf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func TestAFixedNumberCannotTakeTheNewDefinition(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.approveFirstChild()
	changed := strings.Replace(samples.Phases, `scope: ["wip/research/*"]`, `scope: ["wip/research/*", "x/*"]`, 1)
	write(t, w.phases, changed)
	r := w.proposeParent(samples.ParentText(parentName, planOf("research", "design", "acceptance"), samples.ParentOpts{Copy: changed}))
	if r.Code == 0 || !strings.Contains(r.Stderr, "子が承認された番号が使う定義") {
		t.Errorf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

// ---- 5. `--plan-order <親> --fill-phases`

func (w *ws) fill(name string) run.Result { return w.ccnavi("--plan-order", name, "--fill-phases") }

// fillIn は `--fill-phases` を cwd を変えて打つ（エージェントが自分のワークツリーから打つ形）。
func (w *ws) fillIn(cwd, name string) run.Result {
	return run.Ccnavi(w.t, []string{"--root", w.root, "--plan-order", name, "--fill-phases"}, run.Opts{Dir: cwd})
}

func TestFillPhasesWritesOnlyTheUsedDefinitionsAndKeepsOtherBytes(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	original := strings.Replace(w.noCopy(planOf("research", "design")), "title: 親\n", "# タイトルの前のコメント\ntitle: 親\n", 1)
	w.propose(parentName, original)
	r := w.fill(parentName)
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	text := read(t, w.todo(parentName))
	front := frontOf(t, text)
	keys, _ := front.Map("phases").StringKeys()
	equalStrings(t, "phases のキー", keys, []string{"research", "design"})
	contains(t, "提案", text, "# タイトルの前のコメント")
	if !yaml.Equal(front.Without("phases"), frontOf(t, original).Without("phases")) {
		t.Error("phases 以外の欄が変わった")
	}
	if got, want := strings.SplitN(text, "---\n", 3)[2], strings.SplitN(original, "---\n", 3)[2]; got != want {
		t.Error("本文が変わった")
	}
	w.commitParent()
	if a := w.approve(); a.Code != 0 {
		t.Errorf("書いた提案が承認されない: code %d\n%s%s", a.Code, a.Stdout, a.Stderr)
	}
}

func TestFillPhasesReplacesAStaleCopyAndDropsUnusedDefinitions(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	stale := joinDefs(strings.Replace(defs(samples.Phases, "design"), "review: true", "review: false", 1), defs(samples.Phases, "acceptance"))
	w.propose(parentName, withBlock(w.noCopy(planOf("research", "design")), stale))
	if r := w.fill(parentName); r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	front := frontOf(t, read(t, w.todo(parentName)))
	keys, _ := front.Map("phases").StringKeys()
	equalStrings(t, "phases のキー", keys, []string{"research", "design"})
	if review, _ := front.Map("phases").Map("design").Bool("review"); !review {
		t.Error("design の review が phases.yml の値（true）に戻らない")
	}
	// 2 度打っても変わらない。
	before := read(t, w.todo(parentName))
	if again := w.fill(parentName); again.Code != 0 || read(t, w.todo(parentName)) != before {
		t.Errorf("2 度目で変わった: code %d\n%s", again.Code, again.Stderr)
	}
}

func TestFillPhasesOnARevisionKeepsTheDefinitionsOfFixedNumbers(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.approveFirstChild()
	changed := strings.Replace(strings.Replace(samples.Phases, `scope: ["wip/research/*"]`, `scope: ["x/*"]`, 1), "title: 設計\n", "title: 設計と方針\n", 1)
	write(t, w.phases, changed)
	w.propose(parentName, w.noCopy(planOf("research", "design", "acceptance")))
	if r := w.fill(parentName); r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	phases := frontOf(t, read(t, w.todo(parentName))).Map("phases")
	scope, _ := yaml.Strings(phases.Map("research").Get("scope"))
	equalStrings(t, "research の scope", scope, []string{"wip/research/*"})
	if title := phases.Map("design").Text("title"); title != "設計と方針" {
		t.Errorf("design の title = %q", title)
	}
	w.commitParent()
	if a := w.approve(); a.Code != 0 {
		t.Errorf("code %d\n%s%s", a.Code, a.Stdout, a.Stderr)
	}
}

func TestFillPhasesWritesNothingWhenOtherFieldsWouldChange(t *testing.T) {
	t.Parallel()
	// `phases:` の値にアンカーがあり、ほかの欄が別名で指していれば書かない。
	w := newWS(t)
	text := strings.Replace(w.noCopy(planOf("research")), "plan:", "phases: &copied {}\nrationale_extra: *copied\nplan:", 1)
	w.propose(parentName, text)
	r := w.fill(parentName)
	if r.Code == 0 || !strings.Contains(r.Stderr, "何も反映しません") {
		t.Errorf("code %d\n%s", r.Code, r.Stderr)
	}
	if read(t, w.todo(parentName)) != text {
		t.Error("止めたのに提案を書き換えた")
	}
}

func TestFillPhasesWritesOnlyTheCopyInTheCallersTree(t *testing.T) {
	t.Parallel()
	// 同じ提案が 2 つのツリーにあって本物とするツリーが決まらなければ、打ったツリーの側だけを書く。
	w := newWS(t)
	text := samples.ParentText("i0002", planOf("research"), samples.ParentOpts{NoCopy: true})
	var paths []string
	for _, name := range []string{"tree-a", "tree-b"} {
		tree := w.worktree(name, "main")
		paths = append(paths, write(t, filepath.Join(tree, "wip", "proposals", "todo", "i0002.md"), text))
	}
	refused := w.fill("i0002")
	if refused.Code == 0 || !strings.Contains(refused.Stderr, "複数の場所") {
		t.Errorf("どちらのツリーでもない所から打って止まらない: code %d\n%s", refused.Code, refused.Stderr)
	}
	if read(t, paths[0]) != text || read(t, paths[1]) != text {
		t.Error("止めたのに書いた")
	}
	done := w.fillIn(filepath.Dir(filepath.Dir(filepath.Dir(paths[0]))), "i0002")
	if done.Code != 0 {
		t.Fatalf("code %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	contains(t, "打ったツリーの提案", read(t, paths[0]), "phases:")
	if read(t, paths[1]) != text {
		t.Error("打っていないツリーの提案を書いた")
	}
}

func TestFillPhasesNamesAColumnZeroCommentInsideTheValue(t *testing.T) {
	t.Parallel()
	// `phases:` の値の中に行頭のコメントがあれば、値の範囲が決まらないので書かずにそう言う。
	w := newWS(t)
	text := strings.Replace(w.noCopy(planOf("research")), "plan:", "phases:\n  research:\n# 行頭のコメント\n    kind: work\nplan:", 1)
	w.propose(parentName, text)
	r := w.fill(parentName)
	if r.Code == 0 || !strings.Contains(r.Stderr, "行頭") {
		t.Errorf("code %d\n%s", r.Code, r.Stderr)
	}
	if read(t, w.todo(parentName)) != text {
		t.Error("止めたのに提案を書き換えた")
	}
}

func TestFillPhasesRefusesAPlanItemMissingFromPhasesYml(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	text := w.noCopy(planOf("research", "nope"))
	w.propose(parentName, text)
	r := w.fill(parentName)
	if r.Code == 0 || !strings.Contains(r.Stderr, "nope") {
		t.Errorf("code %d\n%s", r.Code, r.Stderr)
	}
	if read(t, w.todo(parentName)) != text {
		t.Error("止めたのに提案を書き換えた")
	}
}

func TestPlanOrderNeedsAKnownProposalAndFillPhases(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.propose(parentName, w.noCopy(planOf("research")))
	cases := []struct {
		name string
		r    run.Result
		want string
	}{
		{"--fill-phases が無い", w.ccnavi("--plan-order", parentName), "--fill-phases"},
		{"--plan-order が無い", w.ccnavi("--fill-phases"), "--plan-order"},
		{"知らない識別子", w.fill("i0002"), "i0002"},
		{"user agree と一緒", w.ccnavi("user", "agree", "--plan-order", parentName, "--fill-phases"), "--agree"},
	}
	for _, c := range cases {
		if c.r.Code == 0 || !strings.Contains(c.r.Stderr, c.want) {
			t.Errorf("%s: code %d, stderr に %q が無い:\n%s", c.name, c.r.Code, c.want, c.r.Stderr)
		}
	}
	lacks(t, "提案", read(t, w.todo(parentName)), "phases:")
}
