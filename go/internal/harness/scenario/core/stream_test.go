package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

func lines(events ...map[string]any) string {
	var sb strings.Builder
	for _, e := range events {
		data, _ := json.Marshal(e)
		sb.Write(data)
		sb.WriteString("\n")
	}
	return sb.String()
}

func TestParseStreamExtractsEvidenceAndSkipsBadLines(t *testing.T) {
	t.Parallel()
	text := lines(
		map[string]any{"type": "system", "subtype": "init", "session_id": "abc"},
		map[string]any{"type": "assistant", "session_id": "abc", "message": map[string]any{"content": []any{map[string]any{"type": "tool_use", "name": "Edit", "input": map[string]any{"x": 1}}}}},
		map[string]any{"type": "user", "message": map[string]any{"content": []any{map[string]any{"type": "tool_result", "is_error": true, "content": "no"}}}},
		map[string]any{"type": "system", "subtype": "hook_response", "hook_event": "Stop"},
		map[string]any{"type": "result", "session_id": "abc", "result": "終わり", "total_cost_usd": 0.01},
	) + "noise\n"
	got := ParseStream(text)
	if len(got.SessionIDs) != 1 || got.SessionIDs[0] != "abc" || got.ToolCalls[0].Name != "Edit" || !got.ToolResults[0].IsError ||
		got.FinalText != "終わり" || got.CostUSD != 0.01 || len(got.HookEvents) != 1 {
		t.Errorf("= %+v", got)
	}
}

func TestParseStreamKeepsIDsAndParentsAndLeavesMissingOnesEmpty(t *testing.T) {
	t.Parallel()
	data, err := os.ReadFile(filepath.Join(base.Root(), "tests", "scenarios", "fixtures", "subagent-edit-denied.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	got := ParseStream(string(data))
	byID := map[string]ToolCall{}
	var agent, read ToolCall
	var names []string
	for _, c := range got.ToolCalls {
		byID[c.ID] = c
		names = append(names, c.Name)
		switch c.Name {
		case "Agent":
			agent = c
		case "Read":
			read = c
		}
	}
	if agent.Parent != "" || read.Parent != agent.ID || strings.Join(names, ",") != "Agent,Read,Edit" {
		t.Errorf("親子 = %+v / %+v / %v", agent, read, names)
	}
	for _, r := range got.ToolResults {
		if c, ok := byID[r.ToolUseID]; !ok || r.Parent != c.Parent {
			t.Errorf("結果 %+v が呼び出しと対応しない", r)
		}
	}
	if len(got.SessionIDs) != 1 || got.SessionIDs[0] != "sess-main" || got.FinalText == "" {
		t.Errorf("= %+v", got)
	}
	bare := ParseStream(lines(
		map[string]any{"type": "assistant", "message": map[string]any{"content": []any{map[string]any{"type": "tool_use", "name": "Edit", "input": map[string]any{}}}}},
		map[string]any{"type": "user", "message": map[string]any{"content": []any{map[string]any{"type": "tool_result", "content": "x"}}}},
	))
	if c := bare.ToolCalls[0]; c.ID != "" || c.Parent != "" || bare.ToolResults[0].ToolUseID != "" || bare.ToolResults[0].Parent != "" {
		t.Errorf("id が無ければ空: %+v", bare)
	}
}

func TestTextOfFlattensBlocks(t *testing.T) {
	t.Parallel()
	got := TextOf([]any{map[string]any{"type": "image"}, map[string]any{"type": "text", "text": "本文"}})
	if got != "[image ブロック]\n本文" || TextOf(nil) != "" || TextOf("x") != "x" {
		t.Errorf("= %q", got)
	}
}
