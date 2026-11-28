package diagnose

import (
	"slices"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/core"
)

func inboxIDs(o *pyjson.Object, key string) []string {
	ids, _ := o.Value(key).([]string)
	return ids
}

// 1 つのチケットは上から最初に当てはまる 1 種類だけに入る。承認待ち → レビュー待ちのフェーズ → 注意 → フローの下書き。
func TestInboxPutsEachTicketInTheFirstKindItMatches(t *testing.T) {
	rows := []*ticketRow{
		// 親はフェーズ 2 がレビュー待ちなので review に入る（attention でも notice に入れない）
		calmRow("i0001", "", 0, func(r *ticketRow) { r.attention = true }),
		calmRow("i0001-01-01", "i0001", 1, func(r *ticketRow) { r.attention, r.flowDraft = true, true }),
		calmRow("i0001-02-01", "i0001", 2, func(r *ticketRow) { r.attention = true }),
		calmRow("i0001-02-02", "i0001", 2, func(r *ticketRow) { r.attention, r.flowDraft = true, true }),
		calmRow("i0001-03-01", "i0001", 3, func(r *ticketRow) { r.flowDraft = true }),
		calmRow("i0001-03-02", "i0001", 3, func(r *ticketRow) { r.attention, r.flowDraft = true, true }),
		calmRow("i0001-03-03", "i0001", 3, nil),
		calmRow("i0002", "", 0, func(r *ticketRow) { r.attention = true }),
	}
	parents := []*parentRow{{id: "i0001", phases: []phaseRow{{number: 1}, {number: 2, gateClosed: true, reviewWaiting: true, confirm: core.Confirmability{Code: "CORE_CONFIRM_NO_PARENT", Reason: "親が作業中にありません"}}, {number: 3}}}}
	got := inboxRecord(rows, parents, []string{"i0001-03-02", "i0002"}, []any{})
	if want := []string{"i0001-03-02", "i0002"}; !slices.Equal(inboxIDs(got, "approval"), want) {
		t.Errorf("approval = %v", got.Value("approval"))
	}
	review, _ := got.Value("review").([]any)
	if len(review) != 1 || review[0].(*pyjson.Object).String("parent") != "i0001" || review[0].(*pyjson.Object).Value("phase") != 2 {
		t.Errorf("review = %v", review)
	}
	// 完了にできるかの答え（フェーズの review_confirm と同じもの）を、理由つきでそのまま持つ。
	if c, _ := review[0].(*pyjson.Object).Value("review_confirm").(*pyjson.Object); c == nil || c.Value("ok") != false || c.String("code") != "CORE_CONFIRM_NO_PARENT" {
		t.Errorf("review_confirm = %v", review[0].(*pyjson.Object).Value("review_confirm"))
	}
	if want := []string{"i0001-01-01"}; !slices.Equal(inboxIDs(got, "notice"), want) {
		t.Errorf("notice = %v", got.Value("notice"))
	}
	if want := []string{"i0001-03-01"}; !slices.Equal(inboxIDs(got, "flow_draft"), want) {
		t.Errorf("flow_draft = %v", got.Value("flow_draft"))
	}
}

// チケット制御が切れているときの形。どの種類も空の配列で出す（null にしない）。
func TestInboxWithoutTicketsIsEmptyLists(t *testing.T) {
	got := inboxRecord(nil, nil, nil, []any{})
	for _, key := range []string{"approval", "notice", "flow_draft"} {
		if ids, ok := got.Value(key).([]string); !ok || len(ids) != 0 {
			t.Errorf("%s = %#v", key, got.Value(key))
		}
	}
	if review, ok := got.Value("review").([]any); !ok || len(review) != 0 {
		t.Errorf("review = %#v", got.Value("review"))
	}
}
