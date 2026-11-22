package judge

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/infra/yaml"
)

func js(v any) string {
	data, _ := json.Marshal(v)
	return string(data)
}

func hookLine(event, name string, payload any) string {
	return js(obj{"type": "system", "subtype": "hook_response", "hook_event": event, "hook_name": name, "output": js(payload), "session_id": "s1"})
}

func denyLine(reason string) string {
	return hookLine("PreToolUse", "PreToolUse:Edit", obj{"hookSpecificOutput": obj{"permissionDecision": "deny", "permissionDecisionReason": reason}})
}

func callLine(name string, input any, id, parent string) string {
	block := obj{"type": "tool_use", "id": id, "name": name, "input": input}
	return js(obj{"type": "assistant", "session_id": "s1", "parent_tool_use_id": nilIfEmpty(parent), "message": obj{"content": []any{block}}})
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// makeOut は step-N.jsonl を並べた出力ディレクトリを作る。
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

func hookEvents(lines ...string) []map[string]any {
	return core.ParseStream(strings.Join(lines, "\n")).HookEvents
}

func TestSummarizeHooksKeepsClipsOrOnlyCounts(t *testing.T) {
	t.Parallel()
	raw := func(event, name string, extra obj) string {
		ev := obj{"type": "system", "subtype": "hook_response", "hook_event": event, "hook_name": name}
		for k, v := range extra {
			ev[k] = v
		}
		return js(ev)
	}
	reason := strings.Repeat("拒否の理由", 300)
	stderr := strings.Repeat("削除は禁止です", 300)
	lines, omitted := SummarizeHooks(hookEvents(
		raw("PreToolUse", "guard", obj{"stdout": js(obj{"hookSpecificOutput": obj{"permissionDecision": "deny", "permissionDecisionReason": reason}})}),
		raw("SessionStart", "start", obj{"stdout": js(obj{"hookSpecificOutput": obj{"additionalContext": strings.Repeat("長い前置き", 50)}})}),
		raw("SessionStart", "start2", nil),
		raw("SessionStart", "s", obj{"stdout": js(obj{"systemMessage": "起動の通知"})}),
		raw("PostToolUse", "empty", nil),
		raw("PreToolUse", "guide", obj{"stdout": js(obj{"hookSpecificOutput": obj{"additionalContext": "git の入口は…"}})}),
		raw("Stop", "stop", obj{"stdout": "止める理由"}),
		raw("PreToolUse", "long", obj{"stdout": js(obj{"hookSpecificOutput": obj{"additionalContext": strings.Repeat("あ", 5000)}})}),
		raw("PreToolUse", "g", obj{"stderr": stderr, "exit_code": 2}),
		raw("PostToolUse", "p", obj{"stdout": js(obj{"systemMessage": "保護領域が変わった"})}),
		raw("PreToolUse", "q", obj{"stdout": js(obj{"continue": false, "stopReason": "止めた理由"})}),
	), "step-1.jsonl")
	text := strings.Join(lines, "\n")
	for _, kept := range []string{reason, "案内: git の入口は…", "止める理由", "終了コード 2: " + stderr, "通知: 保護領域が変わった", "停止: 止めた理由"} {
		if !strings.Contains(text, kept) {
			t.Errorf("残す文面が無い: %.30s", kept)
		}
	}
	for _, counted := range []string{"長い前置き", "起動の通知"} {
		if strings.Contains(text, counted) {
			t.Errorf("件数だけにする文面が出ている: %s", counted)
		}
	}
	if omitted["SessionStart"] != 3 || omitted["PostToolUse"] != 1 {
		t.Errorf("省いた件数 = %v", omitted)
	}
	if !strings.Contains(text, "全 5000 文字のうち先頭 2000 文字。全量は step-1.jsonl") {
		t.Error("長い案内は先頭を切って全量の在りかを書く")
	}
}

func TestAnErrorThatRepeatsTheHookDenialPointsToTheHook(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		reason, err string
		same        bool
	}{
		{"DENY_PATH 本文", "DENY_PATH 本文", true},
		{"ファイル /a/b.txt への書き込みは禁止です", "禁止", false},
		{"DENY本文", "DENY本文\n別の原因 exit 1", false},
		{"  ", "全然別のエラー", false},
	} {
		denials := HookDenialTexts(hookEvents(denyLine(tc.reason)))
		if got := DuplicatesHook(tc.err, denials); got != tc.same {
			t.Errorf("DuplicatesHook(%q, %q) = %v", tc.err, tc.reason, got)
		}
	}
}

func TestRepeatedDenialsAreCountedPerCodeAndStep(t *testing.T) {
	t.Parallel()
	sessions := map[int][]string{1: {"s1"}, 2: {"s2"}}
	deny := func(code, session string) Row { return rec("decision", "deny", "code", code, "session", session) }
	cases := []struct {
		name string
		rows []Row
		want string
	}{
		{"三連続", []Row{deny("DENY_PATH", "s1"), deny("DENY_PATH", "s1"), deny("DENY_PATH", "s1")}, "1:DENY_PATH:3"},
		{"二回では出さない", []Row{deny("DENY_PATH", "s1"), deny("DENY_PATH", "s1")}, ""},
		{"allow で戻る", []Row{deny("D", "s1"), deny("D", "s1"), rec("decision", "allow", "code", "D"), deny("D", "s1"), deny("D", "s1")}, ""},
		{"他の行は戻さず別の code は戻す", []Row{deny("A", "s1"), rec("decision", "skip", "code", ""), deny("A", "s1"), deny("A", "s1"), deny("B", "s1")}, "1:A:3"},
		{"step は別に数える", []Row{deny("A", "s1"), deny("A", "s1"), deny("A", "s2"), deny("A", "s2")}, ""},
	}
	for _, tc := range cases {
		var got []string
		for _, r := range RepeatedDenials(tc.rows, sessions) {
			got = append(got, fmt.Sprintf("%s:%s:%d", r.Step, r.Code, r.Count))
		}
		if strings.Join(got, ",") != tc.want {
			t.Errorf("%s = %v, want %s", tc.name, got, tc.want)
		}
	}
}

func TestBypassSuspects(t *testing.T) {
	t.Parallel()
	sessions := map[int][]string{1: {"s1"}}
	call := func(path, name string) core.ToolCall {
		return core.ToolCall{Name: name, Input: obj{"file_path": path}}
	}
	streams := func(calls ...core.ToolCall) map[int]*core.Stream { return map[int]*core.Stream{1: {ToolCalls: calls}} }
	deny := func(tool, subject string) Row { return rec("decision", "deny", "tool", tool, "subject", subject) }

	got := BypassSuspects([]Row{deny("Edit", "/work/Repo/README.md")}, streams(call("/work/Repo/README.md", "Edit"), call("/work/repo/readme.md", "Write")), sessions)
	if len(got) != 1 || got[0].Step != "1" || got[0].Denied != 1 || got[0].Calls != 2 {
		t.Errorf("大文字小文字の違うパスも同じと見る: %+v", got)
	}
	again := []Row{deny("Edit", "/work/a.md"), deny("Edit", "/work/a.md")}
	if got := BypassSuspects(again, streams(call("/work/a.md", "Edit"), call("/work/a.md", "Edit")), sessions); len(got) != 0 {
		t.Errorf("同じ拒否を受けた再試行は迂回ではない: %+v", got)
	}
	other := streams(call("/work/b.md", "Edit"))
	for _, row := range []Row{deny("Edit", "/work/a.md"), deny("Bash", "sed -i s/a/b/ /work/b.md"), deny("Edit", "/work/b.md…(+30)")} {
		if got := BypassSuspects([]Row{row}, other, sessions); len(got) != 0 {
			t.Errorf("別のパス・Bash・切れた subject は比べない: %+v", row)
		}
	}
}

func TestDenialsWithoutANextStepAreFlagged(t *testing.T) {
	t.Parallel()
	flagged := func(lines ...string) []string {
		var out []string
		for _, u := range UnguidedDenials(ReadStepStreams(makeOut(t, strings.Join(lines, "\n")+"\n"))) {
			out = append(out, u.Text)
		}
		return out
	}
	got := flagged(
		denyLine("[ccnavi] DENY_PATH\n編集しません。"),
		denyLine("[ccnavi] DENY_PATH\n理由\n  次: sh x.sh を打つ"),
		denyLine("理由\n案内: 補足"),
		denyLine("理由\n必須: 項目"),
		denyLine("本文の途中に 次: と書いても行頭ではない"),
		denyLine("[ccnavi] DENY_PATH"),
	)
	want := []string{"[ccnavi] DENY_PATH\n編集しません。", "本文の途中に 次: と書いても行頭ではない", "[ccnavi] DENY_PATH"}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Errorf("= %q", got)
	}
	empty := hookLine("PreToolUse", "PreToolUse:Bash", obj{"hookSpecificOutput": obj{"permissionDecision": "deny", "permissionDecisionReason": ""}})
	block := hookLine("Stop", "Stop", obj{"decision": "block"})
	if got := flagged(empty, block); len(got) != 2 || got[0] != EmptyReason || got[1] != EmptyReason {
		t.Errorf("理由文が空の拒否も拾う: %q", got)
	}
	ask := hookLine("PreToolUse", "PreToolUse:Bash", obj{"hookSpecificOutput": obj{"permissionDecision": "ask", "permissionDecisionReason": "x"}})
	if got := flagged(ask); len(got) != 0 {
		t.Errorf("確認（ask）は数えない: %q", got)
	}
}

func TestEveryShippedDenyMessageHasANextStep(t *testing.T) {
	t.Parallel()
	// 組み込みの deny の文は Go のテスト（policy/builtin の TestEveryDenyTextHasANextStep）が見る。ここは配る共通ルールの文。
	data, err := os.ReadFile(filepath.Join(base.Root(), ".ccnavi", "config", "common", "rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	doc, err := yaml.Load(string(data))
	if err != nil {
		t.Fatal(err)
	}
	denies, _ := doc.(*yaml.Map).List("deny")
	if len(denies) <= 3 {
		t.Fatalf("deny のルールが少ない: %d", len(denies))
	}
	for _, d := range denies {
		m := d.(*yaml.Map)
		if text := m.Text("message"); !HasGuide(text) {
			t.Errorf("%s の文に次の手が無い: %s", m.Text("id"), text)
		}
	}
}

func TestNormalizeMessageKeepsCommitIDsIntactAndFoldsPathsAndNumbers(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct{ in, want string }{
		{"commit 0123abc の 3 件を /home/u/a/b.sh と C:\\w\\x で見る", "commit <SHA> の <N> 件を <PATH> と <PATH> で見る"},
		{"12345678", "<SHA>"},
		{"（.ccnavi/docs/a.md「節」）を読む", "（<PATH>「節」）を読む"},
	} {
		if got := NormalizeMessage(tc.in); got != tc.want {
			t.Errorf("%q = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestCollectMessages(t *testing.T) {
	t.Parallel()
	a := denyLine("[ccnavi] DENY_PATH\nsubject: /w/one/README.md\n編集しません。 3 回目")
	b := denyLine("[ccnavi] DENY_PATH\nsubject: /w/two/main.sh\n編集しません。 4 回目")
	c := denyLine("別の文面")
	out := makeOut(t, strings.Join([]string{a, b, c}, "\n")+"\n")
	rows := []Row{
		rec("decision", "deny", "code", "DENY_PATH", "rules", []any{"r:workspace-root"}, "subject", "/w/one/README.md"),
		rec("decision", "deny", "code", "DENY_PATH", "rules", []any{"r:workspace-root"}, "subject", "/w/two/main.sh"),
		rec("decision", "deny", "code", "DENY_X", "rules", []any{}, "subject", "x"),
	}
	msgs := CollectMessages(ReadStepStreams(out), rows)
	if len(msgs) != 2 || msgs[0].Count != 2 || msgs[1].Count != 1 {
		t.Fatalf("パスだけが違う文は 1 件にまとめる: %+v", msgs)
	}
	if strings.Join(msgs[0].Src, "|") != "DENY_PATH / r:workspace-root" || strings.Join(msgs[1].Src, "|") != "DENY_X / -" {
		t.Errorf("出どころ = %v / %v", msgs[0].Src, msgs[1].Src)
	}
	if got := CollectMessages(ReadStepStreams(out), nil); len(got[0].Src) != 0 {
		t.Errorf("行の数が合わなければ出どころは不明: %v", got[0].Src)
	}
}

func TestSessionStartContextIsLeftOutOfMessages(t *testing.T) {
	t.Parallel()
	start := hookLine("SessionStart", "s", obj{"hookSpecificOutput": obj{"additionalContext": "決まり"}})
	guide := hookLine("PostToolUse", "p", obj{"hookSpecificOutput": obj{"additionalContext": "案内文"}})
	msgs := CollectMessages(ReadStepStreams(makeOut(t, start+"\n"+guide+"\n")), nil)
	if len(msgs) != 1 || msgs[0].Kind != "案内" || msgs[0].Text != "案内文" {
		t.Errorf("= %+v", msgs)
	}
}

func TestRealStreamFixtureWithItsDecisions(t *testing.T) {
	t.Parallel()
	dir := filepath.Join(base.Root(), "tests", "scenarios", "fixtures")
	stream, err := os.ReadFile(filepath.Join(dir, "subagent-edit-denied.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	decisions, err := os.ReadFile(filepath.Join(dir, "subagent-edit-denied.decisions.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var rows []Row
	for _, line := range strings.Split(string(decisions), "\n") {
		var r Row
		if strings.TrimSpace(line) != "" && json.Unmarshal([]byte(line), &r) == nil {
			rows = append(rows, r)
		}
	}
	msgs := CollectMessages(ReadStepStreams(makeOut(t, string(stream))), rows)
	var kinds []string
	var deny Message
	for _, m := range msgs {
		kinds = append(kinds, m.Kind+":"+strings.Join(m.Events, ","))
		if m.Kind == "拒否" {
			deny = m
		}
	}
	text := strings.Join(kinds, " ")
	if !strings.Contains(text, "拒否:PreToolUse") || !strings.Contains(text, "案内:SubagentStart") {
		t.Errorf("kinds = %v", kinds)
	}
	if strings.Join(deny.Src, "|") != "DENY_PATH / .ccnavi/config/common/rules.yml:workspace-root" {
		t.Errorf("出どころ = %v", deny.Src)
	}
}

func TestContextLinesCountCharactersClipTheHeadAndKeepTheSubagentApart(t *testing.T) {
	t.Parallel()
	// 文脈が無ければ節もファイルも作らない
	none := makeOut(t, callLine("Read", obj{"file_path": "a"}, "t1", "")+"\n")
	if got, _ := ContextLines(ReadStepStreams(none).Streams[1], 1, none); got != nil {
		t.Errorf("文脈が無ければ空: %v", got)
	}
	if _, err := os.Stat(filepath.Join(none, "context-1.md")); err == nil {
		t.Error("context-1.md を書いた")
	}
	// 長い案内は先頭だけを出し、全文はファイルへ
	ctx := strings.Repeat("あ", ContextLimit+500)
	long := makeOut(t, hookLine("SessionStart", "SessionStart:startup", obj{"hookSpecificOutput": obj{"additionalContext": ctx}})+"\n")
	lines, err := ContextLines(ReadStepStreams(long).Streams[1], 1, long)
	if err != nil {
		t.Fatal(err)
	}
	text := strings.Join(lines, "\n")
	for _, want := range []string{fmt.Sprintf("SessionStart [SessionStart:startup] 案内（%d 文字）", len([]rune(ctx))),
		strings.Repeat("あ", ContextLimit) + "…", fmt.Sprintf("全 %d 文字のうち先頭 %d 文字。全量は context-1.md", len([]rune(ctx)), ContextLimit)} {
		if !strings.Contains(text, want) {
			t.Errorf("節に「%.40s」が無い", want)
		}
	}
	if strings.Contains(text, strings.Repeat("あ", ContextLimit+1)) {
		t.Error("先頭を超えて出ている")
	}
	if full, _ := os.ReadFile(filepath.Join(long, "context-1.md")); !strings.Contains(string(full), ctx) {
		t.Error("全文はファイルに残す")
	}
	// 拒否の理由は証拠の上限
	reason := strings.Repeat("拒", EvidenceLimit+10)
	deny := makeOut(t, denyLine(reason)+"\n")
	lines, _ = ContextLines(ReadStepStreams(deny).Streams[1], 1, deny)
	if got := strings.Join(lines, "\n"); strings.Contains(got, strings.Repeat("拒", EvidenceLimit+1)) || !strings.Contains(got, fmt.Sprintf("拒否（%d 文字）", EvidenceLimit+10)) {
		t.Error("拒否の理由は EvidenceLimit で切る")
	}
	// hook_event_name の綴りも受ける
	line := hookLine("x", "n", obj{"hookSpecificOutput": obj{"additionalContext": "文脈"}})
	var ev obj
	_ = json.Unmarshal([]byte(line), &ev)
	delete(ev, "hook_event")
	ev["hook_event_name"] = "UserPromptSubmit"
	alt := makeOut(t, js(ev)+"\n")
	lines, _ = ContextLines(ReadStepStreams(alt).Streams[1], 1, alt)
	if !strings.Contains(strings.Join(lines, "\n"), "UserPromptSubmit [n] 案内（2 文字）") {
		t.Errorf("= %v", lines)
	}
	// サブエージェントは親と分ける
	stream, err := os.ReadFile(filepath.Join(base.Root(), "tests", "scenarios", "fixtures", "subagent-edit-denied.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	sub := makeOut(t, string(stream))
	lines, _ = ContextLines(ReadStepStreams(sub).Streams[1], 1, sub)
	main, subPart, ok := strings.Cut(strings.Join(lines, "\n"), "#### サブエージェント")
	if !ok {
		t.Fatal("サブエージェントの節が無い")
	}
	for _, want := range []string{"SessionStart [SessionStart:startup] 案内", "PreToolUse [PreToolUse:Edit] 拒否"} {
		if !strings.Contains(main, want) {
			t.Errorf("親の節に %s が無い", want)
		}
	}
	for _, want := range []string{"に親が渡した prompt", "この Agent 呼び出しの中のツール呼び出し: 1 件", "  - Read:", "SubagentStart [SubagentStart:Explore] 案内", "取れない: サブエージェントが受け取った SessionStart の文脈"} {
		if !strings.Contains(subPart, want) {
			t.Errorf("サブエージェントの節に %s が無い", want)
		}
	}
	if strings.Contains(main, "SubagentStart") || strings.Contains(subPart, "Edit:") {
		t.Error("親とサブエージェントが混ざっている")
	}
	if full, _ := os.ReadFile(filepath.Join(sub, "context-1.md")); !strings.Contains(string(full), "スキル候補") {
		t.Error("全文にサブエージェントの案内が無い")
	}
	// Agent の呼び出しが無ければサブエージェントの節は出さない
	if got := strings.Join(mustLines(t, deny), "\n"); strings.Contains(got, "サブエージェント") {
		t.Error("Agent 呼び出しが無いのにサブエージェントの節がある")
	}
}

func mustLines(t *testing.T, out string) []string {
	lines, err := ContextLines(ReadStepStreams(out).Streams[1], 1, out)
	if err != nil {
		t.Fatal(err)
	}
	return lines
}
