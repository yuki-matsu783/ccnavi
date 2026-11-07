package stopreview

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
)

const (
	testKey     = "s1:u-last"
	testVersion = "m1"
)

func basePlan() Plan {
	return Plan{IsCandidate: true, Targets: []string{StopTarget, "p1"}, MaterialVersion: testVersion}
}

func newStore(t *testing.T) Store {
	t.Helper()
	dir := t.TempDir()
	return Store{State: dir, Logs: filepath.Join(dir, "logs")}
}

func mustPlan(t *testing.T, s Store, key string, plan Plan, now time.Time) string {
	t.Helper()
	aid, err := s.WritePlan(key, plan, now)
	if err != nil {
		t.Fatal(err)
	}
	return aid
}

func stateIs(t *testing.T, s Store, aid string, want state) {
	t.Helper()
	if got := stateOf(s.readAttempt(aid)); got != want {
		t.Errorf("試行 %s の状態 = %s, want %s", aid, got, want)
	}
}

func begin(t *testing.T, s Store, key string) Status {
	t.Helper()
	status, _, err := s.Begin(key, time.Unix(0, 0))
	if err != nil {
		t.Fatal(err)
	}
	return status
}

func TestWritePlanIsIdempotent(t *testing.T) {
	s := newStore(t)
	first := mustPlan(t, s, testKey, basePlan(), time.Unix(1, 0))
	other := basePlan()
	other.Targets = []string{"other"}
	if second := mustPlan(t, s, testKey, other, time.Unix(2, 0)); second != first {
		t.Errorf("試行 ID が変わった: %s, %s", first, second)
	}
	if plan, _ := s.ReadPlan(testKey); !slices.Equal(plan.Targets, []string{StopTarget, "p1"}) {
		t.Errorf("計画が書き換わった: %v", plan.Targets)
	}
	stateIs(t, s, first, pending)
}

func TestNonCandidateIsFinalisedByCcnavi(t *testing.T) {
	s := newStore(t)
	aid := mustPlan(t, s, testKey, Plan{Final: FinalOutOfScope, Reason: ReasonExistingNudge}, time.Now())
	a := s.readAttempt(aid)
	if stateOf(a) != done || a.String(keyJudge) != string(judgeCcnavi) || a.String(keyFinal) != string(FinalOutOfScope) {
		t.Errorf("試行 = %s", dumps(t, a))
	}
	if got := begin(t, s, testKey); got != statusNotCandidate {
		t.Errorf("Begin = %s", got)
	}
}

func TestMaterialBeforePlan(t *testing.T) {
	s := newStore(t)
	if status, _, err := s.Begin(testKey, time.Unix(1, 0)); status != StatusWait || err != nil {
		t.Fatalf("Begin = %s, %v", status, err)
	}
	aid := attemptID(testKey, false)
	stateIs(t, s, aid, planWait)
	mustPlan(t, s, testKey, basePlan(), time.Unix(2, 0))
	stateIs(t, s, aid, pending)
	if got := begin(t, s, testKey); got != StatusCandidate {
		t.Errorf("Begin = %s", got)
	}
	stateIs(t, s, aid, running)
	// 遅れて来た material は、実行中の試行を書き換えない。
	if got := begin(t, s, testKey); got != StatusCandidate {
		t.Errorf("2 回目の Begin = %s", got)
	}
	stateIs(t, s, aid, running)
}

func TestIllegalTransitionIsRefused(t *testing.T) {
	s := newStore(t)
	aid := mustPlan(t, s, testKey, basePlan(), time.Now())
	if s.move(s.readAttempt(aid), done) {
		t.Error("pending → done を許した")
	}
}

func TestExpiry(t *testing.T) {
	t.Setenv(env.StopReviewTTLSeconds, "60")
	s := newStore(t)
	aid := mustPlan(t, s, testKey, basePlan(), time.Unix(0, 0))
	if got := s.ExpireOverdue(time.Unix(10, 0)); got != nil {
		t.Errorf("期限内に期限切れ: %v", got)
	}
	if got := s.ExpireOverdue(time.Unix(100, 0)); !slices.Equal(got, []string{aid}) {
		t.Errorf("ExpireOverdue = %v", got)
	}
	a := s.readAttempt(aid)
	if stateOf(a) != expired || a.String(keyFinal) != string(FinalUnchecked) || a.String(keyReason) != string(reasonExpired) {
		t.Errorf("試行 = %s", dumps(t, a))
	}
	if got := begin(t, s, testKey); got != statusExpired {
		t.Errorf("Begin = %s", got)
	}
}

// 期限切れで閉じた試行に遅れて届いた record は、終了可でも不備でも受理しない。
func TestLateRecordAfterExpiryChangesNothing(t *testing.T) {
	s := newStore(t)
	aid := mustPlan(t, s, testKey, basePlan(), time.Unix(0, 0))
	begin(t, s, testKey)
	ttl := config.StopReviewTTL.Value()
	if got := s.ExpireOverdue(time.Unix(0, 0).Add(ttl - time.Second)); got != nil {
		t.Errorf("期限内に期限切れ: %v", got)
	}
	if got := s.ExpireOverdue(time.Unix(0, 0).Add(ttl + time.Second)); !slices.Equal(got, []string{aid}) {
		t.Errorf("ExpireOverdue = %v", got)
	}
	for _, late := range []string{passing(aid, true), flawed(aid)} {
		if out := s.Accept(testKey, accept(t, late)); out != OKReturn {
			t.Errorf("Accept = %+v", out)
		}
		a := s.readAttempt(aid)
		if _, has := a.Get(keyAccepted); stateOf(a) != expired || a.String(keyFinal) != string(FinalUnchecked) || has {
			t.Errorf("試行 = %s", dumps(t, a))
		}
	}
}

func TestEventsAreLoggedApart(t *testing.T) {
	s := newStore(t)
	mustPlan(t, s, testKey, basePlan(), time.Now())
	begin(t, s, testKey)
	data, err := os.ReadFile(filepath.Join(s.Logs, "stop-review", "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	rows := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(rows) != 1 || obj(t, rows[0]).String("event") != string(running) {
		t.Errorf("イベント = %q", data)
	}
	if _, err := os.Stat(filepath.Join(s.Logs, "decisions.jsonl")); !os.IsNotExist(err) {
		t.Errorf("判定の記録に書いた: %v", err)
	}
	// Logs が空なら書かない。
	quiet := Store{State: t.TempDir()}
	mustPlan(t, quiet, testKey, basePlan(), time.Now())
	begin(t, quiet, testKey)
}

func TestPlanWaitExpiresAndALateNonCandidatePlanIsFinalised(t *testing.T) {
	s := newStore(t)
	if _, _, err := s.Begin(testKey, time.Unix(0, 0)); err != nil {
		t.Fatal(err)
	}
	if got := s.ExpireOverdue(time.Unix(1_000_000, 0)); !slices.Equal(got, []string{attemptID(testKey, false)}) {
		t.Errorf("ExpireOverdue = %v", got)
	}
	other := "s1:other"
	if _, _, err := s.Begin(other, time.Unix(0, 0)); err != nil {
		t.Fatal(err)
	}
	aid := mustPlan(t, s, other, Plan{Final: FinalOutOfScope, Reason: ReasonExistingNudge}, time.Now())
	a := s.readAttempt(aid)
	if stateOf(a) != done || a.String(keyFinal) != string(FinalOutOfScope) || a.String(keyJudge) != string(judgeCcnavi) {
		t.Errorf("試行 = %s", dumps(t, a))
	}
}

func TestUnreadableExistingAttemptIsClosedUnchecked(t *testing.T) {
	s := newStore(t)
	aid := attemptID(testKey, false)
	writeFile(t, s.attemptPath(aid), "{broken")
	mustPlan(t, s, testKey, Plan{IsCandidate: true, Targets: []string{StopTarget}}, time.Now())
	a := s.readAttempt(aid)
	if stateOf(a) != done || a.String(keyFinal) != string(FinalUnchecked) || a.String(keyReason) != string(reasonUnreadable) {
		t.Errorf("試行 = %s", dumps(t, a))
	}
	// 判定役は材料を受け取っても判定せず、record は何もせず止めない。
	if out := s.Accept(testKey, AcceptInput{MaterialVersion: "x"}); out != OKReturn {
		t.Errorf("Accept = %+v", out)
	}
	// 次の Stop は別のキーなので、新しい試行として始まる。
	next := testKey + "-next"
	mustPlan(t, s, next, Plan{IsCandidate: true, Targets: []string{StopTarget}}, time.Now())
	stateIs(t, s, attemptID(next, false), pending)
}

func TestUnwritableAttemptIsReported(t *testing.T) {
	dir := t.TempDir()
	blocker := filepath.Join(dir, "file")
	writeFile(t, blocker, "x")
	if _, err := (Store{State: blocker}).WritePlan(testKey, basePlan(), time.Now()); err == nil {
		t.Error("書けないのにエラーにならない")
	}
}

func TestLatePlanForThePreviousStopDoesNotFeedTheNext(t *testing.T) {
	s := newStore(t)
	older, newer := "s1:old", "s1:new"
	mustPlan(t, s, older, Plan{IsCandidate: true, Targets: []string{StopTarget}}, time.Now())
	if got := begin(t, s, newer); got != StatusWait {
		t.Errorf("Begin = %s", got)
	}
	stateIs(t, s, attemptID(older, false), pending)
	stateIs(t, s, attemptID(newer, false), planWait)
	// 最後の行が同じでも、セッションが違えば別の試行。
	mustPlan(t, s, "s1:same", Plan{IsCandidate: true, Targets: []string{StopTarget}}, time.Now())
	if got := begin(t, s, "s2:same"); got != StatusWait {
		t.Errorf("別のセッションの Begin = %s", got)
	}
}
