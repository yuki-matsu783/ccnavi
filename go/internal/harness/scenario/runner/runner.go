package runner

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/harness/scenario/actors"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/harness/scenario/judge"
	"ccnavi/internal/harness/scenario/prepare"
	"ccnavi/internal/harness/scenario/report"
	"ccnavi/internal/harness/scenario/world"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

// StepTimeout は claude 1 回（1 step）の上限。
const StepTimeout = 900 * time.Second

// 終了コード。
const (
	ExitHalted = 11 // step を止めた（finish に回す）
	ExitHuman  = 10 // 人間の操作で止まった
)

// StepInput は step 1 つを回す入力。Env が nil なら親の環境を継承する（setup.host が real のとき）。
type StepInput struct {
	Scn       *core.Scenario
	Step      core.Step
	Out       string
	Idx       int // 1 始まり
	SessionID string
	ResumeID  string
	Persist   bool
	Root      string
	IssueNo   int
	Env       map[string]string
}

// StepFunc は step 1 つを回して、出力（stream-json）を読んだ結果を返す。
type StepFunc func(in StepInput) (*core.Stream, error)

// Runner は実行器。欄は呼び手（入口の実行ファイル・テスト）が決める。
type Runner struct {
	// RepoRoot はこのリポジトリの作業ツリー（使い捨てのワークスペースへ写す元。出力の置き場の既定の親）。
	RepoRoot string
	// LogsDir は run・run-all・run-stub の出力の既定の置き場。空なら RepoRoot/logs/scenarios。
	LogsDir string
	// Manager は setup.host の相手を用意する。必須。
	Manager *host.Manager
	// Ccnavi は組んだ ccnavi の実行ファイル（ボードを読む・使い捨てのワークスペースへ置く）。Teststub は組んだ teststub。
	Ccnavi   func() (string, error)
	Teststub func() (string, error)
	// Entry は本物の claude を呼べる（入口の実行ファイルだけが立てる）。
	Entry bool
	// Step は step を回す関数。nil なら claude（--agent script なら teststub claude）を起こす。
	Step StepFunc
	// Environ は step に渡す親の環境（`名前=値`）。nil なら os.Environ。
	Environ []string
	// Getenv は判定のモードを読む。nil なら os.Getenv。
	Getenv func(string) string
	// Out は標準出力の代わり。nil なら os.Stdout。
	Out io.Writer
	// Now は時刻。nil なら time.Now。
	Now func() time.Time
}

func (r *Runner) say(line string) {
	w := r.Out
	if w == nil {
		w = os.Stdout
	}
	fmt.Fprintln(w, line)
}

func (r *Runner) getenv(name string) string {
	if r.Getenv != nil {
		return r.Getenv(name)
	}
	return os.Getenv(name)
}

func (r *Runner) now() time.Time {
	if r.Now != nil {
		return r.Now()
	}
	return time.Now()
}

func (r *Runner) environ() []string {
	if r.Environ != nil {
		return r.Environ
	}
	return os.Environ()
}

func (r *Runner) logsDir() string {
	if r.LogsDir != "" {
		return r.LogsDir
	}
	return filepath.Join(r.RepoRoot, "logs", "scenarios")
}

func (r *Runner) modeCheck(root string) (bool, string) { return world.ModeCheck(root, r.getenv) }

func uuid4() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

func stepCap(scn *core.Scenario) float64 {
	n := scn.PromptSteps
	if n < 1 {
		n = 1
	}
	v, _ := strconv.ParseFloat(fmt.Sprintf("%.2f", scn.BudgetUSD/float64(n)), 64)
	return v
}

// realStep は claude（--agent script なら teststub claude）を 1 回起こす。
func (r *Runner) realStep(in StepInput) (*core.Stream, error) {
	scripted := in.Scn.IsScripted()
	if !scripted && !r.Entry {
		return nil, errors.New("受入シナリオは入口（" + core.Runner + "）から起動したときだけ回せる（テストや他のモジュールから呼ばない）")
	}
	args := []string{"-p", world.FillIssue(strings.TrimSpace(in.Step.Prompt), in.IssueNo), "--model", in.Scn.Model,
		"--output-format", "stream-json", "--verbose", "--include-hook-events",
		"--max-budget-usd", fmt.Sprintf("%.2f", stepCap(in.Scn)), "--permission-mode", in.Scn.PermissionMode}
	if in.ResumeID != "" {
		args = append(args, "--resume", in.ResumeID)
	} else {
		args = append(args, "--session-id", in.SessionID)
	}
	if !in.Persist {
		args = append(args, "--no-session-persistence")
	}
	environ := r.environ()
	if in.Env != nil {
		environ = host.Environ(in.Env)
	}
	program := "claude"
	if scripted {
		stub, err := r.Teststub()
		if err != nil {
			return nil, err
		}
		plan := map[string]any{"script": in.Step.Script, "after_stop_block": in.Step.AfterStopBlock}
		if in.Step.AfterStopBlock == nil {
			plan["after_stop_block"] = []any{}
		}
		data, err := json.Marshal(plan)
		if err != nil {
			return nil, err
		}
		planPath := filepath.Join(in.Out, fmt.Sprintf("step-%d.script.json", in.Idx))
		if err := os.WriteFile(planPath, data, 0o644); err != nil {
			return nil, err
		}
		environ = append(append([]string{}, environ...), env.FakeClaudeScript+"="+planPath)
		program, args = stub, append([]string{"claude"}, args...)
	}
	ctx, cancel := context.WithTimeout(context.Background(), StepTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, program, args...)
	cmd.Dir = in.Root
	cmd.Env = environ
	var stdout, stderr strings.Builder
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	runErr := cmd.Run()
	if err := os.WriteFile(filepath.Join(in.Out, fmt.Sprintf("step-%d.jsonl", in.Idx)), []byte(stdout.String()), 0o644); err != nil {
		return nil, err
	}
	if strings.TrimSpace(stderr.String()) != "" {
		_ = os.WriteFile(filepath.Join(in.Out, fmt.Sprintf("step-%d.stderr", in.Idx)), []byte(stderr.String()), 0o644)
	}
	if ctx.Err() != nil {
		return nil, fmt.Errorf("claude が %d 秒で終わらなかった", int(StepTimeout.Seconds()))
	}
	if runErr != nil {
		if ee, ok := runErr.(*exec.ExitError); ok {
			// 失敗した step の出力は、ここでは判定に使わない（呼び手が止める）
			return nil, fmt.Errorf("claude が終了コード %d で終わった", ee.ExitCode())
		}
		return nil, runErr
	}
	return core.ParseStream(stdout.String()), nil
}

// RunStep は step 1 つを回す（差し替え口が無ければ起こす）。
func (r *Runner) RunStep(in StepInput) (*core.Stream, error) {
	if r.Step != nil {
		return r.Step(in)
	}
	return r.realStep(in)
}

// persistNeeded は、続きの step が --resume でつなぐなら、セッションを残す。
func persistNeeded(steps []core.Step, idx int) bool {
	for _, next := range steps[idx+1:] {
		if next.Human {
			continue
		}
		return next.Resume
	}
	return false
}

// failedStepCost は正常に終わらなかった step の実費の見積もり。累計の上限が過小に見えないようにする。
// 出力の result に費用があればそれ、無ければその step の上限（--max-budget-usd に渡す値。実費より多めの保守側）。
func failedStepCost(scn *core.Scenario, out string, num int) float64 {
	if data, err := os.ReadFile(filepath.Join(out, fmt.Sprintf("step-%d.jsonl", num))); err == nil {
		if c := core.ParseStream(string(data)).CostUSD; c > 0 {
			return c
		}
	}
	return stepCap(scn)
}

type session struct {
	r    *Runner
	scn  *core.Scenario
	st   *core.State
	out  string
	root string
}

func (s *session) save() error { return core.Save(s.out, s.st) }

// snapshot は step の終わりの相手側の状態を残す。記録できなかったら止めて false を返す（finish で後片付けする）。
func (s *session) snapshot(idx int) bool {
	if err := prepare.SnapshotHost(s.r.Manager, s.st, idx, s.root, s.out, s.save); err != nil {
		s.st.Halt = &core.Halt{Step: idx + 1, Kind: "host", Reasons: []string{"相手側の状態を記録できなかった: " + err.Error()}}
		_ = s.save()
		s.r.say(fmt.Sprintf("step %d: 相手側の状態を記録できなかった（%v）。後続の step には進まず止める", idx+1, err))
		return false
	}
	return true
}

func (s *session) terminal() (actors.Terminal, error) {
	tm := actors.Terminal{Board: s.r.board}
	h, err := prepare.HostEnv(s.r.Manager, s.st, s.r.environ())
	if err != nil {
		return tm, err
	}
	if h != nil {
		tm.Env = func(root string) []string {
			var out []string
			for _, kv := range host.Environ(h) {
				if name, _, _ := strings.Cut(kv, "="); name != env.GuardTicketApproval && name != env.Workspace {
					out = append(out, kv)
				}
			}
			return append(out, env.Workspace+"="+root)
		}
	}
	return tm, nil
}

// board は ccnavi のボードを読む（`ccnavi --explain --json --root <root>`）。
func (r *Runner) board(root string) (actors.Board, error) {
	exe, err := r.Ccnavi()
	if err != nil {
		return actors.Board{}, err
	}
	cmd := exec.Command(exe, "--explain", "--json", "--root", root)
	cmd.Dir = root
	var stdout, stderr strings.Builder
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		return actors.Board{}, fmt.Errorf("--explain が失敗した: %v: %s", err, strings.TrimSpace(stderr.String()))
	}
	return actors.ParseBoard([]byte(stdout.String()))
}

// runSteps は st.NextStep から、人間の操作の step か末尾まで進める。人間の操作で止まったら 10、末尾まで進んだら 0、
// 止めたら 11 を返す（done が偽のとき）。
func (s *session) runSteps() (rc int, done bool) {
	steps := s.scn.Steps
	r := s.r
	for s.st.NextStep < len(steps) {
		idx := s.st.NextStep
		step := steps[idx]
		if step.Human && len(step.HumanAPI) > 0 {
			rc := actors.RunAPI(r.Manager, s.st, idx, step.HumanAPI, s.root, s.save, r.say)
			if !s.snapshot(idx) {
				return ExitHalted, false
			}
			if rc != 0 {
				return rc, false
			}
			continue
		}
		if step.Human && step.HumanRun != nil {
			spec := actors.RunSpec{Script: filepath.Join(s.root, filepath.FromSlash(core.HumanRunScripts[step.HumanRun.Script].Path)), Answers: step.HumanRun.Answers}
			tm, err := s.terminal()
			if err != nil { // 相手の環境を作れないまま打つと、本物の origin に向かう恐れがある
				s.st.Halt = &core.Halt{Step: idx + 1, Kind: "run", Reasons: []string{fmt.Sprintf("step %d の環境を作れなかった: %v", idx+1, err)}}
				_ = s.save()
				r.say(fmt.Sprintf("step %d: 環境を作れなかった（%v）。後続の step には進まず止める", idx+1, err))
				return ExitHalted, false
			}
			rc := tm.Run(s.st, idx, spec, s.root, s.out, s.save, r.say)
			if rc != actors.Pause {
				if !s.snapshot(idx) {
					return ExitHalted, false
				}
				if rc != 0 {
					return rc, false
				}
				continue
			}
			r.say(fmt.Sprintf("\nstep %d: この環境では擬似端末を作れないので、人間役を打てない。手で打つ", idx+1))
		}
		if step.Human {
			r.say(fmt.Sprintf("\n== 人間の操作（step %d）==", idx+1))
			r.say(strings.TrimSpace(step.Instruction))
			for _, c := range step.Check {
				r.say("  確認: " + c)
			}
			r.say(fmt.Sprintf("\n済んだら人間が「次へ」と言う。続きは: %s next %s", core.Runner, s.out))
			s.st.NextStep = idx + 1
			_ = s.save()
			return ExitHuman, false
		}
		resumeID := ""
		if step.Resume && len(s.st.SessionIDs) > 0 {
			resumeID = s.st.SessionIDs[len(s.st.SessionIDs)-1]
		}
		sid := uuid4()
		t0 := r.now()
		stepEnv, envErr := prepare.HostEnv(r.Manager, s.st, r.environ())
		var ev *core.Stream
		err := envErr
		if err == nil {
			ev, err = r.RunStep(StepInput{Scn: s.scn, Step: step, Out: s.out, Idx: idx + 1, SessionID: sid, ResumeID: resumeID,
				Persist: persistNeeded(steps, idx), Root: s.root, IssueNo: s.st.IssueNo, Env: stepEnv})
		}
		if err != nil { // 起動できない・時間切れ・終了コードが 0 でない
			s.st.CostUSD += failedStepCost(s.scn, s.out, idx+1)
			_ = s.snapshot(idx)
			// 後続は回さず止める。finish に回して、後片付けを通す
			s.st.Halt = &core.Halt{Step: idx + 1, Kind: "run", Reasons: []string{fmt.Sprintf("step %d が正常に終わらなかった: %v", idx+1, err)}}
			_ = s.save()
			r.say(fmt.Sprintf("step %d: 正常に終わらなかった（%v）。後続の step には進まず止める", idx+1, err))
			return ExitHalted, false
		}
		r.say(fmt.Sprintf("step %d: %.0f 秒、費用 %.3f USD", idx+1, r.now().Sub(t0).Seconds(), ev.CostUSD))
		ids := ev.SessionIDs
		if len(ids) == 0 {
			ids = []string{sid}
		}
		for _, id := range ids {
			if !slices.Contains(s.st.SessionIDs, id) {
				s.st.SessionIDs = append(s.st.SessionIDs, id)
			}
		}
		s.st.CostUSD += ev.CostUSD
		s.st.NextStep = idx + 1
		_ = s.save()
		if !s.snapshot(idx) {
			return ExitHalted, false
		}
		if after := step.ExpectAfter; after != nil {
			results, err := judge.EvaluateAfter(*after, s.st.SessionIDs, s.root)
			var bad []core.Result
			if err != nil { // 期待を評価できない（cwd が無い・時間切れ）。止めて finish に回す
				bad = []core.Result{{Kind: "expect_after", Expect: after, Verdict: core.Inconclusive, Why: fmt.Sprintf("評価で例外: %v", err)}}
			}
			for _, res := range results {
				if res.Verdict != core.Pass {
					bad = append(bad, res)
				}
			}
			if len(bad) > 0 {
				reasons := make([]string, len(bad))
				for i, b := range bad {
					data, _ := json.Marshal(b.Expect)
					reasons[i] = fmt.Sprintf("%s %s: %s", b.Kind, data, b.Why)
				}
				s.st.Halt = &core.Halt{Step: idx + 1, Kind: "expect_after", Reasons: reasons}
				_ = s.save()
				r.say(fmt.Sprintf("step %d: expect_after を満たさない。後続の step には進まず止める", idx+1))
				return ExitHalted, false
			}
		}
	}
	return 0, true
}

// finish は判定と後片付けまで進め、報告を書いて終了コードを返す。
func (s *session) finish() int {
	r, scn, st, out, root := s.r, s.scn, s.st, s.out, s.root
	var results []core.Result
	var rows []judge.Row
	var raw string
	var evalErr error
	func() {
		// state の run は setup のファイルが残っているうちに回す。消すのは判定のあと
		paths, _ := filepath.Glob(filepath.Join(out, "step-*.jsonl"))
		sortNumeric(paths)
		var sb strings.Builder
		for _, p := range paths {
			data, _ := os.ReadFile(p)
			sb.Write(data)
		}
		raw = sb.String()
		rows = judge.ReadDecisions(filepath.Join(root, "logs", "decisions.jsonl"), st.SessionIDs)
		spec := scn.Expect
		if scn.IsScripted() {
			spec.Judge = nil // スタブは台本どおりに打つので、モデルの振る舞いを読む judge は評価しない
		}
		results, evalErr = judge.Evaluate(spec, rows, raw, root, st.PreDigests)
		if evalErr != nil {
			results = []core.Result{{Kind: "state", Expect: "評価", Verdict: core.Inconclusive, Why: fmt.Sprintf("評価で例外: %v", evalErr)}}
		}
		if halt := st.Halt; halt != nil {
			// 止まった回は後続の step が回っていないので、decisions・state は必ず落ちる。外す
			kept := results[:0]
			for _, res := range results {
				if res.Kind != "decisions" && res.Kind != "state" {
					kept = append(kept, res)
				}
			}
			kind := halt.Kind
			if kind == "" {
				kind = "expect_after"
			}
			results = append(kept, core.Result{Kind: kind, Expect: map[string]int{"step": halt.Step}, Verdict: core.Inconclusive, Why: strings.Join(halt.Reasons, "; ")})
		}
		// ccnavi 本体に、ブランチ・コミット・ワークツリーが残っていないかを実際の状態で確かめる
		v, why := judge.RepoResult(st.PreRepo, world.RepoSnapshot(root))
		results = append(results, core.Result{Kind: "repo", Expect: "ccnavi 本体の HEAD・ブランチ・ワークツリー", Verdict: v, Why: why})
		note := report.HostNote(scn.HostKind())
		if st.Host != nil && st.Host.Version != "" {
			note = strings.Replace(note, "docker-gitlab:", fmt.Sprintf("docker-gitlab（GitLab %s）:", st.Host.Version), 1)
		}
		results = append(results, core.Result{Kind: "host", Expect: "setup.host", Verdict: core.Pass, Why: note})
	}()
	undeleted, notRestored := s.cleanup()
	_ = s.save()
	if notRestored != "" {
		results = append(results, core.Result{Kind: "reset", Expect: st.Target.Path, Verdict: core.Fail, Why: "base に戻せなかった。手作業が要る"})
	}
	if len(st.HumanOps) > 0 {
		bad := 0
		for _, o := range st.HumanOps {
			if o.Status < 200 || o.Status >= 300 || o.Error != "" {
				bad++
			}
		}
		total := len(st.HumanOps)
		v, why := core.Pass, fmt.Sprintf("%d 件中 %d 件が成功", total, total-bad)
		if bad > 0 {
			v, why = core.Fail, why+fmt.Sprintf("、%d 件が失敗", bad)
		}
		results = append(results, core.Result{Kind: "human-api", Expect: "人間役の API 操作", Verdict: v, Why: why})
	}
	for _, run := range st.HumanRuns {
		if run.Status != "done" {
			continue
		}
		// 人間が見ていた項目を、実行器が打った回は誰も見ていない。pass にせず目視を求める
		for _, item := range scn.Steps[run.Step-1].Check {
			results = append(results, core.Result{Kind: "human-run-check", Expect: map[string]int{"step": run.Step}, Verdict: core.Review,
				Why: fmt.Sprintf("%s（実行器が打ったので未確認。ログ: %s）", item, run.Log)})
		}
	}
	if undeleted {
		h := st.Host
		results = append(results, core.Result{Kind: "host-delete", Expect: h.URL, Verdict: core.Fail,
			Why: fmt.Sprintf("プロジェクト（ID %d）を消せなかった。手作業で消す", h.ProjectID)})
	}
	if err := report.WriteReport(scn, out, st, results, rows); err != nil {
		r.say(fmt.Sprintf("報告を書けなかった: %v", err))
	}
	final := judge.Overall(results)
	r.say(fmt.Sprintf("\n結果: %s（レポート: %s/report.md）", final, out))
	if notRestored != "" || undeleted {
		return 3 // レポートは書いた。次の回を始めさせない
	}
	return map[string]int{core.Pass: 0, core.Fail: 1, core.Inconclusive: 2, core.Review: 2}[final]
}

func sortNumeric(paths []string) {
	num := func(p string) int {
		n, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(filepath.Base(p), "step-"), ".jsonl"))
		return n
	}
	for i := 1; i < len(paths); i++ {
		for j := i; j > 0 && num(paths[j]) < num(paths[j-1]); j-- {
			paths[j], paths[j-1] = paths[j-1], paths[j]
		}
	}
}

// cleanup は setup のファイル・issue・相手・対象の後片付けをする。(相手のプロジェクトを消せなかった, 対象を戻せなかったときの文面)。
func (s *session) cleanup() (undeleted bool, notRestored string) {
	r, st := s.r, s.st
	prepare.RemoveFiles(st.SetupMade, st.SetupDirs, s.root)
	st.SetupMade = nil // restore が、あとから同じパスに作られたファイルを消さないように
	if issue := s.scn.Setup.Issue; issue != nil && issue.Close && st.IssueNo != 0 {
		world.CloseIssue(*issue, st.IssueNo, s.root, r.say)
	}
	undeleted = prepare.TeardownHost(r.Manager, st, s.root)
	notRestored = world.ReleaseTarget(st, s.root, false, r.say)
	return undeleted, notRestored
}

// abort は回し始める前に止めたとき、作ったものを消し、対象を戻して放す（戻せなければ文面が出る）。
func (s *session) abort() {
	s.cleanup()
	_ = s.save()
}

// StartScenario は確認と回す前の検査が済んだシナリオを回す（setup → step → 判定 → 後片付け）。終了コードを返す。
// run と run-all が使う。top は使い捨てのリポジトリの実体（対象を触らないシナリオは空）。
// 回す前の拒否は文を出して 3、それ以外の setup の失敗は後片付けしてエラーで返す。
func (r *Runner) StartScenario(scn *core.Scenario, scenarioPath, root, top, out string) (int, error) {
	if err := os.MkdirAll(out, 0o755); err != nil {
		return 1, err
	}
	abs, _ := filepath.Abs(scenarioPath)
	st := &core.State{
		Scenario: abs, Agent: scn.Agent, Root: root, Out: out, SessionIDs: []string{}, PreDigests: map[string]string{},
		PreRepo: world.RepoSnapshot(root), SetupMade: []string{}, SetupDirs: []string{},
	}
	for _, rel := range scn.Expect.FilesUnchanged {
		st.PreDigests[rel] = judge.FileDigest(filepath.Join(root, rel))
	}
	s := &session{r: r, scn: scn, st: st, out: out, root: root}
	_ = s.save()
	if err := s.setup(top); err != nil {
		s.abort()
		var refusal *core.Refusal
		if errors.As(err, &refusal) {
			return core.Refuse(r.say, err), nil
		}
		return 1, err
	}
	_ = s.save()
	return s.continueScenario(), nil
}

func (s *session) setup(top string) error {
	r, scn, st, root := s.r, s.scn, s.st, s.root
	base := scn.Setup.ResetBase
	if top != "" {
		lock, err := world.AcquireTarget(top, s.out, false)
		if err != nil {
			return err
		}
		st.Target = &core.Target{Path: top, Base: base, Lock: lock}
		_ = s.save()
		if base != "" {
			if err := world.ResetRepo(root, top, base, ""); err != nil {
				return err
			}
		}
		if err := prepare.StartHost(r.Manager, st, host.Setup{Kind: scn.HostKind(), State: scn.Setup.HostState}, scn.ID, root, top, s.save); err != nil {
			return err
		}
	}
	// issue は回すたびに新しく作る。番号は state に残し、続きの step の prompt にも入れる
	if issue := scn.Setup.Issue; issue != nil {
		n, err := world.MakeIssue(*issue, root)
		if err != nil {
			return err
		}
		st.IssueNo = n
	}
	made, dirs, err := prepare.MakeFiles(world.Hooks(), scn.Setup.Files, root, top)
	if err != nil {
		return err
	}
	st.SetupMade, st.SetupDirs = made, dirs
	if len(scn.Setup.Git) > 0 {
		var via []string
		var genv map[string]string
		if st.Host != nil {
			via = st.Host.GitVia()
			if genv, err = r.Manager.GitEnv(st.Host); err != nil {
				return err
			}
		}
		if err := world.RunSetupGit(root, top, scn.Setup.Git, "", via, genv); err != nil {
			return err
		}
	}
	for _, step := range scn.Steps {
		if step.HumanRun != nil {
			// human_run の対象を決める基準。setup で置いた提案は対象にしない
			board, err := r.board(root)
			if err != nil {
				return err
			}
			st.ProposalsBefore = board.Proposals()
			if st.ProposalsBefore == nil {
				st.ProposalsBefore = []core.Proposal{}
			}
			break
		}
	}
	return nil
}

// continueScenario は steps を進め、判定と後片付けまで持っていく。回している最中に panic が起きても、
// setup のファイルを残さず、対象を base へ戻す（戻せなければ文面を出す。元の panic はそのまま投げ直す）。
func (s *session) continueScenario() int {
	r, st := s.r, s.st
	finished := false
	defer func() {
		if !finished {
			if p := recover(); p != nil {
				prepare.RemoveFiles(st.SetupMade, st.SetupDirs, s.root)
				st.SetupMade = nil
				prepare.TeardownHost(r.Manager, st, s.root)
				world.ReleaseTarget(st, s.root, false, r.say)
				_ = s.save()
				panic(p)
			}
		}
	}()
	rc, done := s.runSteps()
	finished = true
	if rc == ExitHuman && st.Host != nil {
		// 模擬は止める。置き場（bare・スタブ・模擬の状態）は、next が同じ環境で続けるので残す
		r.Manager.Hold(s.root, st.Host)
		_ = s.save()
		if st.Host.ProjectID != 0 {
			r.say(fmt.Sprintf("案内: docker-gitlab のプロジェクトを残している: %s（ID %d）。next が最後まで終わるか restore で消える", st.Host.URL, st.Host.ProjectID))
		}
	}
	if rc == ExitHuman && st.Target != nil {
		// 人間が操作したあとの next で続くので、対象は base に戻さず残す（ロックだけ放す）
		world.ReleaseTarget(st, s.root, true, r.say)
		_ = s.save()
		if st.Target.Base != "" {
			r.say(fmt.Sprintf("案内: %s を %s に戻していない（setup のファイルも残している）。戻すのは next が最後まで終わったとき", st.Target.Path, st.Target.Base))
			r.say(fmt.Sprintf("次: 続けるなら `%s next %s`、やめるなら `%s restore %s`", core.Runner, s.out, core.Runner, s.out))
		}
	}
	if done || rc == ExitHalted {
		return s.finish()
	}
	return rc
}

func samePath(a, b string) bool { return ospath.Realpath(a) == ospath.Realpath(b) }
