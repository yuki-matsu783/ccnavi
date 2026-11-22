package report

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/harness/scenario/judge"
)

type obj = map[string]any

func js(v any) string { data, _ := json.Marshal(v); return string(data) }

func hookLine(event, name string, payload any) string {
	return js(obj{"type": "system", "subtype": "hook_response", "hook_event": event, "hook_name": name, "output": js(payload), "session_id": "s1"})
}

func denyLine(reason string) string {
	return hookLine("PreToolUse", "PreToolUse:Edit", obj{"hookSpecificOutput": obj{"permissionDecision": "deny", "permissionDecisionReason": reason}})
}

func makeOut(t *testing.T, steps ...string) string {
	t.Helper()
	dir := base.TempDir(t)
	for i, text := range steps {
		if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("step-%d.jsonl", i+1)), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func files(t *testing.T, scn *core.Scenario, out string, rows []judge.Row, results ...core.Result) map[string]string {
	t.Helper()
	if err := WriteReport(scn, out, &core.State{}, results, rows); err != nil {
		t.Fatal(err)
	}
	got := map[string]string{}
	for _, name := range []string{"report.md", "evidence.md", "messages.md", "report.json"} {
		data, err := os.ReadFile(filepath.Join(out, name))
		if err != nil {
			t.Fatal(err)
		}
		got[name] = string(data)
	}
	return got
}

func simple() *core.Scenario {
	return &core.Scenario{ID: "UC-99", Title: "t", Steps: []core.Step{{Prompt: "  ワークツリーは切らない  "}}}
}

func TestScenarioSummaryShowsTitleStepsAndExpectations(t *testing.T) {
	t.Parallel()
	scn, err := core.LoadFile(filepath.Join(base.Root(), "tests", "scenarios", "UC-01-direct-edit-denied.yml"), core.AgentClaude)
	if err != nil {
		t.Fatal(err)
	}
	text := ScenarioSummary(scn)
	for _, want := range []string{"UC-01", "ワークツリーを切らずに", "DENY_PATH", "projects/test-repo/README.md", "ccnavi 本体"} {
		if !strings.Contains(text, want) {
			t.Errorf("概要に %q が無い", want)
		}
	}
}

func TestSummaryShowsHumanStepsUnconfirmedAndSeparatesCreatedFromAppended(t *testing.T) {
	t.Parallel()
	x := "x"
	scn := &core.Scenario{
		ID: "UC-XX", Title: "t", Layer: "3", TestRepo: true, Unconfirmed: []string{"点"},
		Steps:  []core.Step{{Prompt: "a"}, {Human: true, Instruction: "確認して"}},
		Expect: core.Expect{State: []core.StateCheck{{Run: "true", ExpectContains: &x}}},
		Setup: core.Setup{
			Files:     []core.File{{Rel: "new.txt", Body: "x"}, {Rel: "projects/test-repo/README.md", Body: "y", Append: true}},
			Git:       [][]string{{"git", "switch", "-c", "feat"}},
			ResetBase: strings.Repeat("a", 40),
		},
	}
	text := ScenarioSummary(scn)
	for _, want := range []string{"2. 人間の操作: 確認して", "projects/test-repo にブランチ・コミット・MR", "「x」が出る", "まだ実ログと突き合わせていない点",
		"回す前に作り、回したあとに消すファイル: new.txt", "回す前に追記する、対象にある管理下のファイル", "git switch -c feat", strings.Repeat("a", 40), "人間の操作で止めた回は、戻さずに残す"} {
		if !strings.Contains(text, want) {
			t.Errorf("概要に %q が無い", want)
		}
	}
	if !strings.Contains(strings.SplitN(text, "追記する", 2)[1], "projects/test-repo/README.md") {
		t.Error("追記の行にファイルが出ていない")
	}
}

func TestSummarySaysWhoTheOtherSideIs(t *testing.T) {
	t.Parallel()
	scn := simple()
	scn.Setup.Host = host.Bare
	text := ScenarioSummary(scn)
	if !strings.Contains(text, "相手（setup.host）: bare") || !strings.Contains(text, "本物のトークンは claude に渡さない") {
		t.Errorf("= %s", text)
	}
	if strings.Contains(ScenarioSummary(simple()), "setup.host") {
		t.Error("host を書かなければ出さない")
	}
	if n := HostNote(host.DockerGitLab); !strings.Contains(n, "docker-gitlab") || strings.Contains(n, "模擬のずれ") {
		t.Errorf("docker-gitlab は模擬ではない: %s", n)
	}
}

func TestStepAnswerLinesShowEachInputAndFinalOutput(t *testing.T) {
	t.Parallel()
	scn := &core.Scenario{Steps: []core.Step{{Prompt: "一つ目の質問"}, {Human: true, Instruction: "手で確かめて"}, {Prompt: "三つ目"}}}
	out := makeOut(t, js(obj{"type": "result", "result": "一つ目の答え", "session_id": "s1"})+"\n")
	text := strings.Join(StepAnswerLines(scn, out), "\n")
	for _, want := range []string{"【入力プロンプト】\n一つ目の質問", "【最終出力】\n一つ目の答え", "### step 2（人間の操作）", "手で確かめて", "### step 3\n【入力プロンプト】\n三つ目\n【最終出力】\n未実行"} {
		if !strings.Contains(text, want) {
			t.Errorf("%q が無い:\n%s", want, text)
		}
	}
}

func TestConfirmationTextHasSummaryModeFactsAndQuestionsInOrder(t *testing.T) {
	t.Parallel()
	scn := &core.Scenario{ID: "UC-XX", Title: "t", Steps: []core.Step{{Prompt: "a"}}, TestRepo: true}
	text := ConfirmationText(scn, base.TempDir(t), "MODE OK", nil)
	for _, want := range []string{"UC-XX", "MODE OK", "## ワークスペースルート: 置き場が無い", "確認事項 1", "確認事項 2"} {
		if !strings.Contains(text, want) && want != "## ワークスペースルート: 置き場が無い" {
			t.Errorf("確認文に %q が無い", want)
		}
	}
	last := -1
	for _, h := range []string{"# 1. 事実", "# 2. ワークスペース状態", "# 3. ユーザ確認事項"} {
		i := strings.Index(text, h)
		if i < last {
			t.Errorf("見出しの順が違う: %s", h)
		}
		last = i
	}
	scn.TestRepo = false
	if strings.Contains(ConfirmationText(scn, base.TempDir(t), "MODE OK", nil), "確認事項 2") {
		t.Error("確認事項 2 は test_repo のシナリオだけ")
	}
}

func TestResultsTableCutsCellsOnlyWithALimit(t *testing.T) {
	t.Parallel()
	results := []core.Result{{Kind: "k", Expect: strings.Repeat("c", 300), Verdict: core.Fail, Why: strings.Repeat("w", 300)}}
	cut := strings.Join(ResultsTable(results, 100), "\n")
	if !strings.Contains(cut, strings.Repeat("c", 100)+" |") || strings.Contains(cut, strings.Repeat("c", 101)) || strings.Contains(cut, strings.Repeat("w", 101)) {
		t.Errorf("limit で切る: %s", cut)
	}
	full := strings.Join(ResultsTable(results, 0), "\n")
	if !strings.Contains(full, strings.Repeat("c", 300)) || !strings.Contains(full, strings.Repeat("w", 300)) {
		t.Error("limit 無しは切らない")
	}
}

func TestEvidenceKeepsClipsOrOnlyCountsHookResponses(t *testing.T) {
	t.Parallel()
	reason := strings.Repeat("拒否の理由", 300)
	raw := func(event, name string, extra obj) string {
		ev := obj{"type": "system", "subtype": "hook_response", "hook_event": event, "hook_name": name}
		for k, v := range extra {
			ev[k] = v
		}
		return js(ev)
	}
	out := makeOut(t, strings.Join([]string{
		denyLine(reason),
		raw("SessionStart", "start", obj{"stdout": js(obj{"hookSpecificOutput": obj{"additionalContext": strings.Repeat("長い前置き", 50)}})}),
		raw("PostToolUse", "empty", nil),
		raw("PreToolUse", "long", obj{"stdout": js(obj{"hookSpecificOutput": obj{"additionalContext": strings.Repeat("あ", 5000)}})}),
	}, "\n")+"\n")
	scn := &core.Scenario{ID: "UC-99", Title: "t", Expect: core.Expect{Judge: []string{"基準A"}}}
	ev := files(t, scn, out, nil, core.Result{Kind: "files_unchanged", Expect: "README.md", Verdict: core.Pass, Why: "変わっていない"})["evidence.md"]
	hook := strings.SplitN(strings.SplitN(ev, "### hook の応答", 2)[1], "\n###", 2)[0]
	if !strings.Contains(hook, reason) || strings.Contains(hook, "長い前置き") {
		t.Error("拒否は切らず、SessionStart は件数だけ")
	}
	for _, want := range []string{"SessionStart 1 件", "PostToolUse 1 件", "全 5000 文字のうち先頭 2000 文字。全量は step-1.jsonl", "judge の基準:\n- 基準A"} {
		if !strings.Contains(ev, want) {
			t.Errorf("evidence に %q が無い", want)
		}
	}
	if strings.Index(ev, "## 機械判定の結果") > strings.Index(ev, "judge の基準") {
		t.Error("節の順")
	}
}

func TestEvidenceToolResultsAndTheMachineResultTable(t *testing.T) {
	t.Parallel()
	result := func(content any, isErr bool) string {
		return js(obj{"type": "user", "message": obj{"content": []any{obj{"type": "tool_result", "is_error": isErr, "content": content}}}})
	}
	out := makeOut(t, strings.Join([]string{
		result("ok本文", false), result("別のエラー", true),
		result([]any{obj{"type": "image"}, obj{"type": "text", "text": "本文"}}, false),
		denyLine("DENY_PATH 本文"), result("DENY_PATH 本文", true),
	}, "\n")+"\n")
	why := strings.Repeat("理由", 100)
	ev := files(t, &core.Scenario{ID: "UC-99", Title: "t"}, out, nil,
		core.Result{Kind: "state", Expect: "ls", Verdict: core.Fail, Why: why},
		core.Result{Kind: "state", Expect: "cell", Verdict: core.Fail, Why: "出力に「a」が無い: l1\nl2 | l3"})["evidence.md"]
	for _, want := range []string{"### ツール結果（成功）\n- ok本文", "- 別のエラー", "[image ブロック]", why, "l1 l2 \\| l3", "hook の応答を参照"} {
		if !strings.Contains(ev, want) {
			t.Errorf("evidence に %q が無い", want)
		}
	}
	head, tail, _ := strings.Cut(ev, "### hook の応答")
	if strings.Count(head, "DENY_PATH 本文") != 0 || strings.Count(strings.SplitN(tail, "\n###", 2)[0], "DENY_PATH 本文") != 1 {
		t.Error("hook の拒否と同じ文のエラーは、hook の側だけに出す")
	}
}

func TestEvidenceDecisionsTableClipNoticeAndPrompt(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	for n, sid := range []string{"S1", "S2"} {
		line := js(obj{"type": "result", "session_id": sid, "result": "x"})
		if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("step-%d.jsonl", n+1)), []byte(line+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	rows := []judge.Row{
		{"event": "SessionStart", "session": "S2", "decision": "allow"},
		{"event": "PreToolUse", "tool": "Read", "session": "S1", "decision": "deny", "code": "UNDECLARED", "subject": "a|b"},
		{"event": "PreToolUse", "tool": "Bash", "decision": "deny", "subject": strings.Repeat("s", 800)},
	}
	ev := files(t, simple(), dir, rows)["evidence.md"]
	for _, want := range []string{"| step | event | tool | decision | code | rules | subject |", "| 2 | SessionStart |", "| 1 | PreToolUse | Read | deny | UNDECLARED |",
		"全 800 文字のうち先頭 500 文字。全量は logs/decisions.jsonl", "### 入力プロンプト\nワークツリーは切らない"} {
		if !strings.Contains(ev, want) {
			t.Errorf("evidence に %q が無い", want)
		}
	}
}

func TestReportShowsTheDenialTrendMarksOrSaysNone(t *testing.T) {
	t.Parallel()
	deny := denyLine("[ccnavi] DENY_PATH\n編集しません。")
	out := makeOut(t, strings.Join([]string{deny, deny, deny}, "\n")+"\n")
	var rows []judge.Row
	for i := 0; i < 3; i++ {
		rows = append(rows, judge.Row{"decision": "deny", "code": "DENY_PATH", "tool": "Edit", "subject": fmt.Sprintf("/w/%d", i), "session": "s1"})
	}
	md := files(t, simple(), out, rows)["report.md"]
	for _, want := range []string{"## 拒否の傾向（目印。判定ではない）", "- step 1: DENY_PATH が 3 回連続", "- step 1 [PreToolUse:Edit]: [ccnavi] DENY_PATH 編集しません。"} {
		if !strings.Contains(md, want) {
			t.Errorf("report に %q が無い", want)
		}
	}
	call := js(obj{"type": "assistant", "session_id": "s1", "message": obj{"content": []any{obj{"type": "tool_use", "id": "t1", "name": "Read", "input": obj{"file_path": "a"}}}}})
	none := files(t, simple(), makeOut(t, call+"\n"), nil)["report.md"]
	if strings.Count(none, "- なし") != 3 {
		t.Errorf("印が無ければ 3 つとも なし: %d", strings.Count(none, "- なし"))
	}
}

func TestMessagesMergeTextsThatDifferOnlyInPathAndNumber(t *testing.T) {
	t.Parallel()
	a := denyLine("[ccnavi] DENY_PATH\nsubject: /w/one/README.md\n編集しません。 3 回目")
	b := denyLine("[ccnavi] DENY_PATH\nsubject: /w/two/main.sh\n編集しません。 4 回目")
	out := makeOut(t, strings.Join([]string{a, b, denyLine("別の文面")}, "\n")+"\n")
	rows := []judge.Row{
		{"event": "PreToolUse", "decision": "deny", "code": "DENY_PATH", "rules": []any{"r:workspace-root"}, "enforced": true},
		{"event": "PreToolUse", "decision": "deny", "code": "DENY_PATH", "rules": []any{"r:workspace-root"}},
		{"event": "PreToolUse", "decision": "deny", "code": "DENY_X", "rules": []any{}},
	}
	msg := files(t, simple(), out, rows)["messages.md"]
	if strings.Count(msg, "編集しません。") != 1 || !strings.Contains(msg, "## 1. 拒否（2 回）") || !strings.Contains(msg, "出どころ（code / rules）: DENY_PATH / r:workspace-root") {
		t.Errorf("= %s", msg)
	}
}

func TestReportJSONKeepsTheCostRow(t *testing.T) {
	t.Parallel()
	out := base.TempDir(t)
	scn := simple()
	if err := WriteReport(scn, out, &core.State{CostUSD: 0.25}, nil, nil); err != nil {
		t.Fatal(err)
	}
	rs, err := judge.ReadReport(out)
	if err != nil || len(rs) != 1 || rs[0].Kind != "cost" || rs[0].CostUSD == nil || *rs[0].CostUSD != 0.25 {
		t.Errorf("= %+v %v", rs, err)
	}
}
