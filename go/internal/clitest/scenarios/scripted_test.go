package scenarios

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/judge"
	"ccnavi/internal/harness/scenario/runner"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/yaml"
)

// scriptedRig は差し替え口なしで、台本のスタブ（teststub claude）を起こす実行器。
func scriptedRig(t *testing.T) (*rig, string, string) {
	t.Helper()
	w := newWs(t, "", "")
	g := newRig(t, w)
	g.r.Step = nil
	g.r.Environ = append(os.Environ(), env.FakeClaudeHome+"="+filepath.Join(w.tmp, "home"))
	root, out := filepath.Join(w.tmp, "root"), filepath.Join(w.tmp, "scripted-out")
	for _, d := range []string{filepath.Join(root, ".claude"), out} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return g, root, out
}

func scriptedScenario() *core.Scenario {
	return &core.Scenario{Agent: core.AgentScript, Model: "haiku", BudgetUSD: 1.0, PromptSteps: 1, PermissionMode: "auto"}
}

func TestAScriptedStepRunsTheFakeClaudeAndTheHookDenies(t *testing.T) {
	t.Parallel()
	g, root, out := scriptedRig(t)
	write(t, filepath.Join(root, "a.txt"), "hello\n")
	deny, _ := json.Marshal(map[string]any{"hookSpecificOutput": map[string]any{"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "止めた"}})
	settings, _ := json.Marshal(map[string]any{"hooks": map[string]any{"PreToolUse": []any{map[string]any{"hooks": []any{map[string]any{"type": "command", "command": "printf '%s' '" + string(deny) + "'"}}}}}})
	write(t, filepath.Join(root, ".claude", "settings.json"), string(settings))
	step := core.Step{Prompt: "直して", Script: []any{map[string]any{"tool": "Edit", "input": map[string]any{"file_path": "a.txt", "old_string": "hello", "new_string": "hi"}}}}
	ev, err := g.r.RunStep(runner.StepInput{Scn: scriptedScenario(), Step: step, Out: out, Idx: 1, SessionID: "s-1", Root: root})
	if err != nil {
		t.Fatal(err)
	}
	if len(ev.ToolResults) == 0 || !ev.ToolResults[0].IsError || !strings.Contains(core.TextOf(ev.ToolResults[0].Content), "止めた") {
		t.Errorf("hook の拒否がツール結果に出ていない: %+v", ev.ToolResults)
	}
	if read(t, filepath.Join(root, "a.txt")) != "hello\n" {
		t.Error("拒否されたのにファイルが変わった")
	}
}

func TestAScriptedStepGivesTheHookTheSettingsEnv(t *testing.T) {
	t.Parallel()
	g, root, out := scriptedRig(t)
	settings, _ := json.Marshal(map[string]any{
		"env":   map[string]any{"PROBE": "from-settings"},
		"hooks": map[string]any{"PreToolUse": []any{map[string]any{"hooks": []any{map[string]any{"type": "command", "command": `echo "$PROBE" > "$CLAUDE_PROJECT_DIR/got"`}}}}}})
	write(t, filepath.Join(root, ".claude", "settings.json"), string(settings))
	step := core.Step{Prompt: "x", Script: []any{map[string]any{"tool": "Bash", "input": map[string]any{"command": "true"}}}}
	if _, err := g.r.RunStep(runner.StepInput{Scn: scriptedScenario(), Step: step, Out: out, Idx: 1, SessionID: "s-1", Root: root}); err != nil {
		t.Fatal(err)
	}
	if got := read(t, filepath.Join(root, "got")); got != "from-settings\n" {
		t.Errorf("settings.json の env が hook に入らない（ccnavi の hook は CCNAVI_ENTRY_PATH をここから受け取る）: %q", got)
	}
}

func TestEveryScriptedScenarioPassesInAThrowawayWorkspace(t *testing.T) {
	t.Parallel()
	g, _, _ := scriptedRig(t)
	g.r.LogsDir = base.TempDir(t)
	paths, _ := filepath.Glob(filepath.Join(base.Root(), "tests", "scenarios", "*.yml"))
	results, err := g.r.RunStub(paths)
	if err != nil {
		t.Fatal(err)
	}
	ran := 0
	for _, r := range results {
		if r.RC == nil {
			continue
		}
		ran++
		if *r.RC != 0 {
			t.Errorf("%s: 終了コード %d（%s）", r.ID, *r.RC, r.Note)
		}
	}
	if ran == 0 {
		t.Error("台本のあるシナリオが 1 本も無い")
	}
}

func TestRealDenialsAndShippedRulesHaveANextStep(t *testing.T) {
	t.Parallel()
	said := func(command string) string {
		r := run.Ccnavi(t, []string{"--root", base.Root(), "--test", "Bash", command, "--json"}, run.Opts{Dir: base.Root()})
		var out struct{ Response string }
		if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
			t.Fatalf("%q: %v\n%s", command, err, r.Stdout)
		}
		return out.Response
	}
	data, err := os.ReadFile(filepath.Join(base.Root(), ".ccnavi", "config", "common", "rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	doc, _ := yaml.Load(string(data))
	var heredoc string
	denies, _ := doc.(*yaml.Map).List("deny")
	for _, d := range denies {
		if m := d.(*yaml.Map); m.Text("id") == "heredoc" {
			heredoc = m.Text("message")
		}
	}
	texts := []string{said("git status"), said("rm -rf /tmp/x"), heredoc}
	for _, text := range texts {
		if text == "" || !judge.HasGuide(text) {
			t.Errorf("次の手を示していない文: %q", text)
		}
	}
	if !strings.Contains(texts[0], "builtin:guard-raw-git") || !strings.Contains(texts[1], "builtin:recursive-delete") {
		t.Error("実行ファイルの文面が変わった（判定の根拠の名前）")
	}
}
