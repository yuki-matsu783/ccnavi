package fakeclaude

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// 止める応答は、本物の tool_result と差し戻しの文に合わせた文になる。止めない応答は nil。
func TestBlockingFollowsTheRealWording(t *testing.T) {
	t.Parallel()
	deny := `{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"R"}}`
	ask := `{"hookSpecificOutput":{"permissionDecision":"ask","permissionDecisionReason":"R"}}`
	cases := []struct {
		name          string
		event, tool   string
		code          int
		stdout, stder string
		want          string // 空は止めない
	}{
		{"終了コード 2（ツール）", "PreToolUse", "Bash", 2, "", " no \n", "PreToolUse:Bash hook error: [command hook]: no"},
		{"終了コード 2（Stop）", "Stop", "", 2, "", "again", "Stop hook feedback:\n[command hook]: again"},
		{"deny", "PreToolUse", "Write", 0, deny, "", "PreToolUse:Write hook error: R"},
		{"ask は -p では止まる", "PreToolUse", "Write", 0, ask, "", "R"},
		{"Stop の block", "Stop", "", 0, `{"decision":"block","reason":"X"}`, "", "Stop hook feedback:\nX"},
		{"PostToolUse の block では止めない", "PostToolUse", "Bash", 0, `{"decision":"block","reason":"X"}`, "", ""},
		{"終了コード 1 は止めない", "PreToolUse", "Bash", 1, deny, "", ""},
		{"JSON でない出力は止めない", "PreToolUse", "Bash", 0, "text", "", ""},
		{"空の出力は止めない", "Stop", "", 0, "", "", ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			got := blocking(c.event, c.tool, c.code, c.stdout, c.stder)
			switch {
			case c.want == "" && got != nil:
				t.Errorf("止めてはいけない: %q", got.text)
			case c.want != "" && (got == nil || got.text != c.want):
				t.Errorf("got %v, want %q", got, c.want)
			}
		})
	}
}

// 台本の手は tool・say・agent のどれか 1 つで、tool は 4 つのうちのどれか。
func TestParsePlanRejectsBadMoves(t *testing.T) {
	t.Parallel()
	cases := []struct{ name, plan, want string }{
		{"空の手", `{"script":[{}]}`, "script の 1 手目: tool・say・agent のどれか 1 つ"},
		{"2 つ書いた手", `{"script":[{"say":"a","tool":"Read"}]}`, "script の 1 手目"},
		{"知らないツール", `{"script":[{"tool":"Grep"}]}`, "tool は Read / Write / Edit / Bash のどれか"},
		{"サブエージェントの中", `{"script":[{"agent":{"script":[{"say":"a"},{}]}}]}`, "script の 1 手目の agent の 2 手目"},
		{"差し戻しのあと", `{"after_stop_block":[{"tool":"Nope"}]}`, "after_stop_block の 1 手目"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			_, err := ParsePlan(c.plan)
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Errorf("err = %v, want %q", err, c.want)
			}
		})
	}
}

// matcher は tool 名に完全一致の正規表現。空と * は全部。ツールの無いイベントでは matcher を持つ hook を起こさない。
func TestMatchingHooks(t *testing.T) {
	t.Parallel()
	groups := []hookGroup{
		{Matcher: "", Hooks: []hook{{Command: "all"}}},
		{Matcher: "*", Hooks: []hook{{Type: "command", Command: "star"}, {Type: "prompt", Command: "x"}, {Command: "async", AsyncRewake: true}}},
		{Matcher: "Write|Edit", Hooks: []hook{{Command: "we"}}},
	}
	names := func(hs []hook) string {
		var out []string
		for _, h := range hs {
			out = append(out, h.Command)
		}
		return strings.Join(out, ",")
	}
	for _, c := range []struct {
		tool    string
		hasTool bool
		want    string
	}{
		{"Write", true, "all,star,we"},
		{"Edit", true, "all,star,we"},
		{"Bash", true, "all,star"},
		{"WriteX", true, "all,star"},
		{"", false, "all,star"},
	} {
		if got := names(matching(groups, c.tool, c.hasTool)); got != c.want {
			t.Errorf("%q: got %q, want %q", c.tool, got, c.want)
		}
	}
}

// run は台本をスタブで回し、stream-json の行と終了コードを返す。
func play(t *testing.T, settings string, plan Plan, extra ...string) (code int, lines []object, root string) {
	t.Helper()
	root = base.TempDir(t)
	if settings != "" {
		if err := os.MkdirAll(filepath.Join(root, ".claude"), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, ".claude", "settings.json"), []byte(settings), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	data, _ := json.Marshal(plan)
	script := filepath.Join(root, "script.json")
	if err := os.WriteFile(script, data, 0o644); err != nil {
		t.Fatal(err)
	}
	argv := append([]string{"-p", "hi", "--output-format", "stream-json"}, extra...)
	env := append(os.Environ(), ScriptEnv+"="+script, HomeEnv+"="+filepath.Join(root, "home"))
	var out, errw bytes.Buffer
	code = Run(argv, env, root, &out, &errw)
	if code != 0 {
		t.Logf("stderr: %s", errw.String())
	}
	return code, readJSONL(out.String()), root
}

// ツールが失敗した理由は本物の tool_result の文に合わせる。台本は止められても次の手へ進み、最後の文が result に入る。
func TestToolFailuresAreReportedAndTheScriptContinues(t *testing.T) {
	t.Parallel()
	plan := Plan{Script: []Move{
		tool("Read", object{"file_path": "missing.txt"}),
		tool("Write", object{"file_path": "a.txt", "content": "x\nx\n"}),
		tool("Edit", object{"file_path": "a.txt", "old_string": "x", "new_string": "y"}),
		tool("Edit", object{"file_path": "a.txt", "old_string": "q", "new_string": "y"}),
		tool("Bash", object{"command": "echo out; echo err >&2; exit 3"}),
		say("done"),
	}}
	code, lines, root := play(t, "", plan)
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	var results []string
	for _, l := range lines {
		if l["type"] != "user" {
			continue
		}
		block := asObject(asList(asObject(l["message"])["content"])[0])
		results = append(results, asString(block["content"]))
	}
	want := []string{
		"File does not exist. No such file or directory",
		"File created successfully at: " + filepath.Join(root, "a.txt"),
		"Found 2 matches of the string to replace, but replace_all is false.",
		"String to replace not found in file.\nString: q",
	}
	for i, w := range want {
		if i >= len(results) || results[i] != w {
			t.Errorf("結果 %d = %q, want %q", i, results[i], w)
		}
	}
	if last := results[len(results)-1]; !strings.HasPrefix(last, "Exit code 3\n") || !strings.Contains(last, "out") || !strings.Contains(last, "err") {
		t.Errorf("Bash の失敗 = %q", last)
	}
	end := lines[len(lines)-1]
	if end["type"] != "result" || end["result"] != "done" || end["stop_reason"] != "end_turn" {
		t.Errorf("result の行 = %v", end)
	}
}

// Stop の hook が 2 回続けて止めると、差し戻しの手を打ったあとも stop_reason は stop_hook_blocked_twice。
func TestStopBlockedTwice(t *testing.T) {
	t.Parallel()
	settings := `{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"echo '{\"decision\":\"block\",\"reason\":\"again\"}'"}]}]}}`
	plan := Plan{Script: []Move{say("one")}, AfterStopBlock: []Move{tool("Write", object{"file_path": "after.txt", "content": "k"})}}
	code, lines, root := play(t, settings, plan, "--include-hook-events")
	if code != 0 {
		t.Fatalf("code = %d", code)
	}
	end := lines[len(lines)-1]
	if end["stop_reason"] != "stop_hook_blocked_twice" {
		t.Errorf("stop_reason = %v", end["stop_reason"])
	}
	if _, err := os.Stat(filepath.Join(root, "after.txt")); err != nil {
		t.Errorf("差し戻しのあとの手が打たれていない: %v", err)
	}
	var synthetic bool
	for _, l := range lines {
		synthetic = synthetic || l["isSynthetic"] == true
	}
	if !synthetic {
		t.Error("差し戻しの文の行が無い")
	}
}

// 知らない引数・欠けた引数は終了コード 2。台本の環境変数が無いのは 1。
func TestRunRefusesBadInvocations(t *testing.T) {
	t.Parallel()
	for _, argv := range [][]string{
		{"-p", "x", "--output-format", "stream-json", "--nope"},
		{"--output-format", "stream-json"},
		{"-p", "x", "--output-format", "json"},
		{"-p", "x", "--output-format", "stream-json", "--session-id", "a", "--resume", "b"},
	} {
		var out, errw bytes.Buffer
		if code := Run(argv, nil, base.TempDir(t), &out, &errw); code != 2 {
			t.Errorf("%v: code = %d", argv, code)
		}
	}
	var out, errw bytes.Buffer
	if code := Run([]string{"-p", "x", "--output-format", "stream-json"}, nil, base.TempDir(t), &out, &errw); code != 1 || !strings.Contains(errw.String(), ScriptEnv) {
		t.Errorf("台本なし: code = %d, stderr = %q", code, errw.String())
	}
}
