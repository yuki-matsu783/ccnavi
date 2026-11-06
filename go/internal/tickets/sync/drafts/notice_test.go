package drafts

import "testing"

func TestShellQuote(t *testing.T) {
	for in, want := range map[string]string{
		"":             "''",
		"/tmp/ws-1.a":  "/tmp/ws-1.a",
		"/tmp/a b":     "'/tmp/a b'",
		"/tmp/it's":    `'/tmp/it'"'"'s'`,
		"/tmp/日本":      "'/tmp/日本'",
		`C:\work\tree`: `'C:\work\tree'`,
	} {
		if got := shellQuote(in); got != want {
			t.Errorf("shellQuote(%q) = %q, want %q", in, got, want)
		}
	}
}
