package core

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// ボードの「レビュー完了にできるか」は、confirmGate と同じ stateRefusal を通る。理由の種別は confirm の断りと同じ。
func TestConfirmableByStateUsesTheConfirmRefusals(t *testing.T) {
	reviewed := &phase.Phase{Number: 2, Marks: map[marks.Kind]*pyjson.Object{marks.Reviewed: pyjson.NewObject(), marks.Requested: pyjson.NewObject()}}
	if r := stateRefusal(reviewed, 2); r == nil || r.code != codeConfirmAlreadyReviewed {
		t.Errorf("レビュー済み: %v", r)
	}
	unrequested := &phase.Phase{Number: 3, Marks: map[marks.Kind]*pyjson.Object{}}
	if r := stateRefusal(unrequested, 3); r == nil || r.code != codeConfirmNotRequested {
		t.Errorf("依頼なし: %v", r)
	}
	requested := &phase.Phase{Number: 1, Marks: map[marks.Kind]*pyjson.Object{marks.Requested: pyjson.NewObject()}}
	if r := stateRefusal(requested, 1); r != nil {
		t.Errorf("依頼済みは通る: %v", r)
	}
	// 親がクローズ済みなら、親が作業中に無い扱い（理由に `ccnavi: ` は付けない）。
	got := ConfirmableByState(nil, "", &ticket.Ticket{ID: "i0001"}, true, requested, nil)
	if got.OK || got.Code != string(codeConfirmNoParent) || strings.HasPrefix(got.Reason, linePrefix) {
		t.Errorf("クローズ済みの親 = %+v", got)
	}
}
