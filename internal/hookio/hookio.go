// Package hookio decodes the payload an agent hook writes to stdin and encodes
// the response it expects on stdout.
package hookio

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
)

// Event names the hook that invoked us. The payload carries it, so a
// registration mistake cannot silently run the wrong checks.
type Event string

const (
	PreToolUse   Event = "PreToolUse"
	PostToolUse  Event = "PostToolUse"
	SessionStart Event = "SessionStart"
)

// Decision is the verdict on a single tool call, from most to least permissive.
type Decision string

const (
	Allow Decision = "allow"
	Ask   Decision = "ask"
	Deny  Decision = "deny"
)

// Input is the subset of the hook payload the checks read. Unknown fields are
// ignored so that a new field upstream does not break a decision.
type Input struct {
	SessionID      string          `json:"session_id"`
	CWD            string          `json:"cwd"`
	PermissionMode string          `json:"permission_mode"`
	Event          Event           `json:"hook_event_name"`
	ToolName       string          `json:"tool_name"`
	ToolInput      json.RawMessage `json:"tool_input"`
	ToolUseID      string          `json:"tool_use_id"`
}

// ErrNoPayload reports that stdin held nothing. A person ran the binary by
// hand; say so rather than exiting 0 as if the guard had run.
var ErrNoPayload = errors.New("no hook payload on stdin")

// Decode reads one hook payload.
func Decode(r io.Reader) (*Input, error) {
	raw, err := io.ReadAll(r)
	if err != nil {
		return nil, fmt.Errorf("read stdin: %w", err)
	}
	if len(trimSpace(raw)) == 0 {
		return nil, ErrNoPayload
	}

	var in Input
	if err := json.Unmarshal(raw, &in); err != nil {
		return nil, fmt.Errorf("parse hook payload: %w", err)
	}
	if in.Event == "" {
		return nil, errors.New("hook payload has no hook_event_name")
	}
	return &in, nil
}

// Field pulls a string out of tool_input, e.g. "command" or "file_path".
func (in *Input) Field(name string) string {
	if len(in.ToolInput) == 0 {
		return ""
	}
	var fields map[string]any
	if err := json.Unmarshal(in.ToolInput, &fields); err != nil {
		return ""
	}
	s, _ := fields[name].(string)
	return s
}

// preToolUseOutput is the shape Claude Code reads for a tool-call verdict.
type preToolUseOutput struct {
	HookEventName            Event    `json:"hookEventName"`
	PermissionDecision       Decision `json:"permissionDecision"`
	PermissionDecisionReason string   `json:"permissionDecisionReason"`
}

// contextOutput is the shape that reaches the model as extra context. It is the
// only channel PostToolUse and SessionStart have; plain stdout is discarded.
type contextOutput struct {
	HookEventName     Event  `json:"hookEventName"`
	AdditionalContext string `json:"additionalContext"`
}

type envelope struct {
	HookSpecificOutput any `json:"hookSpecificOutput"`
}

// WriteVerdict emits a PreToolUse decision. The reason carries why the call was
// stopped and what to do instead, so an allow needs no reason at all.
func WriteVerdict(w io.Writer, d Decision, reason string) error {
	return write(w, envelope{preToolUseOutput{
		HookEventName:            PreToolUse,
		PermissionDecision:       d,
		PermissionDecisionReason: reason,
	}})
}

// WriteContext emits text for the model on an event that cannot block.
func WriteContext(w io.Writer, ev Event, text string) error {
	return write(w, envelope{contextOutput{
		HookEventName:     ev,
		AdditionalContext: text,
	}})
}

func write(w io.Writer, v any) error {
	// Claude Code reads stdout as JSON only when it starts with "{" and ends
	// with "}", so nothing else may be printed alongside it.
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	return enc.Encode(v)
}

func trimSpace(b []byte) []byte {
	start := 0
	for start < len(b) && isSpace(b[start]) {
		start++
	}
	end := len(b)
	for end > start && isSpace(b[end-1]) {
		end--
	}
	return b[start:end]
}

func isSpace(c byte) bool {
	return c == ' ' || c == '\t' || c == '\n' || c == '\r'
}
