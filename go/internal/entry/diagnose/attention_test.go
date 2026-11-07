package diagnose

import (
	"slices"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/config/risk"
)

// calmRow は要対応を決めるのに要る欄だけを持つ、順調なチケット 1 件（作業中でワークツリーがある）。
func calmRow(id, parent string, phase int, change func(*ticketRow)) *ticketRow {
	r := &ticketRow{obj: pyjson.NewObject(), id: id, parent: parent, phase: phase, status: statusOpen, worktree: true}
	if change != nil {
		change(r)
	}
	return r
}

type marked struct {
	issues    []string
	attention bool
}

func mark(rows []*ticketRow, phases []phaseRow, pending ...string) map[string]marked {
	waiting := map[string]bool{}
	for _, id := range pending {
		waiting[id] = true
	}
	markAttention(rows, []*parentRow{{id: "i0001", phases: phases}}, waiting)
	out := map[string]marked{}
	for _, r := range rows {
		issues, _ := r.obj.Value("issues").([]string)
		attention, _ := r.obj.Value("attention").(bool)
		out[r.id] = marked{issues, attention}
	}
	return out
}

// childAttention は順調な親と、change を当てた子 1 枚（フェーズ 1）の、子の答え。
func childAttention(change func(*ticketRow)) marked {
	return mark([]*ticketRow{calmRow("i0001", "", 0, nil), calmRow("i0001-01-01", "i0001", 1, change)},
		[]phaseRow{{number: 1}})["i0001-01-01"]
}

func TestAttention(t *testing.T) {
	gone := func(r *ticketRow) { r.worktree = false }
	cases := []struct {
		name   string
		change func(*ticketRow)
		want   bool
	}{
		{"順調", nil, false},
		{"未着手・作業中の列でワークツリーが無い", gone, true},
		{"レビュー待ちでワークツリーが無い", func(r *ticketRow) { gone(r); r.hasProposal, r.status = true, statusReview }, true},
		// 閉じた（完了・取り消し）子はワークツリーが無くても要対応ではない
		{"閉じた子のワークツリーが無い", func(r *ticketRow) { gone(r); r.status = statusClosed }, false},
		// 提案が残っていれば閉じていても未着手・作業中の列
		{"提案の残る閉じた子のワークツリーが無い", func(r *ticketRow) { gone(r); r.status, r.hasProposal = statusClosed, true }, true},
		{"どれが正か決まらない", func(r *ticketRow) { r.scattered = true }, true},
	}
	for _, level := range []risk.Level{risk.High, risk.Critical, risk.Medium, risk.Low} {
		cases = append(cases, struct {
			name   string
			change func(*ticketRow)
			want   bool
		}{"リスク " + string(level), func(r *ticketRow) { r.risk = pyjson.NewObject("points", 70, "level", string(level)) },
			risk.Escalates(level)})
	}
	for _, c := range cases {
		got := childAttention(c.change)
		if got.attention != c.want {
			t.Errorf("%s: attention %v", c.name, got.attention)
		}
	}
	// 順調な親も要対応ではなく、不備も無い
	for id, got := range mark([]*ticketRow{calmRow("i0001", "", 0, nil), calmRow("i0001-01-01", "i0001", 1, nil)},
		[]phaseRow{{number: 1}}) {
		if got.attention || len(got.issues) != 0 {
			t.Errorf("%s: %+v", id, got)
		}
	}
}

func TestAttentionFollowsPhases(t *testing.T) {
	if got := mark([]*ticketRow{calmRow("i0001", "", 0, nil)}, nil, "i0001")["i0001"]; !got.attention {
		t.Error("承認待ちは要対応")
	}
	// 子は自分のフェーズだけを見る
	got := mark([]*ticketRow{calmRow("i0001", "", 0, nil), calmRow("i0001-01-01", "i0001", 1, nil),
		calmRow("i0001-02-01", "i0001", 2, nil)}, []phaseRow{{number: 1, gateClosed: true}, {number: 2}})
	if !got["i0001-01-01"].attention || got["i0001-02-01"].attention {
		t.Errorf("gate_closed: %+v", got)
	}
	if got := childAttention(nil); got.attention {
		t.Error("順調な子")
	}
	got = mark([]*ticketRow{calmRow("i0001", "", 0, nil), calmRow("i0001-01-01", "i0001", 1, nil)},
		[]phaseRow{{number: 1, reviewWaiting: true}})
	if !got["i0001-01-01"].attention {
		t.Error("review_waiting")
	}
	// 親はフェーズのどれかで要対応。子はほかのフェーズでは要対応にならない
	for _, p := range []phaseRow{{number: 2, gateClosed: true}, {number: 2, reviewWaiting: true}, {number: 2, escalates: true}} {
		got := mark([]*ticketRow{calmRow("i0001", "", 0, nil), calmRow("i0001-01-01", "i0001", 1, nil)},
			[]phaseRow{{number: 1}, p})
		if !got["i0001"].attention || got["i0001-01-01"].attention {
			t.Errorf("%+v: %+v", p, got)
		}
	}
}

func TestIssues(t *testing.T) {
	blocked := childAttention(func(r *ticketRow) { r.blocked = "親が引けない" })
	if !slices.Equal(blocked.issues, []string{"書き込みが止まっています: 親が引けない"}) || !blocked.attention {
		t.Errorf("blocked: %+v", blocked)
	}
	for _, status := range []copyStatus{statusNone, statusReview} {
		got := childAttention(func(r *ticketRow) { r.status = status })
		if !slices.Equal(got.issues, []string{"提案が見つかりません（承認済みチケットだけがあります）"}) || !got.attention {
			t.Errorf("%s: %+v", status, got)
		}
	}
	// 提案があれば不備ではない
	if got := childAttention(func(r *ticketRow) { r.status, r.hasProposal = statusNone, true }); len(got.issues) != 0 {
		t.Errorf("提案あり: %+v", got)
	}
	// 並びは、書き込みの止め・提案が無い・親が無い
	stray := calmRow("i0002-01-01", "i0002", 1, func(r *ticketRow) { r.blocked, r.status = "b", statusNone })
	got := mark([]*ticketRow{stray}, nil)["i0002-01-01"]
	want := []string{"書き込みが止まっています: b", "提案が見つかりません（承認済みチケットだけがあります）", "親 i0002 が見つかりません"}
	if !slices.Equal(got.issues, want) || !got.attention {
		t.Errorf("並び: %+v", got)
	}
}
