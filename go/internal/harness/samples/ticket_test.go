package samples

import (
	"slices"
	"strings"
	"testing"
)

func TestPlanItemLinesFormsAndDefaultChain(t *testing.T) {
	t.Parallel()
	steps := []Step{
		{Type: "research"},
		ReviewStep("design"),
		DeferStep("chores"),
		{Type: "implement", Review: Off, After: []int{1, 2}},
		{Type: "acceptance", After: []int{}},
	}
	want := []string{
		"  - research",
		"  - {type: design, review: true, after: [1]}",
		"  - {type: chores, deferred: true, after: [2]}",
		"  - {type: implement, review: false, after: [1, 2]}",
		"  - acceptance",
	}
	if got := PlanItemLines(steps, 1); !slices.Equal(got, want) {
		t.Errorf("got\n%s\nwant\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
	// フィードバック計画は続きの番号から数える
	if got := PlanItemLines(Plan("a", "b"), 4); got[1] != "  - {type: b, after: [4]}" {
		t.Errorf("feedback chain: %q", got[1])
	}
}

func TestParentTextCopiesOnlyTheDefinitionsThePlanUses(t *testing.T) {
	t.Parallel()
	text := ParentText("i0001", Plan("design", "implement"), ParentOpts{Feedback: Plan("implement-feedback")})
	for _, want := range []string{"ticket: i0001\n", "feedback:\n  - implement-feedback\n", "phases:\n  design:\n", "  implement:\n", "  implement-feedback:\n"} {
		if !strings.Contains(text, want) {
			t.Errorf("本文に %q が無い:\n%s", want, text)
		}
	}
	for _, not := range []string{"  research:", "  acceptance:", "  chores:"} {
		if strings.Contains(text, not) {
			t.Errorf("使わない定義 %q をコピーしている", not)
		}
	}
	if strings.Index(text, "phases:") > strings.Index(text, "plan:") {
		t.Error("phases: は plan: の前に差し込む")
	}
	if text := ParentText("i0002", Plan("design"), ParentOpts{NoCopy: true}); strings.Contains(text, "phases:") {
		t.Error("NoCopy は phases: を書かない")
	}
	if text := ParentText("i0003", Plan("design"), ParentOpts{Feedback: []Step{}, Allow: []string{}, Issue: 7}); !strings.Contains(text, "feedback: []\n") ||
		!strings.Contains(text, "issue: 7\n") || !strings.Contains(text, "allow:\nstarted_at") {
		t.Errorf("明示した指定が書かれていない:\n%s", text)
	}
}

func TestChildTextHumanReviewDeclaration(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		review Flag
		want   string
	}{
		{"required", On, "human_review:\n  required: true\n"},
		{"not required", Off, "human_review:\n  required: false\n"},
		{"none", Unset, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			text := ChildTextReview("c1", "i1", 2, []string{"src/*"}, c.review)
			if has := strings.Contains(text, "human_review:"); has != (c.want != "") || !strings.Contains(text, c.want) {
				t.Errorf("human_review の書き方が違う:\n%s", text)
			}
			if !strings.Contains(text, "parent: i1\nphase: 2\n") || !strings.Contains(text, `glob: "src/*"`) {
				t.Errorf("親・フェーズ・allow が無い:\n%s", text)
			}
		})
	}
}

func TestPlainParentAddsTheMinimalPlanOnlyToParentsWithoutOne(t *testing.T) {
	t.Parallel()
	plain := ParentText("i1", Plan("work"), ParentOpts{NoCopy: true})
	plain = strings.Replace(plain, "plan:\n  - work\n", "", 1)
	got := PlainParent(plain, Work(2))
	if !strings.Contains(got, "plan:\n  - {type: work, after: []}\n  - {type: work, after: [1]}\nhuman_review:") ||
		!strings.Contains(got, "phases:\n  work:\n") {
		t.Errorf("最小の計画とコピーが足されていない:\n%s", got)
	}
	if PlainParent(got, Work(2)) != got {
		t.Error("plan: がある本文は変えない")
	}
	child := ChildText("c1", "i1", 1, nil)
	if PlainParent(child, Work(1)) != child {
		t.Error("子は変えない")
	}
	if PlainParent(plain, nil) != plain {
		t.Error("計画が空なら変えない")
	}
}
