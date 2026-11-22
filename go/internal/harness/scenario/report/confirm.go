package report

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/harness/scenario/prepare"
	"ccnavi/internal/harness/scenario/world"
)

// ConfirmationText は回す前に人間へ見せる文。順は、事実 → ワークスペース状態 → ユーザ確認事項。
// run の確認と preflight で同じものを出す。m は docker-gitlab の確認に使う（無ければ nil）。
func ConfirmationText(scn *core.Scenario, root, modeText string, m *host.Manager) string {
	facts := []string{ScenarioSummary(scn), "", modeText}
	if clash := prepare.Clashes(scn.Setup.Files, root); len(clash) > 0 {
		facts = append(facts, "", "注意: setup.files のパスが合わない（作る形は既にある・追記の形は無い。run は回さない）: "+strings.Join(clash, ", "))
	}
	if scn.UsesTarget() {
		top, err := world.RequireDisposableRepo(root, filepath.Join(root, filepath.FromSlash(core.DisposableRel)), "")
		if err == nil {
			err = world.CheckBeforeRun(scn, root, top, m)
		}
		var refusal *core.Refusal
		switch {
		case err == nil:
			facts = append(facts, "", fmt.Sprintf("対象のリポジトリ: %s（使い捨てと確かめた）", top))
		case errors.As(err, &refusal):
			facts = append(facts, "", "注意: 対象のリポジトリを使えない（run は回さない）\n"+err.Error())
		default:
			facts = append(facts, "", "注意: 対象のリポジトリを使えない（run は回さない）\n"+err.Error())
		}
	}
	questions := []string{"確認事項 1: ワークツリーに未コミットの変更がなく、origin と同期しているか（他のセッションが作業中の変更を含まないか）"}
	if scn.TestRepo {
		questions = append(questions, "確認事項 2: projects/test-repo にブランチや MR ができてよいか")
	}
	if scn.Setup.ResetBase != "" {
		questions = append(questions, fmt.Sprintf("確認事項 3: %s の未コミットの変更・main 以外のブランチ・ワークツリー・タグ・stash を、"+
			"回す前と回したあとに消してよいか（setup.reset）", core.DisposableRel))
	}
	lines := []string{"回す前に、人間に次を確認する。確認が取れたら --confirmed を付けて打ち直す。", "", "# 1. 事実", ""}
	lines = append(lines, facts...)
	lines = append(lines, "", "# 2. ワークスペース状態", "", world.PreflightFacts(root), "", "# 3. ユーザ確認事項", "")
	return strings.Join(append(lines, questions...), "\n")
}

// Skip は run-all の対象にしなかったもの（パスか ID と、理由）。
type Skip struct{ Who, Why string }

// SkippedLines は対象にしなかったものの行。
func SkippedLines(skipped []Skip) []string {
	if len(skipped) == 0 {
		return []string{"- なし"}
	}
	out := make([]string, len(skipped))
	for i, s := range skipped {
		out[i] = fmt.Sprintf("- %s: %s", s.Who, s.Why)
	}
	return out
}

// Eligible は run-all の対象（シナリオのパスと、読んだシナリオ）。
type Eligible struct {
	Path string
	Scn  *core.Scenario
}

func quote(a string) string {
	if a != "" && !strings.ContainsAny(a, " \t\n'\"\\$`!*?[]{}()<>|&;#~") {
		return a
	}
	return "'" + strings.ReplaceAll(a, "'", `'"'"'`) + "'"
}

// RunAllConfirmation は run-all の確認文。全シナリオに対する 1 回の確認で、回す ID の一覧・概要・作るもの・対象の状態・
// base・費用の上限を見せる。
func RunAllConfirmation(eligible []Eligible, skipped []Skip, cap float64, digest, modeText, root string, scenarioArgs []string) string {
	budget := 0.0
	table := []string{"| ID | タイトル | base | モデル | 1 本の上限 (USD) |", "|---|---|---|---|---|"}
	anyHost := false
	var summaries []string
	for _, e := range eligible {
		budget += e.Scn.BudgetUSD
		table = append(table, fmt.Sprintf("| %s | %s | %s | %s | %v |", e.Scn.ID, Cell(e.Scn.Title), e.Scn.Setup.ResetBase[:12], e.Scn.Model, e.Scn.BudgetUSD))
		summaries = append(summaries, ScenarioSummary(e.Scn)+"\n")
		anyHost = anyHost || e.Scn.TestRepo || e.Scn.Setup.Issue != nil
	}
	questions := []string{
		"確認事項 1: ワークツリーに未コミットの変更がなく、origin と同期しているか（他のセッションが作業中の変更を含まないか）",
		fmt.Sprintf("確認事項 2: %s の未コミットの変更・main 以外のブランチ・ワークツリー・タグ・stash を、各シナリオの前と後に消してよいか（setup.reset。base は上の表）", core.DisposableRel),
	}
	if anyHost {
		questions = append(questions, fmt.Sprintf("確認事項 3: %s にブランチや MR ができてよいか（setup.issue のシナリオは、本物のホストに issue も作る）", core.DisposableRel))
	}
	questions = append(questions, fmt.Sprintf("確認事項 4: 実費の累計 %v USD までを使ってよいか（1 本の上限の合計は %.2f USD。累計の残りを 1 本の上限が超えるシナリオは回さない）", cap, budget))
	quoted := make([]string, len(scenarioArgs))
	for i, a := range scenarioArgs {
		quoted[i] = quote(a)
	}
	command := fmt.Sprintf("%s run-all %s --max-total-usd %v --confirmed %s", core.Runner, strings.Join(quoted, " "), cap, digest)
	lines := []string{
		"回す前に、人間に次を 1 回だけ確認する（シナリオごとには取り直さない）。確認が取れたら、最後の打ち方で回す。", "",
		"この確認が有効なのは、下の ID の集合・base・費用の上限・概要に限る。" +
			"実行の直前に作り直した目印が違えば、回さずに止まる（増減・書き換えがあれば取り直す）。", "",
		fmt.Sprintf("# 1. 回す一覧（%d 本。直列。ID 順）", len(eligible)), ""}
	lines = append(lines, table...)
	lines = append(lines, "", "# 2. 概要と作るもの", "")
	lines = append(lines, summaries...)
	lines = append(lines, fmt.Sprintf("# 3. 対象にしないもの（%d 件。回さない）", len(skipped)), "")
	lines = append(lines, SkippedLines(skipped)...)
	lines = append(lines, "", "# 4. 事実", "", modeText, "",
		fmt.Sprintf("対象のリポジトリ: %s（使い捨てと確かめた）", filepath.Join(root, filepath.FromSlash(core.DisposableRel))), "",
		world.PreflightFacts(root), "# 5. ユーザ確認事項", "")
	lines = append(lines, questions...)
	lines = append(lines, "", "# 6. 回す打ち方（確認の目印つき）", "", command)
	return strings.Join(lines, "\n")
}

// Row は run-all の表の 1 行。
type Row struct {
	ID        string  `json:"id"`
	RC        int     `json:"rc"`
	Verdict   string  `json:"verdict"`
	CostUSD   float64 `json:"cost_usd"`
	ToolCalls int     `json:"tool_calls"`
	Denies    int     `json:"denies"`
	Asks      int     `json:"asks"`
	Out       string  `json:"out"`
}

// RunAllTable は run-all の結果の表。
func RunAllTable(rows []Row) []string {
	lines := []string{"| ID | 判定 | 実費 (USD) | ツール呼び出し | 拒否 | ask | 出力 |", "|---|---|---|---|---|---|---|"}
	for _, r := range rows {
		lines = append(lines, fmt.Sprintf("| %s | %s | %.3f | %d | %d | %d | %s |", r.ID, r.Verdict, r.CostUSD, r.ToolCalls, r.Denies, r.Asks, r.Out))
	}
	return lines
}

// WriteRunAllSummary は summary.md・summary.json を batch に書き、summary.md の行を返す。
func WriteRunAllSummary(batch string, rows []Row, skipped []Skip, cap float64, remaining []string, stopped string) ([]string, error) {
	total := 0.0
	for _, r := range rows {
		total += r.CostUSD
	}
	lines := []string{"# run-all の結果", "", fmt.Sprintf("回した本数: %d  実費の累計: %.3f USD（上限 %v USD）", len(rows), total, cap), ""}
	lines = append(lines, RunAllTable(rows)...)
	if stopped != "" {
		rest := "なし"
		if len(remaining) > 0 {
			rest = strings.Join(remaining, ", ")
		}
		lines = append(lines, "", "## 止まった: "+stopped, fmt.Sprintf("回した本数 %d。残りの ID: %s", len(rows), rest))
	}
	lines = append(lines, "", "## 対象にしなかったもの")
	lines = append(lines, SkippedLines(skipped)...)
	if err := os.WriteFile(filepath.Join(batch, "summary.md"), []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		return lines, err
	}
	type skip struct {
		Who string `json:"who"`
		Why string `json:"why"`
	}
	sk := []skip{}
	for _, s := range skipped {
		sk = append(sk, skip{s.Who, s.Why})
	}
	if remaining == nil {
		remaining = []string{}
	}
	data, err := json.MarshalIndent(map[string]any{
		"max_total_usd": cap, "total_cost_usd": total, "stopped": stopped, "remaining": remaining, "scenarios": rows, "skipped": sk,
	}, "", " ")
	if err != nil {
		return lines, err
	}
	return lines, os.WriteFile(filepath.Join(batch, "summary.json"), data, 0o644)
}
