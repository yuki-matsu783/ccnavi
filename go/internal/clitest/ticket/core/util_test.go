package core

import (
	"encoding/json"
	"fmt"
	"testing"

	"ccnavi/internal/harness/run"
)

func jsonUnmarshal(text string, v any) error { return json.Unmarshal([]byte(text), v) }

// runLint は `--lint --mode enable` をルール付きで起こし、標準出力を返す（保護の切り替えは付けない）。
func runLint(t *testing.T, ws, rules string, env map[string]string) string {
	t.Helper()
	r := run.Ccnavi(t, []string{"--root", ws, "--rules", rules, "--lint", "--mode", "enable"}, run.Opts{Env: env})
	return r.Stdout
}

func sprintf(format string, args ...any) string { return fmt.Sprintf(format, args...) }
