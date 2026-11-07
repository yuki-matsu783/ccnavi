package stopreview

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/fsio"
)

type evalFixture struct {
	t     *testing.T
	state string
	logs  string
}

func newEval(t *testing.T) evalFixture {
	dir := t.TempDir()
	return evalFixture{t, filepath.Join(dir, "state"), filepath.Join(dir, "logs")}
}

// attempt は試行を直に置く。extra は JSON のキーと値の続き（`"state": "consumed"` など）。
func (f evalFixture) attempt(aid, final string, results string, extra ...string) {
	fields := []string{
		fmt.Sprintf(`"attempt_id": %q`, aid), fmt.Sprintf(`"key": "s1:%s"`, aid), `"state": "done"`,
		`"judge": "ai"`, `"accepted": true`, `"results": ` + results,
	}
	if final != "" {
		fields = append(fields, fmt.Sprintf(`"final": %q`, final))
	}
	// 後から足したキーが前のキーを上書きする（Python の dict.update と同じ）。
	text := "{" + strings.Join(append(fields, extra...), ", ") + "}"
	o := obj(f.t, text)
	writeFile(f.t, filepath.Join(f.state, AttemptPrefix+aid+fsio.JSONExt), dumps(f.t, o))
}

func (f evalFixture) labels(rows ...string) {
	writeFile(f.t, logPath(f.logs, labelsFile), strings.Join(rows, "\n")+"\n")
}

func labelRow(aid, criterion, target, truth string) string {
	return fmt.Sprintf(`{"attempt_id": %q, "criterion": %q, "target": %q, "truth": %q}`, aid, criterion, target, truth)
}

func (f evalFixture) criterion(s summary, name string) criterionSummary {
	for _, c := range s.byCriteria {
		if c.name == name {
			return c
		}
	}
	f.t.Fatalf("%s が無い", name)
	return criterionSummary{}
}

func TestEvalMetrics(t *testing.T) {
	f := newEval(t)
	flaw := `[{"criterion": "R1", "target": "stop", "result": "fail"}, {"criterion": "R3", "target": "stop", "result": "pass"}]`
	f.attempt("a1", "flawed", flaw)    // 不備あり、検出した
	f.attempt("a2", "unchecked", "[]") // 不備あり、見逃した
	f.attempt("a3", "flawed", flaw)    // 不備なしなのに検出した
	f.attempt("a4", "ok", `[{"criterion": "R1", "target": "stop", "result": "pass"}]`)
	f.attempt("a5", "out_of_scope", "[]", `"judge": "ccnavi"`)
	f.attempt("a6", "out_of_scope", "[]")
	f.attempt("a7", "skipped", "[]", `"judge": "ccnavi"`)
	f.labels(labelRow("a1", "R1", "stop", "flaw"), labelRow("a2", "R1", "stop", "flaw"), labelRow("a3", "R1", "stop", "ok"), labelRow("a4", "R1", "STOP", "ok"))
	s := summarize(f.state, f.logs)
	r1 := f.criterion(s, "R1")
	if r1.unit != "stop" || r1.missRate != (rate{0.5, true}) || r1.falseBlockRate != (rate{0.5, true}) || r1.needless != (rate{0.5, true}) {
		t.Errorf("R1 = %+v", r1)
	}
	if r2 := f.criterion(s, "R2"); r2.missRate.known {
		t.Errorf("評価が無いのに見逃し率がある: %+v", r2)
	}
	if f.criterion(s, "R4").unit != "proposal_version" {
		t.Error("R4 の単位が提案の版でない")
	}
	if c := s.counts; c.ccnaviOutOfScope != 1 || c.aiOutOfScope != 1 || c.skipped != 1 {
		t.Errorf("counts = %+v", c)
	}
	if s.coverage != (rate{0.75, true}) {
		t.Errorf("coverage = %+v", s.coverage)
	}
}

func TestEvalFixRateAndCounters(t *testing.T) {
	f := newEval(t)
	f.attempt("b1", "flawed", "[]", `"state": "consumed"`, `"rejudge": "resolved"`, `"sent_at": 1`)
	f.attempt("b2", "flawed", "[]", `"state": "consumed"`, `"rejudge": "unresolved"`, `"sent_at": 1`)
	f.attempt("b3", "flawed", "[]", `"state": "sent_back"`, `"sent_at": 1`)
	f.attempt("b5", "flawed", "[]", `"state": "consumed"`, `"rejudge": "inconclusive"`, `"sent_at": 1`)
	f.attempt("b4", "unchecked", "[]", `"accepted": false`, `"tainted": true`)
	s := summarize(f.state, f.logs)
	if s.fix != (fix{sentBack: 4, resolved: 1, unresolved: 1, inconclusive: 1, notRejudged: 1}) {
		t.Errorf("fix = %+v", s.fix)
	}
	if s.fixRate != (rate{0.25, true}) || s.counts.rejected != 1 || s.counts.tainted != 1 {
		t.Errorf("summary = %+v", s)
	}
}

func TestEvalSeparateCounts(t *testing.T) {
	f := newEval(t)
	// 期限で閉じた差し戻しは、不備検出として数える。
	f.attempt("a", "flawed", `[{"criterion": "R1", "target": "stop", "result": "fail"}]`, `"state": "expired"`, `"sent_at": 1`)
	// 再判定の試行は別の Stop として数えない。
	f.attempt("b", "ok", "[]", `"original": "a"`)
	f.attempt("c", "", "[]", `"state": "running"`)
	s := summarize(f.state, f.logs)
	if s.attempts != 2 || s.counts.flawed != 1 || s.counts.expired != 0 || s.counts.inProgress != 1 || f.criterion(s, "R1").flagged != 1 {
		t.Errorf("summary = %+v", s)
	}
}

func TestEvalSurvivesBrokenRowsAndLabels(t *testing.T) {
	f := newEval(t)
	f.attempt("a", "ok", `[{"target": "stop"}, "x", {"criterion": "R1"}]`)
	f.labels(
		`{"attempt_id": "a", "criterion": "R1", "target": ["stop"], "truth": "flaw"}`,
		labelRow("a", "R1", "STOP", "FLAW"),
		labelRow("a", "R1", "STOP", "ok"),
		labelRow("a", "R1", "stop", "ok"),
	)
	if got := f.criterion(summarize(f.state, f.logs), "R1").labeled; got != 1 {
		t.Errorf("labeled = %d（重複と不正を除く）", got)
	}
}

func TestEvalReadsRotatedEvents(t *testing.T) {
	f := newEval(t)
	for _, name := range []string{"events.jsonl", "events.20260101-000000.jsonl"} {
		writeFile(t, filepath.Join(f.logs, "stop-review", name), `{"event": "suspected_unrecorded_stop"}`+"\n")
	}
	if s := summarize(f.state, f.logs); s.suspected != 2 {
		t.Errorf("suspected = %d", s.suspected)
	}
}

func TestReport(t *testing.T) {
	f := newEval(t)
	text := Report(f.state, f.logs)
	for _, want := range []string{"試行 0 件。検査カバー率 不明。", "待ち時間（計画の作成から判定の完了まで）: 不明", "費用: 不明", "未連携 0"} {
		if !strings.Contains(text, want) {
			t.Errorf("Report に %q が無い:\n%s", want, text)
		}
	}
	f.attempt("a", "ok", "[]", `"created": 10`, `"done_at": 12.5`)
	f.attempt("b", "ok", "[]", `"created": 10`, `"done_at": 11`)
	if text := Report(f.state, f.logs); !strings.Contains(text, "中央値 2.5 秒、最大 2.5 秒（2 件）") || !strings.Contains(text, "検査カバー率 100.0%") {
		t.Errorf("Report:\n%s", text)
	}
}
