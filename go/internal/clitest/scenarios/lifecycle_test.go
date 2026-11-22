package scenarios

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/report"
	"ccnavi/internal/harness/scenario/runner"
	"ccnavi/internal/harness/scenario/world"
)

// setupFor は追記・git・reset を持つシナリオを書く。
func setupFor(t *testing.T, w ws, steps []map[string]any, name ...string) string {
	return w.scenario(t, map[string]any{
		"files": map[string]any{"projects/test-repo/README.md": map[string]any{"content": "追記\n", "append": true}},
		"git":   [][]string{{"git", "switch", "-q", "-c", "feat"}},
		"reset": resetBase(w.base),
	}, steps, name...)
}

var humanSteps = []map[string]any{{"prompt": "a"}, {"human": true, "instruction": "打つ"}}

func TestPreStateIsMadeAndFinishRestoresIt(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	w.dirty(t)
	g := newRig(t, w)
	seen := map[string]any{}
	g.fn = func(runner.StepInput) (*core.Stream, error) {
		seen["branch"] = w.git(t, "branch", "--show-current")
		seen["readme"] = read(t, filepath.Join(w.target, "README.md"))
		seen["lock"] = w.gitDirNames(t)[world.LockName]
		return stepOut, nil
	}
	rc := g.r.Run(w.runOpts(setupFor(t, w, nil)))
	if seen["branch"] != "feat" || seen["readme"] != "元の中身\n追記\n" || seen["lock"] != true {
		t.Errorf("回す前の状態 = %v", seen)
	}
	if rc != 0 && rc != 2 || !w.restored(t) {
		t.Errorf("rc = %d・restored = %v\n%s", rc, w.restored(t), g.text())
	}
	if names := w.gitDirNames(t); names[world.LockName] || names[world.LeftName] {
		t.Errorf("ロックか残し印が残っている: %v", names)
	}
}

func TestAPanicInAStepRestoresAndRepanics(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.fn = func(runner.StepInput) (*core.Stream, error) { panic("割り込み") }
	if p := panics(func() { g.r.Run(w.runOpts(setupFor(t, w, nil))) }); p != "割り込み" {
		t.Fatalf("元の panic を投げ直す: %v", p)
	}
	if !w.restored(t) || w.gitDirNames(t)[world.LockName] {
		t.Error("対象を戻して放していない")
	}
}

func TestAFailedStepHaltsRestoresAndNextAfterwardsIsHarmless(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.fn = func(runner.StepInput) (*core.Stream, error) { return nil, errors.New("起動できない") }
	steps := []map[string]any{{"prompt": "a"}, {"prompt": "b"}}
	if rc := g.r.Run(w.runOpts(setupFor(t, w, steps))); rc != 2 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	if g.ran() != 1 || !w.restored(t) {
		t.Errorf("後続は回さず、対象を戻す（回した step %d）", g.ran())
	}
	if h := w.state(t).Halt; h == nil || h.Kind != "run" || h.Step != 1 {
		t.Errorf("halt = %+v", h)
	}
	if rc := g.r.Next(w.out); rc != 2 || !w.restored(t) || g.ran() != 1 {
		t.Errorf("止めた回の next は何もしない: rc = %d", rc)
	}
}

func TestNextAfterAHaltThatNeverReachedFinishRestores(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	steps := humanSteps
	if rc := g.r.Run(w.runOpts(setupFor(t, w, steps))); rc != 10 {
		t.Fatalf("rc = %d", rc)
	}
	// finish に届かないまま halt だけ残った回（人間の操作で止めて残した状態に halt を足す）
	st := w.state(t)
	st.Halt = &core.Halt{Step: 1, Kind: "run", Reasons: []string{"x"}}
	if err := core.Save(w.out, st); err != nil {
		t.Fatal(err)
	}
	if w.restored(t) {
		t.Fatal("残してあるはず")
	}
	if rc := g.r.Next(w.out); rc != 2 || !w.restored(t) {
		t.Errorf("rc = %d・restored = %v", rc, w.restored(t))
	}
}

func TestAHumanStepLeavesTheStateUntilNextFinishes(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	if rc := g.r.Run(w.runOpts(setupFor(t, w, humanSteps))); rc != 10 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	names := w.gitDirNames(t)
	if w.git(t, "branch", "--show-current") != "feat" || !strings.Contains(read(t, filepath.Join(w.target, "README.md")), "追記") || names[world.LockName] || !names[world.LeftName] {
		t.Error("対象を戻さずに残し、ロックだけ放す")
	}
	for _, want := range []string{w.base + " に戻していない", "次: 続けるなら `" + core.Runner + " next " + w.out + "`", "やめるなら `" + core.Runner + " restore " + w.out + "`"} {
		if !strings.Contains(g.text(), want) {
			t.Errorf("案内に %q が無い", want)
		}
	}
	// 残した状態に、別の回は入れない
	other := w.scenario(t, map[string]any{"reset": resetBase(w.base)}, nil, "UC-98-y.yml")
	if rc := g.r.Run(runner.RunOptions{Scenario: other, Confirmed: true, Root: w.root, Out: filepath.Join(w.tmp, "out2")}); rc != 3 || w.git(t, "branch", "--show-current") != "feat" {
		t.Errorf("別の回: rc = %d", rc)
	}
	// next が最後まで終わると戻る
	if rc := g.r.Next(w.out); rc != 0 || !w.restored(t) {
		t.Errorf("next: rc = %d\n%s", rc, g.text())
	}
	if names := w.gitDirNames(t); names[world.LeftName] || names[world.LockName] {
		t.Errorf("印が残っている: %v", names)
	}
}

func TestRestoreAbandonsAHeldRunAndKeepsAFileMadeLater(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	path := w.scenario(t, map[string]any{"files": map[string]any{"projects/test-repo/new.txt": "x\n"}, "reset": resetBase(w.base)}, humanSteps)
	if rc := g.r.Run(w.runOpts(path)); rc != 10 {
		t.Fatalf("rc = %d", rc)
	}
	if rc := g.r.Restore(w.out); rc != 0 || !w.restored(t) || w.gitDirNames(t)[world.LeftName] {
		t.Errorf("restore: rc = %d", rc)
	}
}

func TestRestoreClosesNothingItDidNotMakeAndFinishDoesNotDeleteALaterFile(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	path := w.scenario(t, map[string]any{"files": map[string]any{"projects/test-repo/new.txt": "x\n"}, "reset": resetBase(w.base)}, nil)
	if rc := g.r.Run(w.runOpts(path)); rc != 0 && rc != 2 {
		t.Fatalf("rc = %d", rc)
	}
	later := filepath.Join(w.target, "new.txt")
	write(t, later, "あとから作った\n")
	if st := w.state(t); len(st.SetupMade) != 0 {
		t.Errorf("finish が setup の印を空にしていない: %v", st.SetupMade)
	}
	g.r.Restore(w.out)
	if !exists(later) {
		t.Error("restore が、あとから作られたファイルを消した")
	}
}

func TestAnUnrestorableTargetGivesExit3AfterTheReportAndBlocksTheNextRun(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.fn = func(in runner.StepInput) (*core.Stream, error) { // 消せないワークツリーを作って、戻せなくする
		locked := filepath.Join(w.root, "logs", "locked")
		run.Git(t, w.target, "worktree", "add", "-q", "--detach", locked, w.base)
		run.Git(t, w.target, "worktree", "lock", locked)
		return stepOut, nil
	}
	if rc := g.r.Run(w.runOpts(setupFor(t, w, nil))); rc != 3 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	if !exists(filepath.Join(w.out, "report.md")) {
		t.Error("レポートは書いてから 3 を返す")
	}
	for _, want := range []string{"手作業で戻す必要がある", "次: "} {
		if !strings.Contains(g.text(), want) {
			t.Errorf("文面に %q が無い", want)
		}
	}
	if names := w.gitDirNames(t); !names[world.LeftName] || names[world.LockName] {
		t.Errorf("残し印を書きロックは放す: %v", names)
	}
	other := w.scenario(t, map[string]any{"reset": resetBase(w.base)}, nil, "UC-98-y.yml")
	if rc := g.r.Run(runner.RunOptions{Scenario: other, Confirmed: true, Root: w.root, Out: filepath.Join(w.tmp, "out2")}); rc != 3 {
		t.Errorf("戻せていない対象では次の回を始めさせない: rc = %d", rc)
	}
	rows := reportRows(t, w.out)
	if rows["reset"] != core.Fail {
		t.Errorf("reset の行が fail でない: %v", rows)
	}
}

func TestAPanicWithAnUnrestorableTargetReportsAndRepanicsTheOriginal(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.fn = func(runner.StepInput) (*core.Stream, error) {
		locked := filepath.Join(w.root, "logs", "locked")
		run.Git(t, w.target, "worktree", "add", "-q", "--detach", locked, w.base)
		run.Git(t, w.target, "worktree", "lock", locked)
		panic("割り込み")
	}
	if p := panics(func() { g.r.Run(w.runOpts(setupFor(t, w, nil))) }); p != "割り込み" {
		t.Errorf("panic = %v", p)
	}
	if !strings.Contains(g.text(), "手作業で戻す必要がある") {
		t.Errorf("戻せなかったことを言う:\n%s", g.text())
	}
}

func TestASecondRunOnALockedTargetIsRefusedBeforeTouchingIt(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	w.dirty(t)
	if _, err := world.AcquireTarget(w.target, w.out, false); err != nil {
		t.Fatal(err)
	}
	g := newRig(t, w)
	rc := g.r.Run(runner.RunOptions{Scenario: setupFor(t, w, nil), Confirmed: true, Root: w.root, Out: filepath.Join(w.tmp, "out2")})
	if rc != 3 || g.ran() != 0 || read(t, filepath.Join(w.target, "README.md")) != "元の中身\n未コミット\n" {
		t.Errorf("rc = %d・step %d 回\n%s", rc, g.ran(), g.text())
	}
}

func TestBadSetupIsStoppedBeforeAnythingIsTouched(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	// 追記の対象が無い: clash の判定で、何も触る前に止まる
	missing := w.scenario(t, map[string]any{
		"files": map[string]any{"projects/test-repo/none.md": map[string]any{"content": "x", "append": true}}, "reset": resetBase(w.base)}, nil)
	if rc := g.r.Run(w.runOpts(missing)); rc != 1 || w.gitDirNames(t)[world.LockName] {
		t.Errorf("追記の対象が無い: rc = %d", rc)
	}
	// 追記の対象が管理下でない（確認文の前に断る）
	write(t, filepath.Join(w.target, "untracked.txt"), "u")
	untracked := w.scenario(t, map[string]any{
		"files": map[string]any{"projects/test-repo/untracked.txt": map[string]any{"content": "x", "append": true}}, "reset": resetBase(w.base)}, nil)
	if rc := g.r.Run(runner.RunOptions{Scenario: untracked, Root: w.root, Out: w.out}); rc != 3 || exists(w.out) {
		t.Errorf("使えない追記の対象: rc = %d", rc)
	}
	// base がコミットとして無い
	unknown := w.scenario(t, map[string]any{"reset": resetBase(strings.Repeat("0", 40))}, nil)
	if rc := g.r.Run(w.runOpts(unknown)); rc != 3 || !strings.Contains(g.text(), "次: ") || w.git(t, "branch", "--show-current") != "main" {
		t.Errorf("未知の base: rc = %d", rc)
	}
	if g.ran() != 0 {
		t.Error("claude を呼んだ")
	}
}

func TestRunRefusesTheBodyWithExit3BeforeAnythingIsMade(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	if err := os.Rename(filepath.Join(w.target, ".git"), filepath.Join(w.tmp, "gone-git")); err != nil {
		t.Fatal(err)
	}
	g := newRig(t, w)
	rc := g.r.Run(w.runOpts(w.scenario(t, map[string]any{"reset": resetBase(w.base)}, nil)))
	if rc != 3 || g.ran() != 0 || exists(w.out) || !strings.Contains(g.text(), "次: ") {
		t.Errorf("rc = %d\n%s", rc, g.text())
	}
}

func TestAFailedSetupGitRollsBackAndExits3(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	path := w.scenario(t, map[string]any{
		"files": map[string]any{"projects/test-repo/README.md": map[string]any{"content": "x\n", "append": true}},
		"git":   [][]string{{"git", "checkout", "nonexistent"}}, "reset": resetBase(w.base)}, nil)
	rc := g.r.Run(w.runOpts(path))
	if rc != 3 || g.ran() != 0 || !strings.Contains(g.text(), "次: ") || !w.restored(t) || w.gitDirNames(t)[world.LockName] {
		t.Errorf("rc = %d\n%s", rc, g.text())
	}
}

func TestConfirmationShowsTheTargetAndAsksAboutTheReset(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	scn, err := core.LoadFile(setupFor(t, w, nil), core.AgentClaude)
	if err != nil {
		t.Fatal(err)
	}
	text := report.ConfirmationText(scn, w.root, "MODE OK", g.r.Manager)
	for _, want := range []string{"対象のリポジトリ: " + filepath.Clean(w.target) + "（使い捨てと確かめた）", "確認事項 3", w.base} {
		if !strings.Contains(text, want) {
			t.Errorf("確認文に %q が無い", want)
		}
	}
	if body := report.ConfirmationText(scn, w.target, "MODE OK", g.r.Manager); !strings.Contains(body, "対象のリポジトリを使えない") {
		t.Error("対象が本体になっていれば、使えないと言う")
	}
}

func TestPreflightShowsTheConfirmationAndNeverRunsClaude(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	path := w.scenario(t, map[string]any{}, nil)
	if rc := g.r.Preflight(path, w.root); rc != 3 || g.ran() != 0 || !strings.Contains(g.text(), "# 1. 事実") {
		t.Errorf("rc = %d", rc)
	}
}

func TestRealClaudeIsNotCalledWithoutTheEntry(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.r.Step = nil // 差し替え口なし・入口でもない
	if rc := g.r.Run(w.runOpts(w.scenario(t, map[string]any{}, nil))); rc == 0 {
		t.Error("入口でなければ、本物の claude を呼ぶ道は閉じる")
	}
	if _, err := g.r.RunStep(runner.StepInput{Scn: &core.Scenario{}, Step: core.Step{Prompt: "x"}, Out: w.tmp, Idx: 1, Root: w.tmp}); err == nil {
		t.Error("RunStep も同じ")
	}
	if rc := g.r.RunAll(runner.RunAllOptions{Scenarios: []string{w.tmp}, MaxTotalUSD: 1, Root: w.root}); rc == 0 {
		t.Error("run-all も同じ")
	}
}

// reportRows は report.json の 種類→判定。
func reportRows(t *testing.T, out string) map[string]string {
	t.Helper()
	rs, err := judgeReport(out)
	if err != nil {
		t.Fatal(err)
	}
	m := map[string]string{}
	for _, r := range rs {
		m[r.Kind] = r.Verdict
	}
	return m
}
