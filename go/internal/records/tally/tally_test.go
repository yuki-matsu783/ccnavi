package tally

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/records/audit"
)

func TestNormalize(t *testing.T) {
	if Normalize("git push origin main") != Normalize("  git\tpush \"origin\"\n 'main' ") {
		t.Error("引用符と空白の違いを均していない")
	}
	if Normalize("cat /A") == Normalize("cat /a") {
		t.Error("大文字小文字は分けたまま")
	}
}

func TestThresholds(t *testing.T) {
	for _, c := range []struct {
		written      string
		deny, streak int
	}{
		{"", 3, 3}, {"x", 3, 3}, {"2.5", 3, 3}, {"-1", 3, 3},
		{"0", 3, 0}, {"1", 3, 1}, {" 5 ", 5, 5}, {"1_0", 10, 10},
	} {
		if got := DenyThreshold(c.written); got != c.deny {
			t.Errorf("DenyThreshold(%q) = %d, want %d", c.written, got, c.deny)
		}
		if got := StreakThreshold(c.written); got != c.streak {
			t.Errorf("StreakThreshold(%q) = %d, want %d", c.written, got, c.streak)
		}
	}
}

func TestCountDeny(t *testing.T) {
	state := t.TempDir()
	r := &audit.Record{Subject: "git push", Rules: []string{"git-push"}, Session: "s", Tool: "Bash"}
	if got := CountDeny("", r); got != 0 {
		t.Errorf("置き場が無いのに数えた: %d", got)
	}
	// セッションを見分けられないものは数えない（別のセッションの拒否と混ぜない）。
	anon := &audit.Record{Subject: "git push", Rules: []string{"git-push"}}
	for range 3 {
		if CountDeny(state, anon) != 0 {
			t.Error("セッションが無いのに数えた")
		}
	}
	if names, _ := os.ReadDir(state); len(names) != 0 {
		t.Errorf("書いた: %v", names)
	}
	var counts []int
	for _, subject := range []string{"git push", "git  'push'", "git push"} {
		r.Subject = subject
		counts = append(counts, CountDeny(state, r))
	}
	if counts[0] != 1 || counts[1] != 2 || counts[2] != 3 {
		t.Errorf("counts = %v", counts)
	}
	// 記録には均した対象のハッシュだけを置く。
	data, _ := os.ReadFile(filepath.Join(state, "denied-s.json"))
	if strings.Contains(string(data), "git push") {
		t.Errorf("対象を平文で残した: %s", data)
	}
	report := DenyReport(state, "s", 3)
	if !strings.HasPrefix(report, denyReportHead) || !strings.HasSuffix(report, "\n  git-push（Bash）: 3 回") {
		t.Errorf("report = %q", report)
	}
	// 同じ回数のまま次のターンで繰り返さない。
	if again := DenyReport(state, "s", 3); again != "" {
		t.Errorf("again = %q", again)
	}
	if !strings.Contains(DenyNote("git-push", 3), "git-push") {
		t.Error("note にルールが無い")
	}
}

func TestRuleOf(t *testing.T) {
	for _, c := range []struct {
		r    audit.Record
		want string
	}{
		{audit.Record{Rules: []string{"a", "b"}, Code: "c"}, "a"},
		{audit.Record{Code: "c"}, "c"},
		{audit.Record{}, "(deny)"},
	} {
		if got := RuleOf(&c.r); got != c.want {
			t.Errorf("RuleOf = %q, want %q", got, c.want)
		}
	}
}

func TestFailAndReset(t *testing.T) {
	state := t.TempDir()
	fail := func(agent string) Notice {
		kind, _ := Fail(state, "s1", agent, 3)
		return kind
	}
	if got := []Notice{fail(""), fail(""), fail("")}; got[0] != First || got[1] != NoNotice || got[2] != streak {
		t.Errorf("notices = %v", got)
	}
	// 成功で 0 に戻し、最初の 1 回の印は残す。
	Reset(state, "s1", "", 3)
	data, _ := os.ReadFile(filepath.Join(state, "failstreak-s1-main.json"))
	if string(data) != `{"first": true}` {
		t.Errorf("after reset = %s", data)
	}
	if got := []Notice{fail(""), fail(""), fail("")}; got[0] != NoNotice || got[2] != streak {
		t.Errorf("notices = %v", got)
	}
	// サブエージェントは別に数える。
	if fail("sub1") != First {
		t.Error("サブエージェントの最初の 1 回")
	}
	if _, err := os.Stat(filepath.Join(state, "failstreak-s1-sub1.json")); err != nil {
		t.Error(err)
	}
	// 連続が無い成功は何も書かない。0 は数えない。セッションが空なら数えない。
	empty := t.TempDir()
	Reset(empty, "s2", "", 3)
	if kind, n := Fail(empty, "s2", "", 0); kind != NoNotice || n != 0 {
		t.Error("0 で数えた")
	}
	if kind, _ := Fail(empty, "", "", 3); kind != NoNotice {
		t.Error("セッションが空で数えた")
	}
	if names, _ := os.ReadDir(empty); len(names) != 0 {
		t.Errorf("書いた: %v", names)
	}
}

// 置き場に書けなければ案内を出さない（覚えられないまま出すと、失敗のたびに同じ案内が並ぶ）。
func TestUnwritableStateSaysNothing(t *testing.T) {
	state := filepath.Join(t.TempDir(), "state")
	os.WriteFile(state, []byte("x"), 0o600)
	for range 4 {
		if kind, _ := Fail(state, "s1", "", 3); kind != NoNotice {
			t.Errorf("kind = %q", kind)
		}
	}
	if n := CountDeny(state, &audit.Record{Subject: "x", Session: "s1"}); n != 0 {
		t.Errorf("n = %d", n)
	}
}
