package core

import (
	"encoding/json"
	"fmt"
	"strings"
)

// ToolCall は stream の tool_use 1 件。
type ToolCall struct {
	Name   string
	Input  any
	ID     string
	Parent string
}

// ToolResult は stream の tool_result 1 件。
type ToolResult struct {
	IsError   bool
	Content   any
	ToolUseID string
	Parent    string
}

// Stream は stream-json から抜き出した証拠。HookEvents は hook の応答の行（読んだままの値）。
type Stream struct {
	SessionIDs  []string
	ToolCalls   []ToolCall
	ToolResults []ToolResult
	HookEvents  []map[string]any
	FinalText   string
	CostUSD     float64
}

func blocks(msg any) []map[string]any {
	m, _ := msg.(map[string]any)
	list, _ := m["content"].([]any)
	var out []map[string]any
	for _, b := range list {
		if bm, ok := b.(map[string]any); ok {
			out = append(out, bm)
		}
	}
	return out
}

func str(v any) string {
	s, _ := v.(string)
	return s
}

// ParseStream は stream-json の行から、証拠に使うものを抜き出す。形が違う行は読み飛ばす。
func ParseStream(text string) *Stream {
	out := &Stream{}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var ev map[string]any
		if json.Unmarshal([]byte(line), &ev) != nil {
			continue
		}
		if sid := str(ev["session_id"]); sid != "" {
			seen := false
			for _, s := range out.SessionIDs {
				seen = seen || s == sid
			}
			if !seen {
				out.SessionIDs = append(out.SessionIDs, sid)
			}
		}
		kind := str(ev["type"])
		switch {
		case kind == "assistant":
			for _, b := range blocks(ev["message"]) {
				if b["type"] == "tool_use" {
					out.ToolCalls = append(out.ToolCalls, ToolCall{Name: str(b["name"]), Input: b["input"], ID: str(b["id"]), Parent: str(ev["parent_tool_use_id"])})
				}
			}
		case kind == "user":
			for _, b := range blocks(ev["message"]) {
				if b["type"] == "tool_result" {
					isErr, _ := b["is_error"].(bool)
					out.ToolResults = append(out.ToolResults, ToolResult{IsError: isErr, Content: b["content"], ToolUseID: str(b["tool_use_id"]), Parent: str(ev["parent_tool_use_id"])})
				}
			}
		case kind == "result":
			out.FinalText = str(ev["result"])
			if c, ok := ev["total_cost_usd"].(float64); ok {
				out.CostUSD += c
			}
		case strings.Contains(fmt.Sprint(orEmpty(ev["subtype"])), "hook") || strings.Contains(kind, "hook"):
			out.HookEvents = append(out.HookEvents, ev)
		}
	}
	return out
}

func orEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}

// TextOf はツール結果の content（文字列かブロックの列）を 1 つの文字列にする。
func TextOf(content any) string {
	switch c := content.(type) {
	case nil:
		return ""
	case []any:
		parts := make([]string, 0, len(c))
		for _, b := range c {
			if m, ok := b.(map[string]any); ok {
				if t := str(m["text"]); t != "" {
					parts = append(parts, t)
				} else {
					kind := str(m["type"])
					if kind == "" {
						kind = "?"
					}
					parts = append(parts, "["+kind+" ブロック]")
				}
			} else {
				parts = append(parts, fmt.Sprint(b))
			}
		}
		return strings.Join(parts, "\n")
	}
	return fmt.Sprint(content)
}
