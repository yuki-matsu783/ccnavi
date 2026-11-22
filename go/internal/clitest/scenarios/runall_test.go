package scenarios

import (
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/report"
	"ccnavi/internal/harness/scenario/runner"
)

type batch struct {
	w   ws
	g   *rig
	dir string
}

func newBatch(t *testing.T) batch {
	w := newWs(t, "", "")
	return batch{w: w, g: newRig(t, w), dir: filepath.Join(w.tmp, "scn")}
}

// make はシナリオを 1 本書く。reset=false は host: bare だけ（対象は触るが戻さない）、body は setup なし、human は人間の step。
func (b batch) make(t *testing.T, id string, opts ...string) {
	t.Helper()
	budget, setup := 1.0, map[string]any{
		"git": [][]string{{"git", "switch", "-q", "-c", "feat-" + strings.ToLower(id)}}, "reset": resetBase(b.w.base)}
	steps := []map[string]any{{"prompt": id + " をやる"}}
	for _, o := range opts {
		switch o {
		case "body":
			setup = map[string]any{}
		case "noreset":
			setup = map[string]any{"host": "bare"}
		case "human":
			steps = []map[string]any{{"human": true, "instruction": "打つ"}}
		case "budget0.5":
			budget = 0.5
		case "budget3":
			budget = 3.0
		}
	}
	data := map[string]any{"id": id, "title": "タイトル " + id, "model": "haiku", "budget_usd": budget, "steps": steps, "expect": map[string]any{}, "setup": setup}
	text, _ := json.Marshal(data)
	write(t, filepath.Join(b.dir, id+".yml"), string(text))
}

func (b batch) opts(cap float64, confirmed *string, out string) runner.RunAllOptions {
	return runner.RunAllOptions{Scenarios: []string{b.dir}, MaxTotalUSD: cap, Confirmed: confirmed, Root: b.w.root, Out: out}
}

func (b batch) digest(t *testing.T, cap float64) string {
	t.Helper()
	eligible, _ := b.g.r.RunAllPlan(runner.ExpandScenarioPaths([]string{b.dir}), b.w.root)
	return runner.RunAllDigest(eligible, cap)
}

func ptr(s string) *string { return &s }

func TestRunAllWithoutConfirmationPrintsTheListAndRunsNothing(t *testing.T) {
	t.Parallel()
	b := newBatch(t)
	b.make(t, "UC-91")
	b.make(t, "UC-92", "budget0.5")
	rc := b.g.r.RunAll(b.opts(3, nil, ""))
	text := b.g.text()
	for _, want := range []string{"| UC-91 |", "| UC-92 |", b.w.base[:12], "3 USD", "--confirmed " + b.digest(t, 3)} {
		if !strings.Contains(text, want) {
			t.Errorf("確認文に %q が無い", want)
		}
	}
	if rc != 3 || b.g.ran() != 0 || b.w.git(t, "branch", "--show-current") != "main" {
		t.Errorf("rc = %d", rc)
	}
}

func TestRunAllRunsOnlyDisposableRepoScenariosAndListsTheRestWithReasons(t *testing.T) {
	t.Parallel()
	b := newBatch(t)
	b.make(t, "UC-91")
	b.make(t, "UC-92", "body")
	b.make(t, "UC-93", "noreset")
	b.make(t, "UC-94", "human")
	write(t, filepath.Join(b.dir, "broken.yml"), "id: UC-95\n")
	eligible, skipped := b.g.r.RunAllPlan(runner.ExpandScenarioPaths([]string{b.dir}), b.w.root)
	if len(eligible) != 1 || eligible[0].Scn.ID != "UC-91" {
		t.Fatalf("対象 = %v", eligible)
	}
	why := map[string]string{}
	for _, s := range skipped {
		why[s.Who] = s.Why
	}
	for id, want := range map[string]string{"UC-92": "ccnavi 本体に作用しうる", "UC-93": "setup.reset が無い", "UC-94": "人間の操作"} {
		if !strings.Contains(why[id], want) {
			t.Errorf("%s の理由 = %q", id, why[id])
		}
	}
	found := false
	for who, y := range why {
		found = found || strings.Contains(who, "broken") && strings.Contains(y, "読み込めない")
	}
	if !found {
		t.Errorf("読めないファイルは理由つきで外す: %v", why)
	}
}

func TestRunAllRefusesTheBodyAsATarget(t *testing.T) {
	t.Parallel()
	b := newBatch(t)
	b.make(t, "UC-91")
	if err := os.RemoveAll(filepath.Join(b.w.target, ".git")); err != nil { // 対象がリポジトリでなくなると、本体のものになる
		t.Fatal(err)
	}
	eligible, skipped := b.g.r.RunAllPlan(runner.ExpandScenarioPaths([]string{b.dir}), b.w.root)
	if len(eligible) != 0 || len(skipped) != 1 || !strings.Contains(skipped[0].Why, "ccnavi 本体") {
		t.Errorf("= %v %v", eligible, skipped)
	}
}

func TestRunAllConfirmedRunsInOrderAndContinuesAfterAFailure(t *testing.T) {
	t.Parallel()
	b := newBatch(t)
	b.make(t, "UC-92")
	b.make(t, "UC-91")
	b.make(t, "UC-93")
	var seen []string
	b.g.fn = func(in runner.StepInput) (*core.Stream, error) {
		seen = append(seen, in.Scn.ID)
		switch in.Scn.ID {
		case "UC-91":
			return &core.Stream{CostUSD: 0.25}, nil
		case "UC-92":
			return nil, errors.New("起動できない")
		}
		return stepOut, nil
	}
	batchOut := filepath.Join(b.w.tmp, "batch")
	rc := b.g.r.RunAll(b.opts(5, ptr(b.digest(t, 5)), batchOut))
	if strings.Join(seen, ",") != "UC-91,UC-92,UC-93" || rc != 2 {
		t.Fatalf("seen = %v・rc = %d\n%s", seen, rc, b.g.text())
	}
	data := read(t, filepath.Join(batchOut, "summary.json"))
	var raw map[string]any
	_ = json.Unmarshal([]byte(data), &raw)
	by := map[string]map[string]any{}
	for _, s := range raw["scenarios"].([]any) {
		m := s.(map[string]any)
		by[m["id"].(string)] = m
	}
	// 失敗した step は出力が無いので、step の上限（1.0）を足す。累計を少なく見せない
	if by["UC-91"]["cost_usd"] != 0.25 || by["UC-92"]["cost_usd"] != 1.0 || raw["total_cost_usd"] != 1.25 || by["UC-92"]["verdict"] != core.Inconclusive {
		t.Errorf("summary = %s", data)
	}
	for _, k := range []string{"tool_calls", "denies", "asks"} {
		if _, ok := by["UC-91"][k]; !ok {
			t.Errorf("%s が無い", k)
		}
	}
	if md := read(t, filepath.Join(batchOut, "summary.md")); !strings.Contains(md, "| ID | 判定 | 実費 (USD) | ツール呼び出し | 拒否 | ask |") || !strings.Contains(md, "| UC-91 |") {
		t.Error("summary.md の表が違う")
	}
	rs, _ := judgeReport(filepath.Join(batchOut, "UC-91"))
	for _, r := range rs {
		if r.Kind == "cost" && (r.CostUSD == nil || *r.CostUSD != 0.25) {
			t.Errorf("実費は各回の report.json にも残る: %+v", r)
		}
	}
	if b.w.git(t, "rev-parse", "main") != b.w.base || b.w.git(t, "branch", "--show-current") != "main" {
		t.Error("どの回も、対象は base に戻っている")
	}
}

func TestRunAllStopsWhenTheSetChangedOrAnythingConfirmedChanges(t *testing.T) {
	t.Parallel()
	b := newBatch(t)
	b.make(t, "UC-91")
	b.make(t, "UC-92")
	confirmed := b.digest(t, 5)
	// 確認のあとに増えた
	b.make(t, "UC-93")
	if rc := b.g.r.RunAll(b.opts(5, &confirmed, "")); rc != 3 || b.g.ran() != 0 || !contains(b.g.text(), "確認した内容と今回の対象が違う", "UC-91, UC-92, UC-93") {
		t.Errorf("増えた: rc = %d\n%s", rc, b.g.text())
	}
	if err := os.Remove(filepath.Join(b.dir, "UC-93.yml")); err != nil {
		t.Fatal(err)
	}
	prev := confirmed
	mutate := map[string]func(t *testing.T){
		"費用の上限": func(*testing.T) {},
		"permission_mode": func(t *testing.T) {
			rewrite(t, filepath.Join(b.dir, "UC-91.yml"), func(m map[string]any) { m["permission_mode"] = "bypassPermissions" })
		},
		"概要に出ない prompt の後ろ": func(t *testing.T) {
			rewrite(t, filepath.Join(b.dir, "UC-91.yml"), func(m map[string]any) {
				m["steps"].([]any)[0].(map[string]any)["prompt"] = "UC-91 をやる" + strings.Repeat("x", 200)
			})
		},
		"base": func(t *testing.T) {
			b.w.git(t, "commit", "--allow-empty", "-q", "-m", "next")
			other := b.w.git(t, "rev-parse", "HEAD")
			rewrite(t, filepath.Join(b.dir, "UC-92.yml"), func(m map[string]any) { m["setup"].(map[string]any)["reset"] = map[string]any{"base": other} })
		},
		"減った": func(t *testing.T) { _ = os.Remove(filepath.Join(b.dir, "UC-92.yml")) },
	}
	cap := 5.0
	for _, name := range []string{"費用の上限", "permission_mode", "概要に出ない prompt の後ろ", "base", "減った"} {
		if name == "費用の上限" {
			cap = 4.0
		}
		mutate[name](t)
		now := b.digest(t, cap)
		if now == prev {
			t.Errorf("%s が変わっても目印が変わらない", name)
		}
		prev = now // 直前の状態と比べる。あとの変更が先の変更を隠さない
	}
}

func rewrite(t *testing.T, path string, f func(map[string]any)) {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(read(t, path)), &m); err != nil {
		t.Fatal(err)
	}
	f(m)
	data, _ := json.Marshal(m)
	write(t, path, string(data))
}

func TestRunAllStopsWhenTheNextBudgetExceedsWhatIsLeft(t *testing.T) {
	t.Parallel()
	b := newBatch(t)
	b.make(t, "UC-91")
	b.make(t, "UC-92")
	b.make(t, "UC-93")
	var seen []string
	b.g.fn = func(in runner.StepInput) (*core.Stream, error) {
		seen = append(seen, in.Scn.ID)
		return &core.Stream{CostUSD: 1.0}, nil
	}
	out := filepath.Join(b.w.tmp, "batch")
	rc := b.g.r.RunAll(b.opts(2.5, ptr(b.digest(t, 2.5)), out))
	var summary map[string]any
	_ = json.Unmarshal([]byte(read(t, filepath.Join(out, "summary.json"))), &summary)
	if rc != 3 || strings.Join(seen, ",") != "UC-91,UC-92" || !strings.Contains(summary["stopped"].(string), "UC-93") ||
		len(summary["remaining"].([]any)) != 1 || !contains(b.g.text(), "残りの ID: UC-93") {
		t.Errorf("rc = %d・seen = %v\n%v", rc, seen, summary)
	}
}

func TestRunAllRunsNothingWhenOneBudgetIsOverTheCapOrTheCapIsInvalidOrTheModeIsNotEnable(t *testing.T) {
	t.Parallel()
	b := newBatch(t)
	b.make(t, "UC-91", "budget3")
	if rc := b.g.r.RunAll(b.opts(2, ptr(b.digest(t, 2)), filepath.Join(b.w.tmp, "batch"))); rc != 3 || b.g.ran() != 0 {
		t.Errorf("1 本の上限が累計を超える: rc = %d", rc)
	}
	for _, bad := range []float64{0, -1, math.NaN(), math.Inf(1)} {
		if rc := b.g.r.RunAll(b.opts(bad, nil, "")); rc != 2 {
			t.Errorf("上限 %v は断る: rc = %d", bad, rc)
		}
	}
	b.g.r.Getenv = func(string) string { return "dry-run" }
	if rc := b.g.r.RunAll(b.opts(5, ptr(b.digest(t, 5)), "")); rc != 3 || b.g.ran() != 0 {
		t.Errorf("enable でなければ回さない: rc = %d", rc)
	}
}

func TestRunAllConfirmationRaisesTheHostQuestionForIssueScenarios(t *testing.T) {
	t.Parallel()
	b := newBatch(t)
	b.make(t, "UC-91")
	rewrite(t, filepath.Join(b.dir, "UC-91.yml"), func(m map[string]any) {
		m["setup"].(map[string]any)["issue"] = map[string]any{"repo": "projects/test-repo", "title": "t", "body": "b"}
	})
	eligible, skipped := b.g.r.RunAllPlan(runner.ExpandScenarioPaths([]string{filepath.Join(b.dir, "UC-91.yml")}), b.w.root)
	if text := report.RunAllConfirmation(eligible, skipped, 5, "d", "m", b.w.root, []string{"x"}); !strings.Contains(text, "本物のホストに issue も作る") {
		t.Error("issue のシナリオは、本物のホストに issue も作ると確認する")
	}
}

func TestJudgeAppendsOnceAndRecordsAcceptance(t *testing.T) {
	t.Parallel()
	table := "| 基準 | 判定 | 理由 |\n|---|---|---|\n| a | pass | r1 |\n| b | pass | r2 |\n"
	w := newWs(t, "", "")
	g := newRig(t, w)
	root := w.tmp
	scnPath := writeScenario(t, root, "UC-99", map[string]any{}, nil)
	rewrite(t, scnPath, func(m map[string]any) { m["expect"] = map[string]any{"judge": []string{"a", "b"}} })
	newOut := func(name string) string {
		out := filepath.Join(root, name)
		write(t, filepath.Join(out, "report.md"), "# UC-99\n\n全体: needs-review\n")
		st := &core.State{Scenario: scnPath, Agent: core.AgentClaude}
		if err := core.Save(out, st); err != nil {
			t.Fatal(err)
		}
		write(t, filepath.Join(out, "report.json"), `[{"kind":"decisions","expect":{},"verdict":"pass","why":"x"},{"kind":"judge","expect":"a","verdict":"needs-review","why":""},{"kind":"judge","expect":"b","verdict":"needs-review","why":""}]`)
		return out
	}
	file := filepath.Join(root, "judge.md")
	write(t, file, table)

	out := newOut("o1")
	if rc := g.r.Judge(out, file, true); rc != 0 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	text := read(t, filepath.Join(out, "report.md"))
	if !contains(text, "## 意味の判定（別のエージェント）", "ユーザの受け入れ: あり", "全体（機械判定と意味の判定の合わせ）: pass") {
		t.Errorf("= %s", text)
	}
	if rc := g.r.Judge(out, file, true); rc != 1 {
		t.Error("受け入れの二重記録は断る")
	}

	// 先に表だけ足し、あとから受け入れを記す
	out = newOut("o2")
	if rc := g.r.Judge(out, file, false); rc != 0 || !strings.Contains(read(t, filepath.Join(out, "report.md")), "ユーザの受け入れ: なし") {
		t.Fatal("表だけ足せない")
	}
	if rc := g.r.Judge(out, "", true); rc != 0 {
		t.Errorf("足し済みの回の受け入れに表のファイルは要らない: rc = %d", rc)
	}
	text = read(t, filepath.Join(out, "report.md"))
	if strings.Count(text, "## 意味の判定（別のエージェント）") != 1 || !contains(text, "ユーザの受け入れ: あり", "全体（機械判定と意味の判定の合わせ）: pass") {
		t.Errorf("= %s", text)
	}
	if rc := g.r.Judge(out, "", true); rc != 1 {
		t.Error("二重には記さない")
	}
	// 表の行数が基準と違えば足さない
	out = newOut("o3")
	write(t, file, "| 基準 | 判定 | 理由 |\n|---|---|---|\n| a | pass | r |\n")
	if rc := g.r.Judge(out, file, false); rc != 1 {
		t.Error("行数が違う")
	}
}
