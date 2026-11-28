package phases

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 親の finish のテンプレート化の案内、親の `ticket start` と計画
// 、手で動かした承認に仕込まれた着手の欄、
// 親の改版で範囲を変える規則。

// ---- テンプレート化の案内

const noticeMark = "テンプレートにするなら"
const templateSame = "name: same\ndescription: 設計だけ\nwhen: 設計をするとき\nplan: [design]\n"
const templateOther = "name: other\ndescription: 設計を 2 回\nwhen: 設計を重ねるとき\nplan: [design, design]\n"

func noticeLines(r run.Result) []string {
	var out []string
	for _, l := range strings.Split(r.Stdout, "\n") {
		if strings.Contains(l, noticeMark) {
			out = append(out, l)
		}
	}
	return out
}

func (w *ws) noticeDraft(name, to string) string {
	layer := "project"
	if to == "common" {
		layer = "common"
	}
	return filepath.Join(w.parentTree, "wip", "handoff", ".ccnavi", "config", layer, "templates", name+".yml")
}

func (w *ws) commonTemplate(name, text string) string {
	return ticketkit.Write(w.t, filepath.Join(w.root, ".ccnavi", "config", "common", "templates", name+".yml"), text)
}

func TestAClosedParentWithAPlanSaysHowToMakeATemplateInOneLine(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	_, closed := w.closedFamilyWith(nil)
	lines := noticeLines(closed)
	if len(lines) != 1 {
		t.Fatalf("案内が %d 行\n%s", len(lines), closed.Stdout)
	}
	contains(t, "案内", lines[0], "`ready` の前に", "ccnavi-launcher.sh --templates --draft i0001", "退避されて拾えない",
		// 下書きを残したままでは ready が止まるので、そのことも言う。
		"ccnavi-user-handoff.sh", "削除してから ready")
	// 案内は、ready の案内の後ろに足す。
	if strings.Index(closed.Stdout, noticeMark) < strings.Index(closed.Stdout, "logs/archive/") {
		t.Error("案内が ready の案内より前にある")
	}
}

func TestTheNoticeIsSilencedOrKept(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		before func(w *ws)
		notice bool
	}{
		{"親の識別子の下書きがあれば出さない", func(w *ws) { ticketkit.Write(w.t, w.noticeDraft("i0001", "project"), "name: i0001\nplan: [design]\n") }, false},
		{"共通向けの下書きでも出さない", func(w *ws) { ticketkit.Write(w.t, w.noticeDraft("i0001", "common"), "name: i0001\nplan: [design]\n") }, false},
		{"別の名前の下書きでは出す", func(w *ws) { ticketkit.Write(w.t, w.noticeDraft("other", "project"), "name: other\n") }, true},
		{"同じ計画の形のテンプレートが取り込み済みなら出さない", func(w *ws) { w.commonTemplate("same", templateSame) }, false},
		{"別の計画のテンプレートでは出す", func(w *ws) { w.commonTemplate("other", templateOther) }, true},
		{"プロジェクトの設定のテンプレートも数える", func(w *ws) {
			ticketkit.Write(w.t, filepath.Join(w.root, ".ccnavi", "config", "project", "templates", "same.yml"), templateSame)
		}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t)
			_, closed := w.closedFamilyWith(func() { c.before(w) })
			want := 0
			if c.notice {
				want = 1
			}
			if got := len(noticeLines(closed)); got != want {
				t.Errorf("案内が %d 行（出すべき: %v）\n%s", got, c.notice, closed.Stdout)
			}
			if !c.notice {
				contains(t, "stdout", closed.Stdout, "logs/archive/")
			}
		})
	}
}

func TestAChildFinishDoesNotSayTheTemplateNotice(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	if got := noticeLines(w.mustClose("i0001-01-01")); len(got) != 0 {
		t.Errorf("子の finish が案内を出した: %v", got)
	}
}

func TestTheNoticeNamesACommandThatWorks(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	_, closed := w.closedFamilyWith(nil)
	if len(noticeLines(closed)) != 1 {
		t.Fatal("案内が出ない")
	}
	w.mustOK(w.bare(nil, "--templates", "--draft", parentName), "--templates --draft")
	if !ticketkit.Exists(w.noticeDraft("i0001", "project")) {
		t.Error("案内のとおりに打っても下書きができない")
	}
}

// ---- 親の ticket start と計画

const noChildHead = "子のない（子の提案もありません）フェーズがある"

func (w *ws) proposeParentWithPlan(plan ...string) {
	w.propose(parentName, samples.ParentText(parentName, planOf(plan...), samples.ParentOpts{Feedback: noFeedback}))
}

func TestAParentWithAPlanAndNoChildrenStartsAndIsTold(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.proposeParentWithPlan("research", "design")
	w.commitParent()
	w.mustApprove()
	started := w.mustOK(w.ccnavi("ticket", "start", parentName), "start")
	contains(t, "stdout", started.Stdout, noChildHead+": 1（調査）, 2（設計）", "`finish` で")
	// 次の一手は、子の提案を書いて approval-request で送ること（識別子の例を埋めて言う）
	var nexts []string
	for _, l := range strings.Split(started.Stdout, "\n") {
		if strings.HasPrefix(l, "次: ") {
			nexts = append(nexts, l)
		}
	}
	if len(nexts) != 1 {
		t.Fatalf("次: が %d 本\n%s", len(nexts), started.Stdout)
	}
	contains(t, "次", nexts[0], "子の提案（例: 'i0001-01-01'）", "approval-request i0001")
	if started.Stderr != "" {
		t.Errorf("stderr = %q", started.Stderr)
	}
}

func TestFinishStillStopsAParentWhosePhasesHaveNoChildren(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.proposeParentWithPlan("research", "design")
	w.commitParent()
	w.mustApprove()
	w.mustOK(w.ccnavi("ticket", "start", parentName), "start")
	done := w.ccnavi("ticket", "finish", parentName)
	if done.Code != 1 || !strings.Contains(done.Stderr, "終わっていないフェーズがある") {
		t.Errorf("code %d\n%s", done.Code, done.Stderr)
	}
}

func TestTheStartNoticeListsOnlyPhasesWithoutAChildOrAProposal(t *testing.T) {
	t.Parallel()
	// 数える子は承認済みの子と todo/ の提案の両方。
	cases := []struct {
		name       string
		approved   bool // 1 番目の子を承認する
		proposed   bool // 2 番目の子を提案だけ置く
		wantHead   string
		wantAbsent []string
	}{
		{"承認済みの子はそのフェーズを一覧から外す", true, false, noChildHead + ": 2（設計）", []string{"1（調査）"}},
		{"todo/ の提案の子もそのフェーズを一覧から外す", false, true, noChildHead + ": 1（調査）", []string{"2（設計）"}},
		{"全部のフェーズに子があれば案内しない", true, true, "", []string{noChildHead}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t)
			w.proposeParentWithPlan("research", "design")
			if c.approved {
				w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/research/*"))
			}
			w.commitParent()
			w.mustApprove()
			if c.proposed {
				w.propose("i0001-02-01", childText("i0001-02-01", 2, "wip/design/*"))
				w.commitParent("approval-request 02")
			}
			started := w.mustOK(w.ccnavi("ticket", "start", parentName), "start")
			if c.wantHead != "" {
				contains(t, "stdout", started.Stdout, c.wantHead)
			}
			lacks(t, "stdout", started.Stdout, c.wantAbsent...)
		})
	}
}

func TestAChildStillNeedsItsParentToHaveStarted(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.proposeParentWithPlan("research", "design")
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
	w.worktree("i0001-01-01", parentName)
	refused := w.ccnavi("ticket", "start", "i0001-01-01")
	if refused.Code != 1 || !strings.Contains(refused.Stderr, "子より先に親を開始してください") {
		t.Errorf("code %d\n%s", refused.Code, refused.Stderr)
	}
	w.mustOK(w.ccnavi("ticket", "start", parentName), "start parent")
	started := w.mustOK(w.ccnavi("ticket", "start", "i0001-01-01"), "start child")
	// 子の着手の次の一手は、サブエージェントへの実装の委任と、終わったときの finish
	var nexts []string
	for _, l := range strings.Split(started.Stdout, "\n") {
		if strings.HasPrefix(l, "次: ") {
			nexts = append(nexts, l)
		}
	}
	if len(nexts) != 1 {
		t.Fatalf("次: が %d 本\n%s", len(nexts), started.Stdout)
	}
	contains(t, "次", nexts[0], "サブエージェント", "finish i0001-01-01")
}

// ---- 手で動かした承認に仕込まれた着手の欄

func TestLintWarnsWhenTheBaseShaIsOffTheWorktreeHistory(t *testing.T) {
	t.Parallel()
	// `start` では止めない（`base_sha` だけなら判定が blocked で止め、`start` は基準点を HEAD で書き直す）。判定にも入れない。
	w := newWS(t)
	w.family(planOf("research"))
	w.startParent()
	w.commitParent("start")
	said := func() []map[string]any {
		var out []map[string]any
		for _, p := range ticketkit.ParseJSON(t, w.ccnavi("--lint", "--json").Stdout)["problems"].([]any) {
			m := p.(map[string]any)
			if strings.HasPrefix(m["detail"].(string), parentName+": ") && strings.Contains(m["detail"].(string), "HEAD の祖先でない") {
				out = append(out, m)
			}
		}
		return out
	}
	if got := said(); len(got) != 0 {
		t.Fatalf("正しい基準点に苦情: %v", got)
	}
	other := w.worktree("elsewhere", "main")
	ticketkit.Write(t, filepath.Join(other, "x.txt"), "x\n")
	w.git(other, "add", "-A")
	w.git(other, "commit", "--quiet", "-m", "elsewhere")
	stray := strings.TrimSpace(w.git(other, "rev-parse", "HEAD"))
	var lines []string
	for _, line := range strings.Split(ticketkit.Read(t, w.doing(parentName)), "\n") {
		if strings.HasPrefix(line, "base_sha:") {
			line = `base_sha: "` + stray + `"`
		}
		lines = append(lines, line)
	}
	ticketkit.Write(t, w.doing(parentName), strings.Join(lines, "\n"))
	w.commitParent("rewritten base")
	got := said()
	if len(got) != 1 || got[0]["severity"] != "warn" { // 判定には入れないので warn
		t.Errorf("苦情 = %v", got)
	}
}

// ---- 親の改版で範囲を変える

var scopeWider = []string{"src/*", "wip/*", "tests/*", "docs/*"}

func (w *ws) reviseScope(allow []string) run.Result {
	w.t.Helper()
	w.propose(parentName, w.parent(planOf("research", "design"), samples.ParentOpts{Allow: allow}))
	w.commitParent("revise")
	return w.approve()
}

func TestAScopeOnlyRevisionReplacesTheScopeAndShowsTheDiff(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	r := w.reviseScope(scopeWider)
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	contains(t, "承認画面", r.Stdout, "親の改版", "■ 範囲の差分", "allow 追加: docs/*")
	contains(t, "承認済みチケット", ticketkit.Read(t, w.doing(parentName)), "glob: docs/*")
	if ticketkit.Exists(w.todo(parentName)) {
		t.Error("改版の提案が todo/ に残っている")
	}
}

func TestNarrowingShowsTheRemovedEntry(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	r := w.reviseScope([]string{"src/*", "wip/*"})
	if r.Code != 0 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	contains(t, "承認画面", r.Stdout, "allow 削除: tests/*")
}

func (w *ws) approveResearchChild() {
	w.t.Helper()
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent("child")
	w.mustApprove()
}

func TestAStaleCopyInAChildWorktreeDoesNotCountAfterCancel(t *testing.T) {
	t.Parallel()
	// 取り消したあとも子のワークツリーには切り出した時点の doing/ のコピーが残る。
	w := newWS(t)
	w.approveResearchChild()
	tree := w.runChild("i0001-01-01")
	if !ticketkit.Exists(filepath.Join(tree, ".ccnavi", "approved", "doing", "i0001-01-01.md")) {
		t.Fatal("子のワークツリーに doing/ のコピーが無い")
	}
	cancelled := w.mustOK(w.ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "範囲を広げる"), "cancel")
	contains(t, "cancel", cancelled.Stdout, "次: ") // 子を個別に取り消したときは、次の一手（親の status）を言う
	w.commitParent("cancel")
	if r := w.reviseScope(scopeWider); r.Code != 0 {
		t.Errorf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func TestAChildInProgressStopsTheRevisionUntilItIsCancelled(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.approveResearchChild()
	stopped := w.reviseScope(scopeWider)
	if stopped.Code == 0 {
		t.Fatal("作業中の子がいるのに範囲を変える改版が通った")
	}
	contains(t, "出力", stopped.Stdout+stopped.Stderr, "i0001-01-01")
	lacks(t, "承認済みチケット", ticketkit.Read(t, w.doing(parentName)), "docs/*")
	w.mustOK(w.ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "範囲を広げる"), "cancel")
	w.commitParent("cancel")
	w.mustApprove()
	contains(t, "承認済みチケット", ticketkit.Read(t, w.doing(parentName)), "docs/*")
}
