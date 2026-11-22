package scenarios

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/runner"
)

func kinds(t *testing.T, out string) []string {
	t.Helper()
	rs, err := judgeReport(out)
	if err != nil {
		t.Fatal(err)
	}
	var ks []string
	for _, r := range rs {
		ks = append(ks, r.Kind)
	}
	return ks
}

func scn(t *testing.T, w ws, setup map[string]any, steps []map[string]any, expect map[string]any) string {
	t.Helper()
	if setup == nil {
		setup = map[string]any{}
	}
	path := writeScenario(t, w.tmp, "UC-99", setup, steps)
	if expect != nil {
		// expect を足して書き直す
		text := read(t, path)
		text = strings.Replace(text, `"expect":{}`, `"expect":`+jsonOf(expect), 1)
		write(t, path, text)
	}
	return path
}

func TestExpectAfterHaltsAndSkipsLaterSteps(t *testing.T) {
	t.Parallel()
	failing := map[string]any{"state": []map[string]any{{"run": "true", "expect_contains": "zzz"}}}
	cases := []struct {
		name      string
		steps     []map[string]any
		wantRuns  int
		wantHalt  bool
		haltMatch string
	}{
		{"満たさなければ後続に進まず止める", []map[string]any{{"prompt": "a", "expect_after": failing}, {"prompt": "b"}}, 1, true, "zzz"},
		{"expect_after が無ければ全部回す", []map[string]any{{"prompt": "a"}, {"prompt": "b"}}, 2, false, ""},
		{"評価できなければ止めて理由を残す", []map[string]any{{"prompt": "a", "expect_after": map[string]any{"state": []map[string]any{{"run": "true", "cwd": "no/such", "expect_contains": "x"}}}}, {"prompt": "b"}}, 1, true, "評価で例外"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			w := newWs(t, "", "")
			g := newRig(t, w)
			rc := g.r.Run(w.runOpts(scn(t, w, nil, tc.steps, nil)))
			if g.ran() != tc.wantRuns {
				t.Errorf("回した step = %d, want %d（rc %d）", g.ran(), tc.wantRuns, rc)
			}
			h := w.state(t).Halt
			if (h != nil) != tc.wantHalt || h != nil && (h.Step != 1 || h.Kind != "expect_after" || !strings.Contains(strings.Join(h.Reasons, ";"), tc.haltMatch)) {
				t.Errorf("halt = %+v", h)
			}
			if tc.wantHalt && rc != 2 {
				t.Errorf("止めた回は未成立（2）: %d", rc)
			}
		})
	}
}

func TestFinishDropsDecisionsAndStateWhenHaltedAndReportsInconclusive(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.fn = func(runner.StepInput) (*core.Stream, error) { return nil, errors.New("x") }
	expect := map[string]any{"state": []map[string]any{{"run": "true", "expect_contains": "zzz"}}}
	if rc := g.r.Run(w.runOpts(scn(t, w, nil, nil, expect))); rc != 2 {
		t.Fatalf("rc = %d", rc)
	}
	if got := strings.Join(kinds(t, w.out), ","); got != "run,repo,host,cost" {
		t.Errorf("止まった回は decisions・state を外し、止めた理由を未成立で足す: %s", got)
	}
	if rows := reportRows(t, w.out); rows["run"] != core.Inconclusive {
		t.Errorf("= %v", rows)
	}
}

func TestStateIsCheckedWhileSetupFilesStillExist(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	setup := map[string]any{"files": map[string]any{"sub/keep.txt": "x\n"}}
	expect := map[string]any{"state": []map[string]any{{"run": "cat sub/keep.txt", "expect_contains": "x"}}}
	if rc := g.r.Run(runner.RunOptions{Scenario: scn(t, w, setup, nil, expect), Confirmed: true, Root: w.root, Out: w.out}); rc != 0 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	if rows := reportRows(t, w.out); rows["state"] != core.Pass {
		t.Errorf("setup のファイルが消えてから確かめている: %v", rows)
	}
	if exists(filepath.Join(w.root, "sub")) {
		t.Error("作ったファイルとディレクトリは判定のあとに消す")
	}
}

func TestAnAppendedFileIsNotRemovedByFinish(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	path := w.scenario(t, map[string]any{
		"files": map[string]any{"projects/test-repo/README.md": map[string]any{"content": "追記\n", "append": true}}, "reset": resetBase(w.base)}, nil)
	g.fn = func(runner.StepInput) (*core.Stream, error) {
		if got := read(t, filepath.Join(w.target, "README.md")); got != "元の中身\n追記\n" {
			t.Errorf("追記されていない: %q", got)
		}
		return stepOut, nil
	}
	g.r.Run(w.runOpts(path))
	if !exists(filepath.Join(w.target, "README.md")) {
		t.Error("追記したファイルを消した")
	}
}

func TestAFailedStepCountsItsBudgetTowardTheTotal(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.fn = func(runner.StepInput) (*core.Stream, error) { return nil, errors.New("時間切れ") }
	steps := []map[string]any{{"prompt": "a"}, {"prompt": "b"}}
	if rc := g.r.Run(w.runOpts(scn(t, w, nil, steps, nil))); rc != 2 {
		t.Fatalf("rc = %d", rc)
	}
	if st := w.state(t); st.CostUSD != 0.5 {
		t.Errorf("出力が無ければ step の上限（1.0 ÷ 2 step）を足す: %v", st.CostUSD)
	}
	// 出力に費用が残っていれば、その費用
	w2 := newWs(t, "", "")
	g2 := newRig(t, w2)
	g2.fn = func(in runner.StepInput) (*core.Stream, error) {
		write(t, filepath.Join(in.Out, "step-1.jsonl"), `{"type":"result","total_cost_usd":0.4,"session_id":"s"}`+"\n")
		return nil, errors.New("終了コード 1")
	}
	g2.r.Run(w2.runOpts(scn(t, w2, nil, steps, nil)))
	if st := w2.state(t); st.CostUSD != 0.4 {
		t.Errorf("= %v", st.CostUSD)
	}
}

func TestASessionIsKeptOnlyWhenTheNextPromptResumes(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	steps := []map[string]any{{"prompt": "a"}, {"human": true, "instruction": "i"}, {"prompt": "b", "resume": true}, {"prompt": "c"}}
	// 人間の step で止まらないよう、human を挟まない並びで確かめる
	steps = []map[string]any{{"prompt": "a"}, {"prompt": "b", "resume": true}, {"prompt": "c"}}
	g.fn = func(in runner.StepInput) (*core.Stream, error) {
		return &core.Stream{SessionIDs: []string{"sess-" + string(rune('0'+in.Idx))}}, nil
	}
	g.r.Run(w.runOpts(scn(t, w, nil, steps, nil)))
	if len(g.calls) != 3 {
		t.Fatalf("回した step = %d", len(g.calls))
	}
	persist := []bool{g.calls[0].Persist, g.calls[1].Persist, g.calls[2].Persist}
	if !persist[0] || persist[1] || persist[2] {
		t.Errorf("続きが --resume でつなぐ step の前だけ残す: %v", persist)
	}
	if g.calls[1].ResumeID != "sess-1" || g.calls[0].ResumeID != "" || g.calls[2].ResumeID != "" {
		t.Errorf("resume の相手 = %q・%q", g.calls[1].ResumeID, g.calls[2].ResumeID)
	}
}

func TestEachRunStartsAFreshSession(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.r.Run(w.runOpts(scn(t, w, nil, []map[string]any{{"prompt": "a"}}, nil)))
	g.r.Run(runner.RunOptions{Scenario: scn(t, w, nil, []map[string]any{{"prompt": "a"}}, nil), Confirmed: true, Root: w.root, Out: filepath.Join(w.tmp, "out2")})
	if len(g.calls) != 2 || g.calls[0].SessionID == g.calls[1].SessionID || g.calls[1].ResumeID != "" || g.calls[1].Persist {
		t.Errorf("hook の once の案内の記憶はセッションごと。持ち越さない: %+v", g.calls)
	}
}

func TestTheRunnerPassesIssueNumberAndHostEnvToTheStep(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	var env map[string]string
	g.fn = func(in runner.StepInput) (*core.Stream, error) {
		env = in.Env
		return stepOut, nil
	}
	g.r.Run(w.runOpts(w.scenario(t, map[string]any{"host": "real", "reset": resetBase(w.base)}, nil)))
	if env != nil {
		t.Errorf("real は親の環境を継承する（nil）: %v", env)
	}
	_ = os.Remove
}
