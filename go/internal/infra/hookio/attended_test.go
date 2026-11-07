package hookio

import (
	"testing"

	"ccnavi/internal/infra/env"
)

// hook の判定（Unattended）は "0" だけを見る。引き継ぎの待機（Headless）は SDK の入口も含める。
func TestAttendance(t *testing.T) {
	cases := []struct {
		env                  map[string]string
		unattended, headless bool
	}{
		{map[string]string{env.ClaudeSessionAttended: "0"}, true, true},
		{map[string]string{env.ClaudeSessionAttended: "1"}, false, false},
		{map[string]string{env.ClaudeSessionAttended: "0 "}, false, false},
		{map[string]string{env.ClaudeSessionAttended: "true"}, false, false},
		{map[string]string{env.ClaudeCodeEntrypoint: "sdk-cli"}, false, true},
		{map[string]string{env.ClaudeCodeEntrypoint: "cli"}, false, false},
		{map[string]string{}, false, false},
	}
	for _, c := range cases {
		getenv := func(k string) string { return c.env[k] }
		if got := unattended(getenv); got != c.unattended {
			t.Errorf("unattended(%v) = %v", c.env, got)
		}
		if got := Headless(getenv); got != c.headless {
			t.Errorf("Headless(%v) = %v", c.env, got)
		}
	}
}
