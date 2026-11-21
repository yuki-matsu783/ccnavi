package phases

import (
	"encoding/json"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 全体計画の順序を計画のステップの `after` で決めることの受入テスト（tests/ticket/test_phases_dag.py）。
// 先行関係のファイルは無い。承認済みチケットの計画から都度計算し、手で動かした承認も `after` で読む。
// フェーズ定義の読み方・`after` の読み方・先行関係の計算（見本の表 tests/fixtures/plan-waits.json）・計画の検査・
// 改版の見つけ方と錠・次の案内は go/internal/tickets の config/phases・model/ticket・proposal/candidates・phase が見る。

// dagDefs は順序の欄を持たない定義。順序は計画のステップの `after` が決める。
const dagDefs = `
version: 1
phases:
  design: {title: 設計, review: false, scope: ["wip/*"]}
  acceptance: {title: 受入, review: false, scope: ["tests/*"]}
  implement: {title: 実装, review: false, scope: ["src/*"]}
  docs: {title: 文書, review: true, scope: inherit}
  extra: {title: 追加, review: false, scope: ["wip/*"]}
  fixup: {kind: feedback, title: 対応, review: true, scope: inherit}
`

// dagPlan は設計 → 受入と実装が並行 → 文書が両方を受ける（終端）。
func dagPlan() []samples.Step {
	return []samples.Step{{Type: "design"}, after("acceptance", 1), after("implement", 1), after("docs", 2, 3)}
}

func newDagWS(t *testing.T) *ws {
	w := newWS(t)
	w.setPhases(dagDefs)
	return w
}

// waits は `--explain` が言う、フェーズごとの先行（番号 → 先行の番号の並び）。
func (w *ws) waits() map[int][]int {
	w.t.Helper()
	out := w.ccnavi("--explain").Stdout
	found := map[int][]int{}
	re := regexp.MustCompile(`(?m)^  ` + parentName + ` フェーズ (\d+)（[^）]*）: (.*)$`)
	for _, m := range re.FindAllStringSubmatch(out, -1) {
		n, _ := strconv.Atoi(m[1])
		tail := m[2]
		if i := strings.LastIndex(tail, " / "); i >= 0 {
			tail = tail[i+len(" / "):]
		}
		tail = strings.TrimSpace(tail)
		list := []int{}
		if tail != "先行フェーズなし" {
			for _, p := range strings.Split(strings.TrimPrefix(tail, "先行 "), ", ") {
				k, _ := strconv.Atoi(p)
				list = append(list, k)
			}
		}
		found[n] = list
	}
	return found
}

func (w *ws) wantWaits(want map[int][]int) {
	w.t.Helper()
	if got := w.waits(); !reflect.DeepEqual(got, want) {
		w.t.Errorf("先行 = %v, want %v", got, want)
	}
}

// lintSays は `--lint` の、その識別子を名指す error の文。
func (w *ws) lintSays(name string) string {
	w.t.Helper()
	var body struct {
		Problems []struct {
			Severity string `json:"severity"`
			Detail   string `json:"detail"`
			Where    string `json:"where"`
		} `json:"problems"`
	}
	if err := json.Unmarshal([]byte(w.ccnavi("--lint", "--json").Stdout), &body); err != nil {
		w.t.Fatal(err)
	}
	var said []string
	for _, p := range body.Problems {
		if p.Severity == "error" && (strings.Contains(p.Detail, name) || strings.Contains(p.Where, name)) {
			said = append(said, p.Detail)
		}
	}
	return strings.Join(said, "\n")
}

func (w *ws) closeFirstPhase() {
	w.t.Helper()
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/a/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/a/x.md", "x\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.hook("PostToolUse", "Bash", w.parentTree, input{"command": "ls"})
}

func (w *ws) proposeBranches() {
	w.t.Helper()
	w.propose("i0001-02-02", childReview("i0001-02-02", 2, samples.Off, "tests/a/*"))
	w.propose("i0001-03-03", childReview("i0001-03-03", 3, samples.Off, "src/a/*"))
	w.commitParent("approval-request 02 03")
}

// moveByHand は提案を通さずに承認済みの置き場へ置く（手で動かした承認）。
func (w *ws) moveByHand(text string, name ...string) {
	w.t.Helper()
	at := parentName
	if len(name) > 0 {
		at = name[0]
	}
	ticketkit.Write(w.t, w.doing(at), text)
	w.commitParent("moved by hand")
}

func (w *ws) front(name string) []step {
	return stepsOf(frontOf(w.t, ticketkit.Read(w.t, w.doing(name))), "plan")
}

func TestParallelBranchesStartTogetherAndTheJoinWaits(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.family(dagPlan())
	w.wantWaits(map[int][]int{1: {}, 2: {1}, 3: {1}, 4: {1, 2, 3}})
	w.closeFirstPhase()
	w.proposeBranches()
	r := w.approve()
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	if !w.isApproved("i0001-02-02") || !w.isApproved("i0001-03-03") {
		t.Error("並行の枝が一緒に承認されない")
	}
	// 合流点は、両方の枝が閉じるまで承認しない。
	w.propose("i0001-04-04", childText("i0001-04-04", 4, "wip/d/*"))
	w.commitParent("approval-request 04")
	refused := w.approve()
	if w.isApproved("i0001-04-04") {
		t.Error("枝が閉じる前に合流点が承認された")
	}
	contains(t, "stderr", refused.Stderr, "閉じるまで承認しない")
}

func TestAChainStillWaitsForEveryEarlierPhase(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.family(planOf("design", "acceptance", "implement", "docs"))
	w.closeFirstPhase()
	w.proposeBranches()
	r := w.approve()
	if !w.isApproved("i0001-02-02") || w.isApproved("i0001-03-03") {
		t.Errorf("一直線の計画で 2 だけが通るはず: 2=%v 3=%v", w.isApproved("i0001-02-02"), w.isApproved("i0001-03-03"))
	}
	contains(t, "stderr", r.Stderr, "閉じるまで承認しない")
}

func TestAPlanWithoutAfterIsNotApproved(t *testing.T) {
	t.Parallel()
	// `after` を 1 つも書かない 2 ステップ以上の計画は、終端の検査で必ず落ちる。
	w := newDagWS(t)
	w.propose(parentName, w.parent([]samples.Step{{Type: "design"}, after("docs")}, samples.ParentOpts{}))
	w.commitParent()
	r := w.approve()
	if r.Code == 0 || !strings.Contains(r.Stderr, "先行していない") {
		t.Errorf("code %d\n%s", r.Code, r.Stderr)
	}
	if w.isApproved(parentName) {
		t.Error("終端が 2 つの計画が承認された")
	}
}

func TestTheApprovalScreenListsEveryNumber(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.propose(parentName, w.parent(dagPlan(), samples.ParentOpts{}))
	w.commitParent()
	r := w.ccnaviIn("n\n", "user", "agree")
	contains(t, "承認画面", r.Stdout, "■ フェーズ間の依存関係", "1: design — 先行なし", "3: implement — 先行 1", "4: docs — 先行 1, 2, 3")
}

func TestNoWorkflowFileIsWrittenAndTheTicketIsMovedAsIs(t *testing.T) {
	t.Parallel()
	// 先行関係のファイル（workflow.yml）は作らない。承認は提案のバイト列のまま動かし、先行は計画から都度計算する。
	w := newDagWS(t)
	w.propose(parentName, w.parent(dagPlan(), samples.ParentOpts{}))
	w.commitParent()
	before := ticketkit.Read(t, w.todo(parentName))
	w.mustApprove()
	if got := ticketkit.Read(t, w.doing(parentName)); got != before {
		t.Errorf("承認が提案の中身を変えた:\n%s", got)
	}
	if ticketkit.Exists(w.approved + "/phases/i0001/workflow.yml") {
		t.Error("workflow.yml が書かれた")
	}
	if got := w.waits()[4]; !reflect.DeepEqual(got, []int{1, 2, 3}) {
		t.Errorf("4 の先行 = %v", got)
	}
}

func TestAWorkflowFieldIsRefusedInAProposalAndBlocksInAnApprovedCopy(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	text := strings.Replace(w.parent(dagPlan(), samples.ParentOpts{}), "title: 親", "workflow: {order: dag, waits: {1: [], 2: [], 3: [], 4: []}}\ntitle: 親", 1)
	w.propose(parentName, text)
	w.commitParent()
	r := w.approve()
	if r.Code == 0 || !strings.Contains(r.Stderr, "の欄は提案に書かない") {
		t.Errorf("提案の workflow を断らない: code %d\n%s", r.Code, r.Stderr)
	}
	// 手で動かした承認に書かれていても読まず、lint が止める。
	remove(t, w.todo(parentName))
	w.moveByHand(text)
	w.wantWaits(map[int][]int{1: {}, 2: {1}, 3: {1}, 4: {1, 2, 3}})
	contains(t, "lint", w.lintSays(parentName), "workflow")
}

func TestAHandMovedParentIsReadByItsAfter(t *testing.T) {
	t.Parallel()
	// 手で動かした承認も、計画の `after` を信じて並行で読む。`after` を書いた計画で内容の検査が error を出さない。
	w := newDagWS(t)
	w.moveByHand(w.parent(dagPlan(), samples.ParentOpts{}))
	if said := w.lintSays(parentName); said != "" {
		t.Errorf("lint が after を咎めた: %s", said)
	}
	w.closeFirstPhase()
	w.proposeBranches()
	if r := w.approve(); r.Code != 0 || !w.isApproved("i0001-03-03") {
		t.Errorf("手で動かした親で並行の枝が通らない: code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func TestAHandMovedBrokenPlanStopsTheParentAndItsChildren(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.moveByHand(w.parent([]samples.Step{{Type: "design"}, after("extra"), {Type: "docs"}}, samples.ParentOpts{}))
	contains(t, "lint", w.lintSays(parentName), "先行していない")
	// 親の範囲はヒットせず、書き込みは止まる。
	denied := w.hook("PreToolUse", "Write", w.parentTree, input{"file_path": w.parentTree + "/src/x.py", "content": "x"})
	contains(t, "hook", denied.Stdout, "deny")
	// 子の承認も止まる。
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/a/*"))
	w.commitParent()
	refused := w.approve()
	if w.isApproved("i0001-01-01") {
		t.Error("壊れた計画の親の子が承認された")
	}
	contains(t, "stderr", refused.Stderr, "計画が不正")
	// 子を手で動かしても、判定と着手で止まる。
	w.moveByHand(childReview("i0001-01-01", 1, samples.Off, "wip/a/*"), "i0001-01-01")
	contains(t, "lint", w.lintSays("i0001-01-01"), "計画が不正")
	w.ccnavi("ticket", "start", parentName)
	if started := w.ccnavi("ticket", "start", "i0001-01-01"); started.Code == 0 {
		t.Error("壊れた計画の子が着手できた")
	}
}

func TestAHandMovedParentWithABrokenAfterStillEntersTheIndex(t *testing.T) {
	t.Parallel()
	// 形の誤った `after` でも承認済みチケットは読め、判定は理由付きで止める。
	w := newDagWS(t)
	w.moveByHand(w.parent([]samples.Step{{Type: "design"}, after("docs", 2)}, samples.ParentOpts{}))
	contains(t, "lint", w.lintSays(parentName), "after")
	contains(t, "lint", w.ccnavi("--lint").Stdout, "after")
}

func TestARevisionAddingAnItemPassesThePerNumberLock(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.family(dagPlan())
	w.closeFirstPhase()
	// 3（実装）だけに子を承認する。2（受入）は始めない。
	w.propose("i0001-03-03", childReview("i0001-03-03", 3, samples.Off, "src/a/*"))
	w.commitParent("approval-request 03")
	w.mustApprove()
	if !w.isApproved("i0001-03-03") {
		t.Fatal("3 の子が承認されない")
	}
	// 子の無い 2 を別の定義に替え、文書の前に 1 ステップ足す。
	revised := []samples.Step{{Type: "design"}, after("extra", 1), after("implement", 1), after("acceptance", 1), after("docs", 2, 3, 4)}
	w.propose(parentName, w.parent(revised, samples.ParentOpts{}))
	w.commitParent("revise")
	r := w.approve()
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	plan := w.front(parentName)
	if plan[1].Type != "extra" || !reflect.DeepEqual(plan[4].After, []int{2, 3, 4}) {
		t.Errorf("改版が反映されない: %+v", plan)
	}
}

func TestARevisionCannotChangeAFixedNumber(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.family(dagPlan())
	w.closeFirstPhase()
	revised := []samples.Step{{Type: "extra"}, after("acceptance", 1), after("implement", 1), after("docs", 2, 3)}
	w.propose(parentName, w.parent(revised, samples.ParentOpts{}))
	w.commitParent("revise")
	r := w.approve()
	if r.Code == 0 || !strings.Contains(r.Stderr, "子が承認されている") {
		t.Errorf("子が承認された番号を変える改版が通る: code %d\n%s", r.Code, r.Stderr)
	}
}

func TestCopyingTheWaitsIntoAfterIsNotARevision(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.family(dagPlan())
	copied := dagPlan()
	copied[3] = after("docs", 1, 2, 3)
	w.propose(parentName, w.parent(copied, samples.ParentOpts{}))
	w.commitParent("copied")
	preview := w.ccnavi("--agree", "--preview", "--json")
	var body struct {
		Tickets []struct {
			Ticket string `json:"ticket"`
		} `json:"tickets"`
	}
	if err := json.Unmarshal([]byte(preview.Stdout), &body); err != nil {
		t.Fatal(err)
	}
	for _, tk := range body.Tickets {
		if tk.Ticket == parentName {
			t.Error("先行の写しだけの違いが改版になった")
		}
	}
}

func TestAPlanChangeAfterTheFeedbackPlanIsRefused(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.familyWith(planOf("design"), samples.ParentOpts{Feedback: noFeedback})
	w.propose(parentName, w.parent(planOf("design", "docs"), samples.ParentOpts{Feedback: noFeedback}))
	w.commitParent("revise")
	r := w.approve()
	if r.Code == 0 || !strings.Contains(r.Stderr, "フィードバック計画") {
		t.Errorf("フィードバック計画の後の計画の変更が通る: code %d\n%s", r.Code, r.Stderr)
	}
}

func TestExplainNamesEveryBranchAtWork(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.family(dagPlan())
	w.closeFirstPhase()
	w.proposeBranches()
	w.mustApprove()
	body := ticketkit.ParseJSON(t, w.ccnavi("--explain", "--json").Stdout)
	for _, p := range body["parents"].([]any) {
		owner := p.(map[string]any)
		if owner["ticket"] == parentName {
			contains(t, "stage", owner["stage"].(string), "2（受入）", "3（実装）")
			return
		}
	}
	t.Error("親が --explain に無い")
}

func TestTheContentCheckDoesNotTripOnAfter(t *testing.T) {
	t.Parallel()
	w := newDagWS(t)
	w.family(dagPlan())
	if said := w.lintSays(parentName); said != "" {
		t.Errorf("lint が after を咎めた: %s", said)
	}
}
