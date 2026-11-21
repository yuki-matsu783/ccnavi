package runner

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/judge"
	"ccnavi/internal/harness/scenario/prepare"
	"ccnavi/internal/harness/scenario/report"
	"ccnavi/internal/harness/scenario/world"
	"ccnavi/internal/infra/ospath"
)

// gate は本物の claude を呼ぶ道を閉じる。入口（Entry）でも、差し替え口（Step）でも、台本のスタブでも無ければ断る。
func (r *Runner) gate(scripted bool) bool {
	if r.Entry || r.Step != nil || scripted {
		return true
	}
	r.say("受入シナリオは入口（" + core.Runner + "）から起動したときだけ回せる（テストや他のモジュールから呼ばない）")
	return false
}

func (r *Runner) abs(path string) string {
	if path == "" {
		path = r.RepoRoot
	}
	a, err := filepath.Abs(path)
	if err != nil {
		return path
	}
	return ospath.Realpath(a)
}

// Preflight は回さずに、確認に出す文だけを出す。終了コードは run の確認と同じ 3。
func (r *Runner) Preflight(scenario, root string) int {
	root = r.abs(root)
	scn, err := core.LoadFile(scenario, core.AgentClaude)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	_, mode := r.modeCheck(root)
	r.say(report.ConfirmationText(scn, root, mode, r.Manager))
	return 3
}

// RunOptions は run の引数。
type RunOptions struct {
	Scenario  string
	Confirmed bool
	Root      string
	Out       string
	// Agent は core.AgentClaude か core.AgentScript（空は claude）。
	Agent string
}

// Run は 1 本のシナリオを回す（確認が済んでいれば）。
func (r *Runner) Run(o RunOptions) int {
	if o.Agent == "" {
		o.Agent = core.AgentClaude
	}
	if !r.gate(o.Agent == core.AgentScript) {
		return 1
	}
	root := r.abs(o.Root)
	scn, err := core.LoadFile(o.Scenario, o.Agent)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	ok, mode := r.modeCheck(root)
	if !ok {
		// 確認の前に止める。--confirmed を付けても、enable でなければ回さない
		r.say(mode)
		return 3
	}
	if clash := prepare.Clashes(scn.Setup.Files, root); len(clash) > 0 {
		// 有るものを後片付けで消さないよう、追記の対象が無いまま書き始めないよう、回す前に止める
		r.say("setup.files のパスが合わない（作る形は既にある・追記の形は無い）: " + strings.Join(clash, ", ") +
			"\n案内: 作る形のパスは、回したあとに消すので、既にあると回さない" +
			"\n次: パスを直すか、既にあるファイルを退けて打ち直す")
		return 1
	}
	top := ""
	if scn.UsesTarget() {
		top, err = world.RequireDisposableRepo(root, filepath.Join(root, filepath.FromSlash(core.DisposableRel)), "")
		if err == nil {
			err = world.CheckBeforeRun(scn, root, top, r.Manager)
		}
		if err != nil {
			return r.refuseOrFail(err)
		}
	}
	if !o.Confirmed {
		r.say(report.ConfirmationText(scn, root, mode, r.Manager))
		return 3
	}
	out := o.Out
	if out == "" {
		out = filepath.Join(r.logsDir(), fmt.Sprintf("%s-%s", scn.ID, r.now().Format("20060102-150405")))
	}
	out = r.abs(out) // cwd が変わっても next / restore が同じ回と分かるように
	rc, err := r.StartScenario(scn, o.Scenario, root, top, out)
	if err != nil {
		r.say(err.Error())
	}
	return rc
}

func (r *Runner) refuseOrFail(err error) int {
	var refusal *core.Refusal
	if errors.As(err, &refusal) {
		return core.Refuse(r.say, err)
	}
	r.say(err.Error())
	return 1
}

// Next は人間の操作で止まったあと、人間が「次へ」と言ってから打つ。続きを回す。
func (r *Runner) Next(out string) int {
	st, err := core.LoadState(out)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	if st.Released {
		r.say("対象を戻して放した回（restore 済みか終了済み）。続きは回せない")
		r.say(fmt.Sprintf("次: やり直すなら `%s run <シナリオ> --confirmed`", core.Runner))
		return 2
	}
	s := &session{r: r, st: st, out: out, root: st.Root}
	if st.Halt != nil {
		// halt は finish で戻してある。届かなかった場合に備え、戻し直す（戻し済みなら何もしない）
		r.say(fmt.Sprintf("step %d で止めた回。続きは回せない", st.Halt.Step))
		undeleted := prepare.TeardownHost(r.Manager, st, st.Root)
		failed := world.ReleaseTarget(st, st.Root, false, r.say)
		_ = s.save()
		if failed != "" || undeleted {
			return 3
		}
		return 2
	}
	// 人間の操作の間にモードが変わることがあるので、続けるたびに確かめる
	if ok, mode := r.modeCheck(st.Root); !ok {
		r.say(mode)
		return 3
	}
	if st.Target != nil && st.Held {
		lock, err := world.AcquireTarget(st.Target.Path, out, true)
		if err != nil {
			return r.refuseOrFail(err)
		}
		st.Target.Lock, st.Held = lock, false
	}
	if st.Host != nil && !st.Host.Down {
		if err := r.Manager.Resume(st.Root, st.Host); err != nil {
			return core.Refuse(r.say, &core.Refusal{What: err.Error(), Next: "ポートを空けてから next を打ち直す"})
		}
		_ = s.save()
	}
	scn, err := core.LoadFromState(st)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	s.scn = scn
	return s.continueScenario()
}

// Restore は人間の操作で止めた回を続けずにやめる。setup のファイルを消し、対象を base へ戻して放す。
// 戻せなかった回の戻し直しにも使う。
func (r *Runner) Restore(out string) int {
	st, err := core.LoadState(out)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	scn, err := core.LoadFromState(st)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	if st.Target != nil && !st.Released {
		lock, err := world.AcquireTarget(st.Target.Path, out, true)
		if err != nil {
			return r.refuseOrFail(err)
		}
		st.Target.Lock = lock
	}
	s := &session{r: r, scn: scn, st: st, out: out, root: st.Root}
	undeleted, failed := s.cleanup()
	_ = s.save()
	if failed != "" || undeleted {
		return 3
	}
	r.say(fmt.Sprintf("%s の回を片付けた（setup のファイルを消し、対象を戻して放した）", out))
	return 0
}

func (r *Runner) recordAcceptance(out, reportMD string, accept bool) int {
	if !accept {
		r.say("この回の意味の判定は、もう報告に足してある（受け入れを記すなら --accept を付ける）")
		return 1
	}
	data, err := os.ReadFile(reportMD)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	head, section, _ := strings.Cut(string(data), judge.JudgeHeading)
	if strings.Contains(section, "ユーザの受け入れ: あり") {
		r.say("受け入れは、もう記録してある（二重には記さない）")
		return 1
	}
	judged, err := judge.ParseJudgment(section)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	mech, err := judge.MechanicalResults(out)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	verdict := judge.FinalVerdict(mech, judged, true)
	const total = "全体（機械判定と意味の判定の合わせ）:"
	lines := strings.Split(section, "\n")
	for i, l := range lines {
		if strings.HasPrefix(l, total) {
			lines[i] = total + " " + verdict
		} else {
			lines[i] = strings.ReplaceAll(l, "ユーザの受け入れ: なし", "ユーザの受け入れ: あり")
		}
	}
	if err := os.WriteFile(reportMD, []byte(head+judge.JudgeHeading+strings.Join(lines, "\n")), 0o644); err != nil {
		r.say(err.Error())
		return 1
	}
	r.say("ユーザの受け入れを記した。全体: " + verdict)
	return 0
}

// Judge は意味の判定（別のエージェントが書いた判定の表）を報告に足す。足してあれば、accept でユーザの受け入れを記す。
func (r *Runner) Judge(out, file string, accept bool) int {
	reportMD := filepath.Join(out, "report.md")
	data, err := os.ReadFile(reportMD)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	if strings.Contains(string(data), judge.JudgeHeading) {
		return r.recordAcceptance(out, reportMD, accept)
	}
	if file == "" {
		r.say("意味の判定がまだ無い。判定の表のファイルを渡す")
		return 1
	}
	table, err := os.ReadFile(file)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	judged, err := judge.ParseJudgment(string(table))
	if err != nil {
		r.say(err.Error())
		return 1
	}
	st, err := core.LoadState(out)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	scn, err := core.LoadFromState(st)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	if want := len(scn.Expect.Judge); len(judged) == 0 || len(judged) != want {
		r.say(fmt.Sprintf("判定の表の行数が違う: 期待 %d 行、読めた %d 行", want, len(judged)))
		return 1
	}
	lines := []string{"", judge.JudgeHeading, "", "| 基準 | 判定 | 理由 |", "|---|---|---|"}
	for _, j := range judged {
		lines = append(lines, fmt.Sprintf("| %s | %s | %s |", j.Criterion, j.Verdict, j.Why))
	}
	accepted := "なし"
	if accept {
		accepted = "あり"
	}
	lines = append(lines, "", "ユーザの受け入れ: "+accepted)
	mech, err := judge.MechanicalResults(out)
	if err != nil {
		r.say(err.Error())
		return 1
	}
	verdict := judge.FinalVerdict(mech, judged, accept)
	lines = append(lines, "全体（機械判定と意味の判定の合わせ）: "+verdict)
	if err := os.WriteFile(reportMD, []byte(string(data)+strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		r.say(err.Error())
		return 1
	}
	r.say("意味の判定を報告に足した: " + reportMD)
	r.say("全体: " + verdict)
	return 0
}

// ExpandScenarioPaths は run-all・run-stub の引数（ディレクトリ・glob・ファイル）をシナリオのパスの列にする。重複は除く。
func ExpandScenarioPaths(patterns []string) []string {
	var found []string
	for _, pattern := range patterns {
		info, err := os.Stat(pattern)
		switch {
		case err == nil && info.IsDir():
			m, _ := filepath.Glob(filepath.Join(pattern, "*.yml"))
			sort.Strings(m)
			found = append(found, m...)
		case err == nil:
			found = append(found, pattern)
		default:
			m, _ := filepath.Glob(pattern)
			sort.Strings(m)
			found = append(found, m...)
		}
	}
	seen := map[string]bool{}
	var paths []string
	for _, p := range found {
		key := r2(p)
		if !seen[key] {
			seen[key] = true
			paths = append(paths, p)
		}
	}
	return paths
}

func r2(p string) string {
	a, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	return ospath.Realpath(a)
}

// runAllReason は run-all の対象にしない理由。対象にできるなら空。使い捨てのリポジトリの判定に、続けて回すのに必要な条件
// （人間の操作が無い・前の回の状態を残さない）を足す。
func (r *Runner) runAllReason(scn *core.Scenario, root, top, topWhy string) string {
	for _, st := range scn.Steps {
		if st.Human {
			return "人間の操作の step がある（続けて回せない。1 本ずつ確認して回す）"
		}
	}
	if !scn.UsesTarget() {
		return fmt.Sprintf("使い捨てのリポジトリ（%s）に作用する setup が無い（ccnavi 本体に作用しうる。1 本ずつ確認して回す）", core.DisposableRel)
	}
	if scn.Setup.ResetBase == "" {
		return "setup.reset が無い（前の回の状態が次の回に残る）"
	}
	if clash := prepare.Clashes(scn.Setup.Files, root); len(clash) > 0 {
		return "setup.files のパスが合わない（作る形は既にある・追記の形は無い）: " + strings.Join(clash, ", ")
	}
	if top == "" {
		return "対象のリポジトリを使えない: " + topWhy
	}
	if err := world.CheckBeforeRun(scn, root, top, r.Manager); err != nil {
		var refusal *core.Refusal
		what := err.Error()
		if errors.As(err, &refusal) {
			what = refusal.What
		}
		return "回す前の確認で断られる: " + what
	}
	return ""
}

// RunAllPlan は (対象, 対象にしない) を返す。対象は ID 順、対象にしないものは読んだ順。
func (r *Runner) RunAllPlan(paths []string, root string) ([]report.Eligible, []report.Skip) {
	top, topWhy := "", ""
	top, err := world.RequireDisposableRepo(root, filepath.Join(root, filepath.FromSlash(core.DisposableRel)), "")
	if err != nil {
		var refusal *core.Refusal
		topWhy = err.Error()
		if errors.As(err, &refusal) {
			topWhy = refusal.What
		}
	}
	var eligible []report.Eligible
	var skipped []report.Skip
	ids := map[string]string{}
	for _, path := range paths {
		scn, err := core.LoadFile(path, core.AgentClaude)
		if err != nil {
			skipped = append(skipped, report.Skip{Who: path, Why: "読み込めない: " + err.Error()})
			continue
		}
		if prev, dup := ids[scn.ID]; dup {
			skipped = append(skipped, report.Skip{Who: scn.ID, Why: fmt.Sprintf("ID が重複している（%s と同じ）", prev)})
			continue
		}
		ids[scn.ID] = path
		if why := r.runAllReason(scn, root, top, topWhy); why != "" {
			skipped = append(skipped, report.Skip{Who: scn.ID, Why: why})
		} else {
			eligible = append(eligible, report.Eligible{Path: path, Scn: scn})
		}
	}
	sort.SliceStable(eligible, func(i, j int) bool { return eligible[i].Scn.ID < eligible[j].Scn.ID })
	return eligible, skipped
}

// RunAllDigest は確認した内容の目印。ID の集合・base・シナリオごとの費用の上限・累計の上限・人が読んだ概要から作る。
// どれか一つでも変われば目印も変わる。実行の直前に作り直して、確認したときの値と比べる。
func RunAllDigest(eligible []report.Eligible, cap float64) string {
	var scenarios []map[string]any
	for _, e := range eligible {
		scenarios = append(scenarios, map[string]any{
			"id": e.Scn.ID, "base": e.Scn.Setup.ResetBase, "budget_usd": e.Scn.BudgetUSD,
			"summary": report.ScenarioSummary(e.Scn),
			// 概要に出ない欄（prompt の全文・permission_mode・model・expect・setup の中身）も含める。
			// 書き換えがあれば目印が変わり、確認を取り直す（安全側）
			"scenario": e.Scn.Raw,
		})
	}
	data, _ := json.Marshal(map[string]any{"max_total_usd": cap, "scenarios": scenarios})
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])[:16]
}

// scenarioRow は run-all の表の 1 行。
func scenarioRow(scn *core.Scenario, out, root string, rc int) report.Row {
	row := report.Row{ID: scn.ID, RC: rc, Verdict: "not-run", Out: out}
	st, err := core.LoadState(out)
	if err != nil {
		return row
	}
	row.CostUSD = st.CostUSD
	for _, p := range judge.ReadStepStreams(out).Streams {
		row.ToolCalls += len(p.ToolCalls)
	}
	for _, d := range judge.ReadDecisions(filepath.Join(root, "logs", "decisions.jsonl"), st.SessionIDs) {
		switch d["decision"] {
		case "deny":
			row.Denies++
		case "ask":
			row.Asks++
		}
	}
	if recorded, err := judge.ReadReport(out); err == nil {
		row.Verdict = judge.Overall(recorded)
	}
	return row
}

// runAllLoop は順に回す。止まった理由（なければ空）を返す。rows に、回した順に行を足す。
func (r *Runner) runAllLoop(eligible []report.Eligible, root string, cap float64, batch string, rows *[]report.Row) string {
	spent := 0.0
	for _, e := range eligible {
		scn := e.Scn
		remaining := cap - spent
		if scn.BudgetUSD > remaining+1e-9 {
			return fmt.Sprintf("%s の費用の上限 %v USD が、累計の残り %.3f USD（上限 %v USD - 実費 %.3f USD）を超える", scn.ID, scn.BudgetUSD, math.Max(remaining, 0), cap, spent)
		}
		if ok, mode := r.modeCheck(root); !ok {
			return fmt.Sprintf("%s の前に、判定のモードが enable でなくなった\n%s", scn.ID, mode)
		}
		out := filepath.Join(batch, scn.ID)
		top, err := world.RequireDisposableRepo(root, filepath.Join(root, filepath.FromSlash(core.DisposableRel)), "")
		if err == nil {
			err = world.CheckBeforeRun(scn, root, top, r.Manager)
		}
		if err != nil {
			return fmt.Sprintf("%s を回す前に断られた\n%s", scn.ID, err)
		}
		rc, err := r.StartScenario(scn, e.Path, root, top, out)
		if err != nil {
			r.say(err.Error())
		}
		row := scenarioRow(scn, out, root, rc)
		*rows = append(*rows, row)
		spent += row.CostUSD
		r.say(fmt.Sprintf("-- %s: %s（実費 %.3f USD、累計 %.3f USD）", scn.ID, row.Verdict, row.CostUSD, spent))
		if rc == 3 || rc == ExitHuman {
			return fmt.Sprintf("%s が終了コード %d で止まった（対象の状態を手で確かめる）", scn.ID, rc)
		}
	}
	return ""
}

// RunAllOptions は run-all の引数。Confirmed が nil なら確認文を出して止まる。
type RunAllOptions struct {
	Scenarios   []string
	MaxTotalUSD float64
	Confirmed   *string
	Root        string
	Out         string
}

// RunAll は使い捨てのリポジトリのシナリオを、1 回の確認で直列に回す。
func (r *Runner) RunAll(o RunAllOptions) int {
	if !r.gate(false) {
		return 1
	}
	root := r.abs(o.Root)
	if math.IsNaN(o.MaxTotalUSD) || math.IsInf(o.MaxTotalUSD, 0) || o.MaxTotalUSD <= 0 {
		r.say("--max-total-usd は 0 より大きい有限の値にする")
		r.say("次: 実費の累計の上限を USD で渡して打ち直す")
		return 2
	}
	cap := math.Round(o.MaxTotalUSD*1e4) / 1e4
	ok, mode := r.modeCheck(root)
	if !ok {
		r.say(mode)
		return 3
	}
	eligible, skipped := r.RunAllPlan(ExpandScenarioPaths(o.Scenarios), root)
	if len(eligible) == 0 {
		r.say("run-all で回せるシナリオが無い\n対象にしないもの:")
		r.say(strings.Join(report.SkippedLines(skipped), "\n"))
		r.say("次: 対象にしなかった理由を直すか、1 本ずつ `run` で確認して回す")
		return 1
	}
	digest := RunAllDigest(eligible, cap)
	if o.Confirmed == nil {
		r.say(report.RunAllConfirmation(eligible, skipped, cap, digest, mode, root, o.Scenarios))
		return 3
	}
	if *o.Confirmed != digest {
		ids := make([]string, len(eligible))
		for i, e := range eligible {
			ids[i] = e.Scn.ID
		}
		r.say("確認した内容と今回の対象が違う（ID の集合・base・費用の上限・概要のどれかが増えたか減ったか、書き換わった）。回さない")
		r.say(fmt.Sprintf("今の対象: %s（目印 %s）", strings.Join(ids, ", "), digest))
		r.say("案内: 確認した目印は " + *o.Confirmed)
		r.say("次: --confirmed を付けずに打ち直して確認を取り直す")
		return 3
	}
	batch := o.Out
	if batch == "" {
		batch = filepath.Join(r.logsDir(), "run-all-"+r.now().Format("20060102-150405"))
	}
	batch = r.abs(batch)
	if err := os.MkdirAll(batch, 0o755); err != nil {
		r.say(err.Error())
		return 1
	}
	var rows []report.Row
	stopped := r.runAllLoop(eligible, root, cap, batch, &rows)
	done := map[string]bool{}
	for _, row := range rows {
		done[row.ID] = true
	}
	var remaining []string
	for _, e := range eligible {
		if !done[e.Scn.ID] {
			remaining = append(remaining, e.Scn.ID)
		}
	}
	lines, _ := report.WriteRunAllSummary(batch, rows, skipped, cap, remaining, stopped)
	r.say(strings.Join(append([]string{""}, lines...), "\n"))
	r.say(fmt.Sprintf("\n要約: %s/summary.md", batch))
	if stopped != "" {
		r.say(fmt.Sprintf("次: 残りを回すなら、%s run-all を --confirmed なしで打って確認を取り直す", core.Runner))
		return 3
	}
	verdicts := map[string]bool{}
	for _, row := range rows {
		verdicts[row.Verdict] = true
	}
	if verdicts[core.Fail] {
		return 1
	}
	for v := range verdicts {
		if v != core.Pass {
			return 2
		}
	}
	return 0
}

// StubResult は run-stub の 1 本の結果。RC が nil なら回さない（Note が理由）。
type StubResult struct {
	ID   string
	RC   *int
	Note string
}

// RunStub はシナリオごとに使い捨てのワークスペースを組み、スタブ（teststub claude）で回して消す。人間の確認は取らない
// （ユーザのワークスペースに触れず、claude も呼ばない）。
func (r *Runner) RunStub(paths []string) ([]StubResult, error) {
	var results []StubResult
	for _, path := range paths {
		scn, err := core.LoadFile(path, core.AgentClaude)
		if err != nil {
			return results, err
		}
		if why := scn.StubUnready(); why != "" {
			results = append(results, StubResult{ID: scn.ID, Note: why})
			continue
		}
		scn, err = core.LoadFile(path, core.AgentScript)
		if err != nil {
			return results, err
		}
		exe, err := r.Ccnavi()
		if err != nil {
			return results, err
		}
		base, err := world.MakeSandboxBase()
		if err != nil {
			return results, err
		}
		out := filepath.Join(r.logsDir(), fmt.Sprintf("%s-stub-%s", scn.ID, r.now().Format("20060102-150405.000000")))
		ws, err := world.BuildSandbox(r.RepoRoot, base, exe)
		var rc int
		if err == nil {
			rc, err = r.StartScenario(scn, path, ws, "", out)
		}
		_ = os.RemoveAll(base)
		if err != nil {
			return results, fmt.Errorf("%s: %w", scn.ID, err)
		}
		code := rc
		results = append(results, StubResult{ID: scn.ID, RC: &code, Note: out})
	}
	return results, nil
}

// RunStubCommand は run-stub: 使い捨てのワークスペースでスタブで回す。0 全部 pass / 1 fail がある・回せるものが無い /
// 2 未成立がある / 3 後片付けができなかった回がある（run と同じ意味）。
func (r *Runner) RunStubCommand(patterns []string) int {
	results, err := r.RunStub(ExpandScenarioPaths(patterns))
	if err != nil {
		r.say(err.Error())
		return 1
	}
	codes := map[int]bool{}
	ran := 0
	for _, res := range results {
		verdict := "回さない"
		if res.RC != nil {
			ran++
			codes[*res.RC] = true
			switch *res.RC {
			case 0:
				verdict = core.Pass
			case 1:
				verdict = core.Fail
			default:
				verdict = fmt.Sprintf("終了コード %d", *res.RC)
			}
		}
		r.say(fmt.Sprintf("%s\t%s\t%s", res.ID, verdict, res.Note))
	}
	if ran == 0 {
		r.say("使い捨てのワークスペースでスタブで回せるシナリオが無い")
		return 1
	}
	for _, rc := range []int{1, 3, 2} { // fail を先に、後片付けができなかった回（3）を未成立（2）より先に知らせる
		if codes[rc] {
			return rc
		}
	}
	if len(codes) == 1 && codes[0] {
		return 0
	}
	return 2
}
