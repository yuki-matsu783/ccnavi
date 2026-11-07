package stopreview

import (
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
)

func row(criterion criterionID, target string, result outcome) string {
	return fmt.Sprintf(`{"criterion": %q, "target": %q, "result": %q}`, criterion, target, result)
}

const flawRow = `{"criterion": "R2", "target": "p1", "result": "fail", ` +
	`"locations": [{"kind": "proposal", "text": "完了の確かめ方が無い"}], ` +
	`"fixes": [{"kind": "revise_proposal", "text": "確認の手順を足す"}], "mismatch": "依頼は結合の確認を求めている"}`

func result(aid string, class classification, rows ...string) string {
	return fmt.Sprintf(`{"attempt_id": %q, "classification": %q, "results": [%s]}`, aid, class, strings.Join(rows, ", "))
}

func passing(aid string, withP1 bool) string {
	rows := []string{row(r1, StopTarget, pass), row(r3, StopTarget, pass)}
	if withP1 {
		rows = append(rows, row(r2, "p1", pass), row(r4, "p1", pass))
	}
	return result(aid, ticketWork, rows...)
}

func flawed(aid string) string {
	return result(aid, ticketWork, row(r1, StopTarget, pass), row(r3, StopTarget, pass), flawRow, row(r4, "p1", pass))
}

func accept(t *testing.T, text string) AcceptInput {
	t.Helper()
	return AcceptInput{Result: load(t, text), effective: config.Enable, MaterialVersion: testVersion}
}

func startRunning(t *testing.T, s Store, key string, plan Plan) string {
	t.Helper()
	aid := mustPlan(t, s, key, plan, time.Unix(0, 0))
	begin(t, s, key)
	return aid
}

func finalIs(t *testing.T, s Store, aid string, want Final) {
	t.Helper()
	if got := s.readAttempt(aid).String(keyFinal); got != string(want) {
		t.Errorf("最終分類 = %s, want %s", got, want)
	}
}

func TestAcceptClassifies(t *testing.T) {
	cases := []struct {
		name      string
		build     func(aid string) string
		effective config.Mode
		insuff    bool
		ok        bool
		state     state
		final     Final
	}{
		{"終了可", func(a string) string { return passing(a, true) }, config.Enable, false, true, done, finalOK},
		{"dry-run の不備は止めない", flawed, config.DryRun, false, true, done, finalFlawed},
		{"判定不能は未検査", func(a string) string {
			return result(a, ticketWork, row(r1, StopTarget, unknown), row(r3, StopTarget, pass), row(r2, "p1", pass), row(r4, "p1", pass))
		}, config.Enable, false, true, done, FinalUnchecked},
		{"不備は判定不能に勝つ", func(a string) string {
			return result(a, ticketWork, row(r1, StopTarget, unknown), row(r3, StopTarget, pass), flawRow, row(r4, "p1", pass))
		}, config.Enable, false, false, sentBack, finalFlawed},
		{"材料不足は未検査で止めない", flawed, config.Enable, true, true, done, FinalUnchecked},
		{"R3 の fail だけでは不備でない", func(a string) string {
			r3fail := `{"criterion": "R3", "target": "stop", "result": "fail", "locations": [{"kind": "final_answer", "text": "x"}]}`
			return result(a, ticketWork, row(r1, StopTarget, pass), r3fail, row(r2, "p1", pass), row(r4, "p1", pass))
		}, config.Enable, false, true, done, finalOK},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s := newStore(t)
			aid := startRunning(t, s, testKey, basePlan())
			in := accept(t, c.build(aid))
			in.effective, in.insufficient = c.effective, c.insuff
			if out := s.Accept(testKey, in); out.ok != c.ok {
				t.Errorf("Accept = %+v", out)
			}
			stateIs(t, s, aid, c.state)
			finalIs(t, s, aid, c.final)
		})
	}
}

func TestFlawedEnableSendsBackWithTheRequiredText(t *testing.T) {
	s := newStore(t)
	aid := startRunning(t, s, testKey, basePlan())
	out := s.Accept(testKey, accept(t, flawed(aid)))
	if out.ok {
		t.Fatal("差し戻さない")
	}
	for _, needle := range []string{aid, "R2", "完了の確かめ方が無い", "確認の手順を足す", "依頼は結合の確認を求めている", rewriteQuestion} {
		if !strings.Contains(out.reason, needle) {
			t.Errorf("差し戻しの文に %q が無い:\n%s", needle, out.reason)
		}
	}
	stateIs(t, s, aid, sentBack)
	finalIs(t, s, aid, finalFlawed)
	if got := dumps(t, out.Object()); !strings.HasPrefix(got, `{"ok": false, "reason": "Stop の AI 判定`) {
		t.Errorf("返り値 = %s", got)
	}
}

func TestNotTicketWorkIsOutOfScopeByAI(t *testing.T) {
	s := newStore(t)
	aid := startRunning(t, s, testKey, basePlan())
	if out := s.Accept(testKey, accept(t, result(aid, notTicketWork))); out != OKReturn {
		t.Errorf("Accept = %+v", out)
	}
	a := s.readAttempt(aid)
	if a.String(keyFinal) != string(FinalOutOfScope) || a.String(keyJudge) != string(judgeAI) {
		t.Errorf("試行 = %s", dumps(t, a))
	}
}

func TestRejectionsCloseUncheckedAndReturnOK(t *testing.T) {
	cases := map[string]func(aid string) string{
		"試行 ID が違う": func(string) string { return passing("zzz", true) },
		"分類が不正":     func(a string) string { return result(a, "?") },
		"基準が足りない":   func(a string) string { return passing(a, false) },
		"計画に無い対象": func(a string) string {
			return strings.Replace(passing(a, true), "]}", ", "+row(r2, "nope", pass)+"]}", 1)
		},
		"問題箇所の無い不備": func(a string) string {
			return result(a, ticketWork, row(r4, "p1", fail), row(r1, StopTarget, pass), row(r3, StopTarget, pass), row(r2, "p1", pass))
		},
		"オブジェクトでない": func(string) string { return `"text"` },
		"results が列でない": func(a string) string {
			return fmt.Sprintf(`{"attempt_id": %q, "classification": "ticket_work", "results": "x"}`, a)
		},
		// 判定役の誤出力（Python は TypeError で拒否していた）。
		"R2 の対象が列": func(a string) string {
			return result(a, ticketWork, `{"criterion": "R2", "target": ["x"], "result": "pass"}`)
		},
		"結果が列": func(a string) string {
			return result(a, ticketWork, `{"criterion": "R1", "target": "stop", "result": ["pass"]}`)
		},
		"問題箇所の種別が列": func(a string) string {
			return result(a, ticketWork, `{"criterion": "R3", "target": "stop", "result": "fail", "locations": [{"kind": ["x"], "text": "t"}]}`)
		},
	}
	for name, build := range cases {
		t.Run(name, func(t *testing.T) {
			s := newStore(t)
			aid := startRunning(t, s, testKey, basePlan())
			if out := s.Accept(testKey, accept(t, build(aid))); out != OKReturn {
				t.Errorf("Accept = %+v", out)
			}
			a := s.readAttempt(aid)
			if a.String(keyFinal) != string(FinalUnchecked) || a.Value(keyAccepted) != false ||
				!strings.HasPrefix(a.String(keyReason), "rejected: ") {
				t.Errorf("試行 = %s", dumps(t, a))
			}
		})
	}
}

func TestTypeErrorsAreNamedLikePython(t *testing.T) {
	_, err := validate(load(t, result("a", ticketWork, `{"criterion": "R2", "target": ["x"], "result": "pass"}`)), Plan{AttemptID: "a"})
	if err == nil || err.Error() != "TypeError" {
		t.Errorf("validate = %v", err)
	}
	_, err = validate(load(t, result("a", ticketWork, `{"criterion": "R9", "target": "stop", "result": "pass"}`)), Plan{AttemptID: "a"})
	if err == nil || err.Error() != "基準 ID が不正: 'R9'" {
		t.Errorf("validate = %v", err)
	}
}

func TestChangedApprovalOrMaterialIsRejected(t *testing.T) {
	s := newStore(t)
	aid := startRunning(t, s, testKey, basePlan())
	in := accept(t, flawed(aid))
	in.StillUnapproved = func(string) bool { return false }
	if out := s.Accept(testKey, in); out != OKReturn {
		t.Errorf("Accept = %+v", out)
	}
	finalIs(t, s, aid, FinalUnchecked)

	s = newStore(t)
	aid = startRunning(t, s, testKey, basePlan())
	in = accept(t, flawed(aid))
	in.MaterialVersion = "m2"
	if out := s.Accept(testKey, in); out != OKReturn {
		t.Errorf("Accept = %+v", out)
	}
	finalIs(t, s, aid, FinalUnchecked)
}

func TestSecondRecordAndMissingPlanAreIgnored(t *testing.T) {
	s := newStore(t)
	if out := s.Accept(testKey, accept(t, passing("x", true))); out != OKReturn {
		t.Errorf("計画なし: %+v", out)
	}
	aid := startRunning(t, s, testKey, basePlan())
	s.Accept(testKey, accept(t, passing(aid, true)))
	if out := s.Accept(testKey, accept(t, flawed(aid))); out != OKReturn {
		t.Errorf("2 回目: %+v", out)
	}
	finalIs(t, s, aid, finalOK)
}

func TestNotTicketWorkIsRefusedWhenCcnaviSawAProposal(t *testing.T) {
	s := newStore(t)
	key := "proposal:x:unsent"
	plan := Plan{IsCandidate: true, Targets: []string{StopTarget, key}, NoR2: []string{key},
		Proposals: []Proposal{{Key: key, Ident: "x", Route: RouteUnsent}}}
	aid := startRunning(t, s, testKey, plan)
	s.Accept(testKey, AcceptInput{Result: load(t, result(aid, notTicketWork))})
	a := s.readAttempt(aid)
	if a.String(keyFinal) != string(FinalUnchecked) || a.Value(keyAccepted) != false {
		t.Errorf("試行 = %s", dumps(t, a))
	}
}

// 判定役が本文の無い版に余分な R2 を出しても、記録ごと拒否せず、その行だけを捨てる。
func TestStrayR2ForAVersionWithoutBodyIsDropped(t *testing.T) {
	s := newStore(t)
	key := "proposal:x:unsent"
	plan := Plan{IsCandidate: true, Targets: []string{StopTarget, key}, NoR2: []string{key},
		Proposals: []Proposal{{Key: key, Ident: "x", Route: RouteUnsent}}}
	aid := startRunning(t, s, testKey, plan)
	stray := `{"criterion": "R2", "target": "proposal:x:unsent", "result": "fail", "locations": [{"kind": "proposal", "text": "t"}], "fixes": [{"kind": "revise_proposal", "text": "t"}]}`
	text := result(aid, ticketWork, row(r1, StopTarget, pass), row(r3, StopTarget, pass), row(r4, key, pass), stray)
	if out := s.Accept(testKey, AcceptInput{Result: load(t, text)}); out != OKReturn {
		t.Errorf("Accept = %+v", out)
	}
	a := s.readAttempt(aid)
	if a.Value(keyAccepted) != true || a.String(keyFinal) != string(finalOK) || strings.Contains(dumps(t, a.Value(keyResults)), `"R2"`) {
		t.Errorf("試行 = %s", dumps(t, a))
	}
}

func TestRejudgeResolvesTheOriginalAndNeverSendsBack(t *testing.T) {
	s := newStore(t)
	first := startRunning(t, s, testKey, basePlan())
	if s.Accept(testKey, accept(t, flawed(first))).ok {
		t.Fatal("差し戻さない")
	}
	if got, ok := s.ConsumeContinuation("s1", time.Now()); !ok || got != first {
		t.Errorf("ConsumeContinuation = %q, %v", got, ok)
	}
	if _, ok := s.ConsumeContinuation("s1", time.Now()); ok {
		t.Error("2 回消費した")
	}
	rejudge := basePlan()
	rejudge.Rejudge, rejudge.Original = true, first
	for _, c := range []struct {
		key, want string
		build     func(string) string
	}{
		{"s1:u-next", "resolved", func(a string) string { return passing(a, true) }},
		{"s1:u-third", "unresolved", flawed},
	} {
		aid := startRunning(t, s, c.key, rejudge)
		if out := s.Accept(c.key, accept(t, c.build(aid))); out != OKReturn {
			t.Errorf("再判定で差し戻した: %+v", out)
		}
		if got := s.readAttempt(first).String(keyRejudge); got != c.want {
			t.Errorf("元の試行 = %s, want %s", got, c.want)
		}
	}
	for final, want := range map[Final]string{FinalUnchecked: "inconclusive", finalFlawed: "unresolved", finalOK: "resolved"} {
		s.resolveOriginal(first, final)
		if got := s.readAttempt(first).String(keyRejudge); got != want {
			t.Errorf("%s → %s, want %s", final, got, want)
		}
	}
}

func TestSentBackIsNotExpiredEarlyNorConsumedByOthers(t *testing.T) {
	s := newStore(t)
	first := startRunning(t, s, testKey, basePlan())
	s.Accept(testKey, accept(t, flawed(first)))
	if got := s.ExpireOverdue(time.Unix(1_000_000_000, 0)); got != nil {
		t.Errorf("差し戻しの前の時刻で期限切れ: %v", got)
	}
	if _, ok := s.ConsumeContinuation("other", time.Now()); ok {
		t.Error("別のセッションが消費した")
	}
	// 差し戻しから期限を過ぎたものは継続として消費せず、期限切れで閉じても不備検出を残す。
	later := moment(s.readAttempt(first).Value(keySentAt), epoch).Add(10_000 * time.Second)
	if _, ok := s.ConsumeContinuation("s1", later); ok {
		t.Error("古い差し戻しを消費した")
	}
	if got := s.ExpireOverdue(later); !slices.Equal(got, []string{first}) {
		t.Errorf("ExpireOverdue = %v", got)
	}
	stateIs(t, s, first, expired)
	finalIs(t, s, first, finalFlawed)
}

func TestSentBackIsNotedOnTheLastStop(t *testing.T) {
	s := newStore(t)
	s.NoteLastStop("s1", testKey, true, false, time.Unix(5, 0))
	aid := startRunning(t, s, testKey, basePlan())
	s.Accept(testKey, accept(t, flawed(aid)))
	last, ok := s.LastStop("s1")
	if !ok || last.key != testKey || !last.Nudged || !last.SentBack {
		t.Errorf("LastStop = %+v, %v", last, ok)
	}
}
