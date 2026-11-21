package layers

import (
	"encoding/json"
	"testing"
)

// hookContext は hook の標準出力の additionalContext。
func hookContext(t testing.TB, stdout string) string {
	t.Helper()
	var out struct {
		HookSpecificOutput struct {
			Context string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(stdout), &out); err != nil {
		t.Fatalf("標準出力が期待した JSON ではない: %v\nstdout: %q", err, stdout)
	}
	return out.HookSpecificOutput.Context
}
