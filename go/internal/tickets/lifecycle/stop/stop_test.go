package stop

import (
	"strings"
	"testing"

	"ccnavi/internal/tickets/model/ticket"
)

// 共通の 1 文は全部の段の促しと finish の促しに 1 回ずつ入り、段ごとの次の一手は残る。
func TestEveryStepKeepsItsGuidanceAndGetsTheCommonNote(t *testing.T) {
	root := clock.Workspace(t)
	guide := map[Step][]string{
		stepFinish:  {"finish i0001-01-01", "レビューの依頼（review-request）へ進む"},
		StepConfirm: {"confirm --phase 1", "decide をユーザに頼む"},
		stepMerge:   {"status i0001", "合流済みと確かめられたら"},
		stepLateFix: {"同じフェーズに子を足して", "判断を仰ぐ"},
		stepRequest: {"未コミットの変更があれば先にコミットして push", "review-request --phase 1"},
	}
	parent := &ticket.Ticket{ID: "i0001"}
	for step, needles := range guide {
		text := RequestNudge(root, &Pending{parent, 1, step, root, "abc", []string{"i0001-01-01"}, "設計"})
		for _, n := range append(needles, "ユーザの許可は要りません", userStopNote, "この案内は同じ状態では 1 回だけ") {
			if !strings.Contains(text, n) {
				t.Errorf("%s: %q が無い\n%s", step, n, text)
			}
		}
		if c := strings.Count(text, "止めた理由と、再開するときの次の一手を1行で書きます。"); c != 1 {
			t.Errorf("%s: 共通の文が %d 回", step, c)
		}
	}
	child := &ticket.Ticket{ID: "i0001-01-01", Parent: "i0001", Phase: 1, BaseSHA: strings.Repeat("0", 40)}
	text := FinishNudge(root, &Unfinished{child, root, 2, "abc"})
	for _, n := range []string{CodeFinishNudge, "finish i0001-01-01", "続ける理由（何が残っているか）をユーザに向けて書いてから終える", userStopNote} {
		if !strings.Contains(text, n) {
			t.Errorf("finish の促しに %q が無い\n%s", n, text)
		}
	}
}
