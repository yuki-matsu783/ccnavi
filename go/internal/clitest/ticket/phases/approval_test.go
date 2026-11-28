package phases

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 全体計画の承認・順序・成果物・レビューの要否・延期。
// 順序の止め方は承認で見る。先行のない番号は並行する。

func TestAChildCannotBeApprovedAgainstAnOldPlanWhenTheParentHasAPendingRevision(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("acceptance", "implement"))
	// 親を改版してフェーズ 1 を設計にする。子はまだ元の計画（受入テスト作成）の範囲で出す
	w.propose(parentName, w.parent(planOf("design", "acceptance", "implement"), samples.ParentOpts{Copy: samples.Phases}))
	w.propose("i0001-01-02", childText("i0001-01-02", 1, "tests/x*"))
	w.commitParent()
	// 絞らないときは改版後の計画で検証される。定義の超過は承認を拒まず、承認画面に出る。n で何も適用しない
	whole := w.ccnaviIn("n\n", "user", "agree")
	contains(t, "承認画面", whole.Stdout, "編集対象としているが", "超えている")
	if w.isApproved("i0001-01-02") {
		t.Fatal("n と答えたのに承認済みチケットが置かれた")
	}
	// 改版を外して子だけ並べても、元の計画で通してはいけない
	only := w.ccnaviIn("y\n", "user", "agree", "i0001-01-02")
	if only.Code != 1 || !strings.Contains(only.Stderr, "改版") {
		t.Errorf("子だけの承認が改版を理由に止まらない: code %d\n%s%s", only.Code, only.Stdout, only.Stderr)
	}
	if w.isApproved("i0001-01-02") {
		t.Error("元の計画で子が承認された")
	}
}

func TestAChildOfARejectedParentIsNotApproved(t *testing.T) {
	t.Parallel()
	// 親が外れたら（置き場に無いプロジェクト）、その子も親が承認されていないので外れる。
	// 外れた親を対応表に残すと、子だけ承認済みチケットになる。
	w := newWS(t)
	parent := strings.Replace(w.parent(planOf("design"), samples.ParentOpts{}), "plan:", "project: nope\nplan:", 1)
	w.propose(parentName, parent)
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	r := w.approve()
	if r.Code != 1 {
		t.Errorf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	contains(t, "stderr", r.Stderr, "i0001 は承認の対象にしない", "親 i0001 が承認されていない")
	if w.isApproved(parentName) || w.isApproved("i0001-01-01") {
		t.Error("外れた親か子が承認済みチケットになった")
	}
}

func TestLintReportsABrokenPhasesFile(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	ticketkit.Write(t, w.phases, "version: 1\nphases:\n  a: {title: X, kind: work}\n  b: {title: X, kind: work}\n")
	r := w.ccnavi("--lint", "--mode", "enable")
	contains(t, "lint", r.Stdout, "表示名")
	if r.Code == 0 {
		t.Error("表示名の重なりを咎めても終了コードが 0")
	}
}

func TestAPlanIsApprovedAndCopied(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family([]samples.Step{samples.Step{Type: "research"}, samples.DeferStep("design"), {Type: "acceptance"}, {Type: "implement"}})
	text := ticketkit.Read(t, w.doing(parentName))
	contains(t, "承認済みチケット", text, "plan:", "research", "deferred: true")
	r := w.ccnavi("--explain")
	contains(t, "--explain", r.Stdout, "局面: 着手待ち・フェーズ1 調査", "フェーズ 2（設計）: 未計画", "レビューは 3 と一緒に")
}

func TestAPlanThatBreaksTheRulesIsRefused(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	cases := []struct {
		name string
		plan []samples.Step
	}{
		{"kind", planOf("implement-feedback")},
		{"defer-none", []samples.Step{samples.DeferStep("research"), {Type: "design"}}},
		{"defer-last", []samples.Step{{Type: "design"}, samples.DeferStep("implement")}},
		{"unknown", planOf("nope")},
	}
	for _, c := range cases {
		if err := os.RemoveAll(w.approved); err != nil {
			t.Fatal(err)
		}
		w.propose(parentName, samples.ParentText(parentName, c.plan, samples.ParentOpts{}))
		r := w.approve()
		if r.Code == 0 {
			t.Errorf("%s: 規則を破る計画が承認された", c.name)
		}
		if w.isApproved(parentName) {
			t.Errorf("%s: 承認済みチケットが置かれた", c.name)
		}
	}
}

func TestAChildMustFitThePhaseTypeAndANumberOutsideThePlanIsRefused(t *testing.T) {
	t.Parallel()
	// 定義の範囲の超過は承認を拒まず承認画面で見せる（判定が切り詰めるので、承認で止める理由が無い）。
	// 計画に無い番号はチケットの形の誤りで、判定では補えないので承認で止める。
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "src/a/*"))
	approved := w.mustApprove()
	if !w.isApproved("i0001-01-01") {
		t.Fatal("範囲の超過で承認が拒まれた")
	}
	contains(t, "承認画面", approved.Stdout, "編集対象としているが", "超えている", "調査")
	lacks(t, "stderr", approved.Stderr, "i0001-01-01 は承認の対象にしない")
	w.propose("i0001-05-05", childText("i0001-05-05", 5, "wip/research/*"))
	refused := w.approve()
	if refused.Code == 0 || !strings.Contains(refused.Stderr, "計画にありません") {
		t.Errorf("計画に無い番号の子が止まらない: code %d\n%s", refused.Code, refused.Stderr)
	}
}

func TestPhaseScopeIgnoresLetterCaseOnEveryMachine(t *testing.T) {
	t.Parallel()
	// 機械ごとに変えると、同じ提案が Linux では「定義の上限を超えている」で拒まれ、Windows では通る。
	// 範囲はユーザが宣言する意図なので、書かれたパスの意味で読む（子 ⊆ 定義 ⊆ 親 を 1 つの規則で揃える）。
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "WIP/Research/*"))
	r := w.approve()
	if r.Code != 0 {
		t.Fatalf("code %d\n%s", r.Code, r.Stderr)
	}
	if !w.isApproved("i0001-01-01") {
		t.Error("表記だけ違う範囲の子が承認されない")
	}
	lacks(t, "stderr", r.Stderr, "超えている")
}

func TestTheNextPhaseWaitsForThePreviousOne(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.propose("i0001-02-02", childText("i0001-02-02", 2, "wip/design/*"))
	w.commitParent()
	r := w.approve()
	if !w.isApproved("i0001-01-01") || w.isApproved("i0001-02-02") {
		t.Errorf("1 番目は通り 2 番目は外れるはず: 1=%v 2=%v", w.isApproved("i0001-01-01"), w.isApproved("i0001-02-02"))
	}
	contains(t, "stderr", r.Stderr, "クローズするまで承認しない")

	w.runChild("i0001-01-01", file{"wip/research/summary.md", "まとめ\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	// レビュー不要の定義なので、マーカーは skipped。2 番目が承認される。
	w.hook("PostToolUse", "Bash", w.parentTree, input{"command": "ls"})
	if r := w.approve(); r.Code != 0 || !w.isApproved("i0001-02-02") {
		t.Errorf("1 をクローズしても 2 が承認されない: code %d\n%s", r.Code, r.Stderr)
	}
}

func TestItemsThatDoNotWaitOnEachOtherArePlannedTogether(t *testing.T) {
	t.Parallel()
	// `after` で先行しないステップは並行する。
	w := newWS(t)
	w.family([]samples.Step{{Type: "acceptance"}, after("implement"), after("design", 1, 2)})
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "tests/a/*"))
	w.propose("i0001-02-02", childText("i0001-02-02", 2, "src/a/*"))
	w.commitParent()
	if r := w.approve(); r.Code != 0 || !w.isApproved("i0001-02-02") {
		t.Errorf("並行のステップが一緒に承認されない: code %d\n%s", r.Code, r.Stderr)
	}
}

func TestABatchDoesNotPassALaterChildOverOneThatReopensAnEarlierPhase(t *testing.T) {
	t.Parallel()
	// 前のフェーズに足す子と次のフェーズの子を一緒に承認しても、次の子は通さない（issue #31）。
	// 識別子の順で検査すると、開き直す前の状態で次の子が通っていた。
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/research/summary.md", "まとめ\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.hook("PostToolUse", "Bash", w.parentTree, input{"command": "ls"})
	// フェーズ 1 はクローズしてレビュー不要。次のフェーズの子と、フェーズ 1 に足す子を一緒に出す。
	w.propose("i0001-02-02", childText("i0001-02-02", 2, "wip/design/*"))
	w.propose("i0001-01-03", childReview("i0001-01-03", 1, samples.Off, "wip/research/*"))
	w.commitParent("approval-request 02 03")
	r := w.approve()
	if !w.isApproved("i0001-01-03") {
		t.Errorf("フェーズ 1 に足す子が承認されない: %s", r.Stderr)
	}
	if w.isApproved("i0001-02-02") {
		t.Error("開き直すフェーズを飛ばして次の子が承認された")
	}
	contains(t, "stderr", r.Stderr, "同じ承認で i0001-01-03 を追加するので再び開く")
}

func TestANewParentAndALaterPhaseChildTogetherKeepTheOrder(t *testing.T) {
	t.Parallel()
	// 承認済みチケットの無い親と 2 番目のフェーズの子を一緒に出すと、親の計画がディスクに無くフェーズが 1 つも並ばない。
	// 順序の検査が何も見ずに通っていた。
	w := newWS(t)
	w.propose(parentName, samples.ParentText(parentName, planOf("research", "design"), samples.ParentOpts{}))
	w.propose("i0001-02-01", childText("i0001-02-01", 2, "wip/design/*"))
	w.commitParent()
	r := w.approve()
	if !w.isApproved(parentName) {
		t.Errorf("親が承認されない: %s", r.Stderr)
	}
	if w.isApproved("i0001-02-01") {
		t.Error("2 番目のフェーズの子が承認された")
	}
	contains(t, "stderr", r.Stderr, "1（調査） がクローズするまで承認しない（子がまだ無い）")
}

func TestANewParentWithChildrenInTwoPhasesPassesOnlyTheFirst(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.propose(parentName, samples.ParentText(parentName, planOf("research", "design"), samples.ParentOpts{}))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.propose("i0001-02-02", childText("i0001-02-02", 2, "wip/design/*"))
	w.commitParent()
	r := w.approve()
	// 一部だけ置いて失敗した。何も置かずに失敗したとき（1）と分けるので 4。
	if r.Code != 4 {
		t.Errorf("code = %d, want 4\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	if !w.isApproved(parentName) || !w.isApproved("i0001-01-01") || w.isApproved("i0001-02-02") {
		t.Errorf("親と 1 番目だけが承認されるはず: 親=%v 1=%v 2=%v", w.isApproved(parentName), w.isApproved("i0001-01-01"), w.isApproved("i0001-02-02"))
	}
	contains(t, "stderr", r.Stderr, "同じ承認で i0001-01-01 を追加するが、まだクローズしていない")
}

func TestTheLastChildCannotCloseWithoutDeliverables(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01")
	refused := w.closeChild("i0001-01-01")
	if refused.Code == 0 {
		t.Fatal("成果物が無いのにクローズできた")
	}
	contains(t, "stderr", refused.Stderr, "成果物が無い", "wip/research/summary.md")
	tree := filepath.Join(w.root, ".claude", "worktrees", "i0001-01-01")
	ticketkit.Write(t, filepath.Join(tree, "wip", "research", "summary.md"), "x\n")
	w.git(tree, "add", "-A")
	w.git(tree, "commit", "--quiet", "-m", "summary")
	closed := w.closeChild("i0001-01-01")
	if closed.Code != 0 {
		t.Fatalf("code %d\n%s%s", closed.Code, closed.Stdout, closed.Stderr)
	}
	// 成果物が wip/ の下にあるときは、ready で消えることを止めずに言う。
	contains(t, "stdout", closed.Stdout, "wip/research/summary.md は wip/ の下にあり")
}

func TestADeliverableTrackedButDeletedFromTheWorktreeIsMissing(t *testing.T) {
	t.Parallel()
	// `git ls-files` はインデックスを見るので、削除をステージするまでは名前が返る。
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
	tree := w.runChild("i0001-01-01", file{"wip/research/summary.md", "x\n"})
	remove(t, filepath.Join(tree, "wip", "research", "summary.md"))
	refused := w.closeChild("i0001-01-01")
	if refused.Code == 0 || !strings.Contains(refused.Stderr, "成果物が無い") {
		t.Errorf("消えた成果物を無いものとして止めない: code %d\n%s", refused.Code, refused.Stderr)
	}
}

// finishTo は子を着手してクローズし、置き場（done か review）を返す。
func (w *ws) finishTo(name string, files ...file) string {
	w.t.Helper()
	w.runChild(name, files...)
	w.mustClose(name)
	if ticketkit.Exists(filepath.Join(w.approved, "done", name+".md")) {
		return "done"
	}
	if ticketkit.Exists(filepath.Join(w.parentTree, "wip", "proposals", "review", name+".md")) {
		return "review"
	}
	return ""
}

func TestTheNeedForReviewOfAChild(t *testing.T) {
	t.Parallel()
	// 子が human_review を書かなければ宣言なしで、定義の review に従う。計画のステップの `review: false` は定義の
	// `review: true` より優先する。子の `required: true` は「要る」へ上げる。
	cases := []struct {
		name   string
		plan   []samples.Step
		review samples.Flag
		allow  string
		file   file
		want   string
	}{
		{"宣言なしは定義の review:false に従う", planOf("research", "design"), samples.Unset, "wip/research/*", file{"wip/research/summary.md", "x\n"}, "done"},
		{"計画の review:false は定義の review:true に勝つ", []samples.Step{{Type: "design", Review: samples.Off}}, samples.Unset, "wip/design/*", file{"wip/design/plan.md", "d\n"}, "done"},
		{"子の required:true は計画の review:false でも要るへ上げる", []samples.Step{{Type: "design", Review: samples.Off}}, samples.On, "wip/design/*", file{"wip/design/plan.md", "d\n"}, "review"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t)
			w.family(c.plan)
			w.propose("i0001-01-01", childReview("i0001-01-01", 1, c.review, c.allow))
			w.commitParent()
			w.mustApprove()
			if got := w.finishTo("i0001-01-01", c.file); got != c.want {
				t.Errorf("クローズした先 = %q, want %q", got, c.want)
			}
		})
	}
}

func TestAFeedbackPlanStepCannotDropTheReview(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.propose(parentName, w.parent(planOf("design"), samples.ParentOpts{Feedback: []samples.Step{{Type: "implement-feedback", Review: samples.Off}}}))
	w.commitParent()
	refused := w.approve()
	if refused.Code == 0 {
		t.Fatal("フィードバック計画の review:false が承認された")
	}
	contains(t, "出力", refused.Stdout+refused.Stderr, "`review: false` は書けない")
}

func TestStartNamesTheProcessSkillOfThePhaseOnlyWhenInstalled(t *testing.T) {
	t.Parallel()
	for _, installed := range []bool{true, false} {
		name := "入っていない工程スキルは案内に出さない"
		if installed {
			name = "定義の skill が指す工程スキルがあれば名前で言う"
		}
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t)
			w.family(planOf("research", "design"))
			w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
			w.commitParent()
			w.mustApprove()
			w.startParent()
			w.worktree("i0001-01-01", parentName)
			if installed {
				ticketkit.Write(t, filepath.Join(w.root, ".claude", "skills", "process-research", "SKILL.md"), "---\nname: process-research\nprocess: true\n---\n")
			}
			started := w.mustOK(w.ccnavi("ticket", "start", "i0001-01-01"), "ticket start")
			if installed {
				contains(t, "stdout", started.Stdout, "最初に Skill ツールで process-research を呼ぶ")
			} else {
				lacks(t, "stdout", started.Stdout, "process-research")
			}
		})
	}
}

func TestADeferredPhaseDoesNotCloseTheGateAndIsCoveredLater(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family([]samples.Step{samples.DeferStep("design"), {Type: "implement"}, {Type: "acceptance"}})
	// implement は acceptance を要るが、acceptance が後ろにあっても計画としては足りる。
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	said := w.hook("PostToolUse", "Bash", w.parentTree, input{"command": "ls"})
	contains(t, "hook", w.reason(said), "一緒に見る")
	spawn := w.hook("PreToolUse", "Agent", w.parentTree, input{"description": "次"})
	lacks(t, "hook", w.reason(spawn), "DENY_PHASE_REVIEW")
	// 延期したフェーズには依頼できない。
	w.merge("i0001-01-01")
	fixture := w.remote()
	refused := w.request(fixture, 1)
	if refused.Code == 0 {
		t.Fatal("延期したフェーズに依頼できた")
	}
	contains(t, "stderr", refused.Stderr, "一緒に見る")
	// 2 番目をクローズして依頼すると、1 番目も含まれる。
	w.propose("i0001-02-02", childText("i0001-02-02", 2, "src/a/*"))
	w.commitParent("approval-request 02")
	w.mustApprove()
	w.runChild("i0001-02-02", file{"src/a/x.py", "x\n"})
	w.mustClose("i0001-02-02")
	w.commitParent("close 02")
	w.merge("i0001-02-02")
	w.push()
	w.hook("PostToolUse", "Bash", w.parentTree, input{"command": "ls"})
	spawn = w.hook("PreToolUse", "Agent", w.parentTree, input{"description": "次"})
	contains(t, "hook", w.reason(spawn), "DENY_PHASE_REVIEW")
	ok := w.request(fixture, 2)
	if ok.Code != 0 {
		t.Fatalf("code %d\n%s", ok.Code, ok.Stderr)
	}
	contains(t, "依頼の本文", w.lastBody, "このレビューが含むフェーズ", "1（設計）", "2（実装とテスト）")
}

func TestFinishRecordsWhereItPutTheChildAndConfirmRecordsTheRoute(t *testing.T) {
	t.Parallel()
	// ユーザが手で動かした子は settled_by を持たないので、ccnavi が動かしたものと区別できる。
	w := newWS(t)
	w.family(planOf("design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	w.mustClose("i0001-01-01")
	waiting := filepath.Join(w.parentTree, "wip", "proposals", "review", "i0001-01-01.md")
	if !ticketkit.Exists(waiting) {
		t.Fatal("finish したレビュー待ちの子が無い")
	}
	text := ticketkit.Read(t, waiting)
	contains(t, "レビュー待ちの子", text, `finished_to: "review"`)
	lacks(t, "レビュー待ちの子", text, "settled_by")
	w.commitParent("close 01")
	w.merge("i0001-01-01")
	fixture := w.remote()
	w.mustOK(w.request(fixture, 1), "request")
	w.mustOK(w.confirm(fixture, 1), "confirm")
	contains(t, "done の子", ticketkit.Read(t, filepath.Join(w.approved, "done", "i0001-01-01.md")), `finished_to: "review"`, `settled_by: "confirm"`)
}

func TestAFailedCopyDoesNotClearTheMarksOfAReviewedPhase(t *testing.T) {
	t.Parallel()
	// 承認済みチケットを置くのと、そのフェーズのマーカーを消すのは、置けたときだけ両方起きる。置けなかったのにマーカーだけ
	// 消えると、子は 1 枚も増えていないのにレビュー準備中へ戻り、サブエージェントの起動が止まる（DENY_PHASE_REVIEW）。
	w := newWS(t)
	w.family(planOf("design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.merge("i0001-01-01")
	fixture := w.remote()
	w.mustOK(w.request(fixture, 1), "request")
	w.mustOK(w.confirm(fixture, 1), "confirm")
	w.wantBoard(1, false, false, "requested", "reviewed")
	// 同じフェーズに子をもう 1 枚足すが、置き場に同じ名前のディレクトリを作って書けなくする。
	w.propose("i0001-01-02", childText("i0001-01-02", 1, "wip/design/*"))
	w.commitParent("approval-request 02")
	if err := os.MkdirAll(filepath.Join(w.approved, "doing", "i0001-01-02.md"), 0o755); err != nil {
		t.Fatal(err)
	}
	refused := w.approve()
	if refused.Code == 0 || !strings.Contains(refused.Stderr, "保存できませんでした") {
		t.Errorf("置けないのに止まらない: code %d\n%s", refused.Code, refused.Stderr)
	}
	// 子は増えていない。だからマーカーも消えていない。
	w.wantBoard(1, false, false, "requested", "reviewed")
}

func (w *ws) wantBoard(number int, gate, waiting bool, marks ...string) {
	w.t.Helper()
	g, wt, m := w.boardPhase(number)
	if g != gate || wt != waiting || strings.Join(m, ",") != strings.Join(marks, ",") {
		w.t.Errorf("フェーズ %d のボード = (%v, %v, %v), want (%v, %v, %v)", number, g, wt, m, gate, waiting, marks)
	}
}
