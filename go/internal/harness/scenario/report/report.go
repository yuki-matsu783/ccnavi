package report

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/harness/scenario/judge"
	"ccnavi/internal/harness/scenario/world"
)

// HostNotes は相手（setup.host）ごとの、報告に出す説明。
var HostNotes = map[host.Kind]string{
	host.Real:         "real: 本物の GitHub / GitLab を相手にした",
	host.Bare:         "bare: ローカルの bare を origin にした。ホストの API は失敗（確かめられなかった）",
	host.FakeGitLab:   "fake-gitlab: ローカルの bare と fakegitlab の模擬を相手にした",
	host.DockerGitLab: "docker-gitlab: Docker の本物の GitLab CE を相手にした。gh / glab は使えず curl で呼ぶ。CE にない機能（変更要求など）は確かめられない",
}

// FakeNote は模擬を相手にしたときに足す注意。
const FakeNote = "。模擬は本物と同じ形を返す保証が無く、模擬のずれで見逃した問題がありうる"

// HostNote は相手の説明（模擬には注意を足す）。
func HostNote(k host.Kind) string {
	if host.IsFake(k) {
		return HostNotes[k] + FakeNote
	}
	return HostNotes[k]
}

// SnapshotPlan は相手側の状態の記録の予告。
const SnapshotPlan = "相手側の状態: step の終わりごとに MR・スレッド・承認・issue を API で読み、" +
	"出力の置き場の host-N.json に残す（読むだけ。トークンは伏せる）"

// SnapshotNote は report の host の行に足す、相手側の状態の記録の要約。記録が無い相手には空。
func SnapshotNote(st *core.State) string {
	if len(st.HostSnapshots) == 0 {
		return ""
	}
	text := fmt.Sprintf("相手側の状態: step ごとに host-N.json へ残した（%d 件。承認は MR ごとに読んだ）", len(st.HostSnapshots))
	var bad []string
	for _, s := range st.HostSnapshots {
		if !s.OK {
			bad = append(bad, fmt.Sprint(s.Step))
		}
	}
	if len(bad) > 0 {
		text += "。取れなかった所がある step: " + strings.Join(bad, "、")
	}
	return text
}

// Cell は表のセルに入れる文字列から、列と行を崩す文字を除く。
func Cell(s string) string {
	return strings.ReplaceAll(strings.ReplaceAll(s, "|", "\\|"), "\n", " ")
}

func jsonText(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return judgeDumps(v)
}

func judgeDumps(v any) string {
	data, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprint(v)
	}
	return strings.ReplaceAll(string(data), `<`, "<")
}

func cut(s string, limit int) string {
	if limit <= 0 {
		return s
	}
	r := []rune(s)
	if len(r) > limit {
		return string(r[:limit])
	}
	return s
}

// ResultsTable は結果の表。limit が正なら、期待と理由のセルをその字数で切る。
func ResultsTable(results []core.Result, limit int) []string {
	lines := []string{"| 種類 | 期待 | 判定 | 理由 |", "|---|---|---|---|"}
	for _, r := range results {
		lines = append(lines, fmt.Sprintf("| %s | %s | %s | %s |", Cell(r.Kind), Cell(cut(jsonText(r.Expect), limit)), Cell(r.Verdict), Cell(cut(r.Why, limit))))
	}
	return lines
}

func oneLine(text string, limit int) string {
	s := strings.Join(strings.Fields(text), " ")
	if r := []rune(s); len(r) > limit {
		return string(r[:limit]) + "…"
	}
	return s
}

func stepDecisionsTable(rows []judge.Row, stepSessions map[int][]string) []string {
	lines := []string{"| step | event | tool | decision | code | rules | subject |", "|---|---|---|---|---|---|---|"}
	for _, r := range rows {
		rules := ""
		switch v := r["rules"].(type) {
		case []any:
			parts := make([]string, len(v))
			for i, x := range v {
				parts[i] = fmt.Sprint(x)
			}
			rules = strings.Join(parts, ",")
		case nil:
		default:
			rules = fmt.Sprint(v)
		}
		str := func(k string) string {
			s, _ := r[k].(string)
			return s
		}
		cells := []string{judge.StepLabel(r, stepSessions), str("event"), str("tool"), str("decision"), str("code"), rules,
			core.Clip(str("subject"), 500, "logs/decisions.jsonl")}
		for i := range cells {
			cells[i] = Cell(cells[i])
		}
		lines = append(lines, "| "+strings.Join(cells, " | ")+" |")
	}
	return lines
}

// AnswerLimit は step の最終出力を報告に出すときの上限。
const AnswerLimit = 1500

// StepAnswerLines は step ごとの入力プロンプトと、claude の最後の出力を報告に出す行。人間の操作の step は、その指示を出す。
func StepAnswerLines(scn *core.Scenario, out string) []string {
	var lines []string
	for i, step := range scn.Steps {
		num := i + 1
		lines = append(lines, "")
		if step.Human {
			lines = append(lines, fmt.Sprintf("### step %d（人間の操作）", num), oneLine(step.Instruction, 200))
			if step.HumanRun != nil {
				lines = append(lines, "実行器が打つ: "+step.HumanRun.Text())
			}
			if len(step.HumanAPI) > 0 {
				ops := make([]string, len(step.HumanAPI))
				for j, o := range step.HumanAPI {
					ops[j] = o.Text()
				}
				lines = append(lines, "人間役の API: "+strings.Join(ops, "、"))
			}
			continue
		}
		lines = append(lines, fmt.Sprintf("### step %d", num), "【入力プロンプト】", strings.TrimSpace(step.Prompt), "【最終出力】")
		path := filepath.Join(out, fmt.Sprintf("step-%d.jsonl", num))
		data, err := os.ReadFile(path)
		if err != nil {
			lines = append(lines, "未実行")
			continue
		}
		final := core.Clip(core.ParseStream(string(data)).FinalText, AnswerLimit, filepath.Base(path))
		if final == "" {
			final = "（出力なし）"
		}
		lines = append(lines, final)
	}
	return lines
}

const trendShown = 10

func shown(lines []string, more int) []string {
	if more > 0 {
		lines = append(lines, fmt.Sprintf("- ほか %d 件", more))
	}
	return lines
}

// TrendLines は拒否の傾向の節（report.md）。人が読む目印で、判定ではない。
func TrendLines(out string, rows []judge.Row) []string {
	ss := judge.ReadStepStreams(out)
	sessions := ss.StepSessions()
	lines := []string{"", "## 拒否の傾向（目印。判定ではない）", "誤検知と見逃しがある。定義と限界は acceptance-scenarios.md の「証拠の読み方」。"}
	repeats := judge.RepeatedDenials(rows, sessions)
	lines = append(lines, "", fmt.Sprintf("### 反復拒否（同じ code の deny が間に allow なしで %d 回以上）", judge.RepeatMin))
	n := 0
	for _, r := range repeats {
		if n++; n > trendShown {
			break
		}
		code := r.Code
		if code == "" {
			code = "(code なし)"
		}
		subjects := r.Subjects[:min(3, len(r.Subjects))]
		lines = append(lines, fmt.Sprintf("- step %s: %s が %d 回連続。対象: %s", r.Step, code, r.Count, oneLine(strings.Join(subjects, ", "), 200)))
	}
	if len(repeats) == 0 {
		lines = append(lines, "- なし")
	}
	lines = shown(lines, len(repeats)-trendShown)
	bypass := judge.BypassSuspects(rows, ss.Streams, sessions)
	lines = append(lines, "", "### 迂回の疑い（deny された Edit / Write と同じパスへの、その後の Edit / Write）")
	for i, b := range bypass {
		if i >= trendShown {
			break
		}
		lines = append(lines, fmt.Sprintf("- step %s: %s が %d 回拒否されたあと、同じパスへの Edit / Write が全 %d 回", b.Step, b.Path, b.Denied, b.Calls))
	}
	if len(bypass) == 0 {
		lines = append(lines, "- なし")
	}
	lines = shown(lines, len(bypass)-trendShown)
	bare := judge.UnguidedDenials(ss)
	lines = append(lines, "", "### 案内の無い拒否（理由文に次の手を示す語・コード片・`次:` などの行が無い）")
	for i, b := range bare {
		if i >= trendShown {
			break
		}
		lines = append(lines, fmt.Sprintf("- step %d [%s]: %s", b.Step, b.Label, oneLine(b.Text, 120)))
	}
	if len(bare) == 0 {
		lines = append(lines, "- なし")
	}
	return shown(lines, len(bare)-trendShown)
}

// WriteMessages は messages.md を書く。日本語の不自然さを人が読む材料で、機械の判定はしない。
func WriteMessages(out string, rows []judge.Row) error {
	msgs := judge.CollectMessages(judge.ReadStepStreams(out), rows)
	lines := []string{"# 文面の一覧（hook の応答）", "",
		"拒否・確認・案内の文面。パスと数字だけが違うものは 1 件にまとめた" +
			"（コミットの ID は `<SHA>`、パスは `<PATH>`、数字は `<N>` に置き換えて比べる）。" +
			"出どころは decisions.jsonl の行と順に突き合わせたもので、数が合わないときは出さない。"}
	for n, m := range msgs {
		steps := make([]string, len(m.Steps))
		for i, s := range m.Steps {
			steps[i] = fmt.Sprint(s)
		}
		src := strings.Join(m.Src, "; ")
		if src == "" {
			src = "不明"
		}
		lines = append(lines, "", fmt.Sprintf("## %d. %s（%d 回）", n+1, m.Kind, m.Count),
			fmt.Sprintf("- 事象: %s / step: %s", strings.Join(m.Events, ", "), strings.Join(steps, ", ")),
			fmt.Sprintf("- 出どころ（code / rules）: %s", src))
		lines = append(lines, judge.Fence(m.Text)...)
	}
	if len(msgs) == 0 {
		lines = append(lines, "", "なし")
	}
	return os.WriteFile(filepath.Join(out, "messages.md"), []byte(strings.Join(lines, "\n")+"\n"), 0o644)
}

// WriteReport は report.md・report.json・evidence.md・messages.md を out に書く。
func WriteReport(scn *core.Scenario, out string, st *core.State, results []core.Result, rows []judge.Row) error {
	lines := []string{
		fmt.Sprintf("# %s %s", scn.ID, scn.Title), "",
		fmt.Sprintf("全体: %s  費用: %.3f USD", judge.Overall(results), st.CostUSD), "",
		"相手（setup.host）: " + HostNote(scn.HostKind()), "",
	}
	if n := SnapshotNote(st); n != "" {
		lines = append(lines, n, "")
	}
	lines = append(lines, ResultsTable(results, 100)...)
	lines = append(lines, "", "## ステップごとの入力プロンプトと最終出力")
	lines = append(lines, StepAnswerLines(scn, out)...)
	if len(scn.Unconfirmed) > 0 {
		lines = append(lines, "", "## 確かめる点（unconfirmed）")
		for _, u := range scn.Unconfirmed {
			lines = append(lines, "- "+u)
		}
	}
	lines = append(lines, TrendLines(out, rows)...)
	if err := os.WriteFile(filepath.Join(out, "report.md"), []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		return err
	}
	cost := st.CostUSD
	recorded := append(append([]core.Result{}, results...), core.Result{Kind: "cost", Expect: "total_cost_usd", Verdict: core.Pass,
		Why: fmt.Sprintf("%.4f USD", cost), CostUSD: &cost})
	data, err := json.MarshalIndent(recorded, "", " ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(out, "report.json"), data, 0o644); err != nil {
		return err
	}
	ev, err := evidenceLines(scn, out, st, results, rows)
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(out, "evidence.md"), []byte(strings.Join(ev, "\n")+"\n"), 0o644); err != nil {
		return err
	}
	return WriteMessages(out, rows)
}

func evidenceLines(scn *core.Scenario, out string, st *core.State, results []core.Result, rows []judge.Row) ([]string, error) {
	ev := []string{"# 証拠（意味の判定用）", "", "## 機械判定の結果", "", "全体: " + judge.Overall(results), ""}
	ev = append(ev, ResultsTable(results, 0)...)
	ev = append(ev, "", "judge の基準:")
	for _, c := range scn.Expect.Judge {
		ev = append(ev, "- "+c)
	}
	if len(st.HumanOps) > 0 {
		ev = append(ev, "", "## 人間役の操作")
		for _, o := range st.HumanOps {
			extra := ""
			if o.DiscussionID != "" {
				extra = " discussion " + o.DiscussionID
			}
			name := ""
			if n := firstNonEmpty(o.As, o.Thread); n != "" {
				name = "（" + n + "）"
			}
			line := fmt.Sprintf("- step %d: %s%s → HTTP %d%s", o.Step, o.Op, name, o.Status, extra)
			if o.Error != "" {
				line += " 失敗: " + o.Error
			}
			ev = append(ev, line)
		}
	}
	if len(st.HumanRuns) > 0 {
		ev = append(ev, "", "## 人間役の操作（端末）")
		for _, r := range st.HumanRuns {
			line := fmt.Sprintf("- step %d: sh %s %s → %s", r.Step, r.Script, strings.Join(r.Tickets, " "), r.Status)
			if r.Exit != nil {
				line += fmt.Sprintf("（終了コード %d、ログ: %s）", *r.Exit, r.Log)
			}
			ev = append(ev, line)
		}
	}
	ss := judge.ReadStepStreams(out)
	for _, num := range ss.Nums {
		parsed := ss.Streams[num]
		name := fmt.Sprintf("step-%d", num)
		file := name + ".jsonl"
		ev = append(ev, "", "## "+name)
		if num > 0 && num <= len(scn.Steps) && scn.Steps[num-1].Prompt != "" {
			ev = append(ev, "### 入力プロンプト", strings.TrimSpace(scn.Steps[num-1].Prompt))
		}
		ev = append(ev, "### ツール呼び出し")
		for _, t := range parsed.ToolCalls {
			input, _ := json.Marshal(t.Input)
			ev = append(ev, fmt.Sprintf("- %s: %s", t.Name, core.Clip(string(input), 500, file)))
		}
		denials := judge.HookDenialTexts(parsed.HookEvents)
		var okLines, errLines []string
		for _, r := range parsed.ToolResults {
			text := core.TextOf(r.Content)
			switch {
			case !r.IsError:
				okLines = append(okLines, "- "+core.Clip(text, judge.EvidenceLimit, file))
			case judge.DuplicatesHook(text, denials):
				errLines = append(errLines, "- hook の応答を参照（下の「hook の応答」と同じ文）")
			default:
				errLines = append(errLines, "- "+core.Clip(text, judge.EvidenceLimit, file))
			}
		}
		if okLines == nil {
			okLines = []string{"- なし"}
		}
		if errLines == nil {
			errLines = []string{"- なし"}
		}
		ev = append(ev, "### ツール結果（成功）")
		ev = append(ev, okLines...)
		ev = append(ev, "### ツール結果（エラー）")
		ev = append(ev, errLines...)
		hookLines, omitted := judge.SummarizeHooks(parsed.HookEvents, file)
		ev = append(ev, "### hook の応答（拒否・確認・案内文・Stop だけ）")
		ev = append(ev, hookLines...)
		if len(omitted) > 0 {
			var keys []string
			for k := range omitted {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			counts := make([]string, len(keys))
			for i, k := range keys {
				counts[i] = fmt.Sprintf("%s %d 件", k, omitted[k])
			}
			ev = append(ev, fmt.Sprintf("- 省いた応答: %s（全量は %s）", strings.Join(counts, "、"), file))
		}
		ev = append(ev, "### 最後の報告", core.Clip(parsed.FinalText, 3000, file))
		ctx, err := judge.ContextLines(parsed, num, out)
		if err != nil {
			return nil, err
		}
		ev = append(ev, ctx...)
	}
	ev = append(ev, "", "## ccnavi の判定の記録（このセッションの行）", "",
		"列: step=どの step の行か / event=hook の種類 / tool=対象のツール / decision=判定（allow・deny・ask など）/"+
			" code=判定の理由コード / rules=ヒットしたルール / subject=判定の対象（コマンドやパス）。"+
			"UNDECLARED はハンドオーバーに宣言が無いことを示す code。", "")
	ev = append(ev, stepDecisionsTable(rows, ss.StepSessions())...)
	return ev, nil
}

func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

func condText(c core.Cond) string {
	var parts []string
	for _, kv := range [][2]string{{"event", c.Event}, {"tool", c.Tool}, {"decision", c.Decision}, {"code", c.Code}} {
		if kv[1] != "" {
			parts = append(parts, kv[0]+"="+kv[1])
		}
	}
	if len(c.RulesInclude) > 0 {
		parts = append(parts, "ルール∋"+strings.Join(c.RulesInclude, ", "))
	}
	if c.SubjectContains != "" {
		parts = append(parts, "対象∋"+c.SubjectContains)
	}
	return strings.Join(parts, " ")
}

func shellJoin(argv []string) string {
	out := make([]string, len(argv))
	for i, a := range argv {
		if a == "" || strings.ContainsAny(a, " \t\n'\"\\$`!*?[]{}()<>|&;#~") {
			a = "'" + strings.ReplaceAll(a, "'", `'"'"'`) + "'"
		}
		out[i] = a
	}
	return strings.Join(out, " ")
}

// ScenarioSummary は回す前に人間へ見せる、シナリオの概要（何をさせ、何を期待し、何を残すか）。
func ScenarioSummary(scn *core.Scenario) string {
	dash := func(s string) string {
		if s == "" {
			return "-"
		}
		return s
	}
	reqs := dash(strings.Join(scn.Reqs, ", "))
	budget := "-"
	if scn.BudgetUSD != 0 {
		budget = fmt.Sprint(scn.BudgetUSD)
	}
	lines := []string{fmt.Sprintf("## 受け入れシナリオ %s: %s", scn.ID, scn.Title),
		fmt.Sprintf("層 %s・要求 %s・状態 %s・モデル %s・費用の上限 %s USD", dash(scn.Layer), reqs, dash(scn.Status), dash(scn.Model), budget)}
	if scn.IsScripted() {
		lines = append(lines, "相手: スタブ（teststub claude）が step の script どおりに打つ。claude は呼ばず、費用はかからない。judge は評価しない")
	}
	if scn.TestRepo {
		lines = append(lines, "作るもの: projects/test-repo にブランチ・コミット・MR が残る（後で片付ける）")
	} else {
		lines = append(lines, "作るもの: なし。ccnavi 本体に何か残らないことは、回したあと repo の判定で確かめる")
	}
	var made, appended []string
	for _, f := range scn.Setup.Files {
		if f.Append {
			appended = append(appended, f.Rel)
		} else {
			made = append(made, f.Rel)
		}
	}
	if len(made) > 0 {
		lines = append(lines, "回す前に作り、回したあとに消すファイル: "+strings.Join(made, ", "))
	}
	if len(appended) > 0 {
		lines = append(lines, "回す前に追記する、対象にある管理下のファイル（消さない。戻すのは setup.reset）: "+strings.Join(appended, ", "))
	}
	for _, argv := range scn.Setup.Git {
		lines = append(lines, "回す前に対象で打つ git: "+shellJoin(argv))
	}
	if scn.Setup.ResetBase != "" {
		lines = append(lines, fmt.Sprintf("回す前と回したあとに、%s を %s へ戻す（未コミットの変更・main 以外のブランチ・ワークツリー・タグ・stash が消える）。"+
			"人間の操作で止めた回は、戻さずに残す（続きは next、やめるのは restore）", core.DisposableRel, scn.Setup.ResetBase))
	}
	k := scn.HostKind()
	if host.IsFake(k) {
		lines = append(lines, fmt.Sprintf("相手（setup.host）: %s。ホスト（GitHub / GitLab）へは出ない（`claude -p` 自身の API 通信は要る）。"+
			"本物のトークンは claude に渡さない。origin への通信は一時ディレクトリの bare へ向き、回したあとに消す%s", k, FakeNote))
		if k == host.FakeGitLab {
			lines = append(lines, SnapshotPlan)
		}
	}
	if k == host.DockerGitLab {
		lines = append(lines, "相手（setup.host）: docker-gitlab。Docker の GitLab（127.0.0.1）に使い捨てのプロジェクトを作り（名前は sc-<シナリオ ID>-…）、"+
			"人間役をメンバーに加えて main を push する。回したあとにプロジェクトを削除する（人間の操作で止めた回は残す）。"+
			"エージェント役のトークンは claude に渡し、人間役のトークンは渡さない", SnapshotPlan)
	}
	if issue := scn.Setup.Issue; issue != nil {
		closeText := "（閉じない）"
		if issue.Close {
			closeText = "（回したあとに閉じる）"
		}
		lines = append(lines, fmt.Sprintf("回す前に %s へ issue を 1 つ作り、prompt の %s に番号を入れる%s", issue.Repo, world.IssuePlaceholder, closeText))
	}
	lines = append(lines, "手順:")
	for i, step := range scn.Steps {
		if step.Human {
			text := oneLine(step.Instruction, 120)
			if len(step.HumanAPI) > 0 {
				ops := make([]string, len(step.HumanAPI))
				for j, o := range step.HumanAPI {
					ops[j] = o.Text()
				}
				text = strings.TrimLeft(fmt.Sprintf("%s（人間役の API: %s。止まらずに進む）", text, strings.Join(ops, "、")), " ")
			}
			if step.HumanRun != nil {
				text = fmt.Sprintf("実行器が打つ: %s。止まらずに進む（手で打つのは端末を作れないときだけ: %s）", step.HumanRun.Text(), text)
			}
			lines = append(lines, fmt.Sprintf("  %d. 人間の操作: %s", i+1, text))
		} else {
			lines = append(lines, fmt.Sprintf("  %d. %s", i+1, oneLine(step.Prompt, 120)))
		}
	}
	lines = append(lines, "期待:")
	for _, c := range scn.Expect.Decisions {
		lines = append(lines, "  - 判定が出ている: "+condText(c))
	}
	for _, c := range scn.Expect.DecisionsNone {
		lines = append(lines, "  - 出ていてはいけない判定: "+condText(c))
	}
	for _, rel := range scn.Expect.FilesUnchanged {
		lines = append(lines, "  - 変わっていないこと: "+rel)
	}
	for _, item := range scn.Expect.State {
		text := "`" + item.Run + "`"
		if item.ExpectContains != nil {
			text += " の出力に「" + *item.ExpectContains + "」が出る"
		}
		if item.ExpectNotContains != nil {
			text += " の出力に「" + *item.ExpectNotContains + "」が出ない"
		}
		lines = append(lines, "  - 最後の状態: "+text)
	}
	for _, s := range scn.Expect.TranscriptForbidden {
		lines = append(lines, "  - 出力に出てはいけない: "+s)
	}
	for _, c := range scn.Expect.Judge {
		lines = append(lines, "  - 意味で読む: "+c)
	}
	if len(scn.Unconfirmed) > 0 {
		lines = append(lines, "まだ実ログと突き合わせていない点:")
		for _, u := range scn.Unconfirmed {
			lines = append(lines, "  - "+u)
		}
	}
	return strings.Join(lines, "\n")
}
