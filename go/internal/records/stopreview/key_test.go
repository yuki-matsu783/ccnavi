package stopreview

import (
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
)

func TestSharedKey(t *testing.T) {
	dir := t.TempDir()
	path := lines(t, dir,
		`{"type": "assistant", "uuid": "a1"}`,
		`{"type": "assistant", "uuid": "a2"}`,
		`{"type": "assistant", "uuid": "side", "isSidechain": true}`,
		`{"type": "system", "uuid": "sys"}`,
		"not json",
		`{"type": "user", "uuid": "u"}`,
	)
	if got := SharedKey("s1", path); got != "s1:a2" {
		t.Errorf("SharedKey = %q", got)
	}
	// 末尾の行が書きかけなら、1 つ前の行で代用しない（hook と判定役でキーがずれる）。
	unfinished := lines(t, t.TempDir(), `{"type": "assistant", "uuid": "a1"}`, `{"type": "assist`)
	for _, c := range []struct{ session, path string }{
		{"s1", unfinished}, {"s1", "/nonexistent"}, {"", path}, {"s1", lines(t, t.TempDir(), `{"type": "user"}`)},
	} {
		if got := SharedKey(c.session, c.path); got != "" {
			t.Errorf("SharedKey(%q, %q) = %q", c.session, c.path, got)
		}
	}
}

func TestSharedKeyReadsFromTailAcrossChunks(t *testing.T) {
	rows := []string{`{"type": "assistant", "uuid": "old"}`}
	filler := `{"type": "user", "text": "` + strings.Repeat("x", 1000) + `"}`
	for range 200 {
		rows = append(rows, filler)
	}
	if got := SharedKey("s1", lines(t, t.TempDir(), rows...)); got != "s1:old" {
		t.Errorf("SharedKey = %q", got)
	}
}

func TestSetting(t *testing.T) {
	cases := []struct {
		mode     config.Mode
		declared string
		want     config.Mode
	}{
		{config.Enable, "enable", config.Enable},
		{config.Enable, "", config.DryRun},
		{config.Enable, "garbage", config.DryRun},
		{config.DryRun, "enable", config.DryRun},
		{config.Enable, "disable", config.Disable},
		{config.Disable, "enable", config.Disable},
	}
	for _, c := range cases {
		if got := Setting(c.mode, c.declared); got != c.want {
			t.Errorf("Setting(%s, %q) = %s, want %s", c.mode, c.declared, got, c.want)
		}
	}
}

func TestCandidate(t *testing.T) {
	base := CandidateInput{MainAgent: true, Effective: config.Enable, TicketsEnabled: true, Registered: true, ProposalActivity: true}
	cases := []struct {
		name   string
		change func(*CandidateInput)
		ok     bool
		why    Reason
	}{
		{"候補", func(*CandidateInput) {}, true, ReasonCandidate},
		{"着手済みのチケットが無い", func(c *CandidateInput) { c.ProposalActivity = false }, true, ReasonCandidate},
		{"サブエージェント", func(c *CandidateInput) { c.MainAgent = false }, false, ReasonNotMainAgent},
		{"hook の継続", func(c *CandidateInput) { c.StopHookActive = true }, false, ReasonStopHookActive},
		{"無効", func(c *CandidateInput) { c.Effective = config.Disable }, false, ReasonDisabled},
		{"チケット制御なし", func(c *CandidateInput) { c.TicketsEnabled = false }, false, ReasonDisabled},
		{"未登録", func(c *CandidateInput) { c.Registered = false }, false, ReasonDisabled},
		{"既存の促し", func(c *CandidateInput) { c.ExistingNudge = true }, false, ReasonExistingNudge},
		{"対象を決められない", func(c *CandidateInput) { c.TargetUnresolvable = true }, false, ReasonTargetUnresolvable},
		{"着手済み", func(c *CandidateInput) { c.ProposalActivity, c.ApprovedInProgress = false, true }, false, ReasonApprovedInProgress},
		{"AI による継続は 1 回", func(c *CandidateInput) { c.StopHookActive, c.AIContinuation = true, true }, true, ReasonCandidate},
		{"再判定は 1 回まで", func(c *CandidateInput) { c.StopHookActive, c.AIContinuation, c.AlreadyRejudged = true, true, true }, false, ReasonStopHookActive},
	}
	for _, c := range cases {
		in := base
		c.change(&in)
		if ok, why := Candidate(in); ok != c.ok || why != c.why {
			t.Errorf("%s: Candidate = %v, %s", c.name, ok, why)
		}
	}
}

func TestAttemptIDSeparatesSessionsAndRejudge(t *testing.T) {
	ids := []string{AttemptID("s1:same", false), AttemptID("s2:same", false), AttemptID("s1:same", true)}
	slices.Sort(ids)
	if len(slices.Compact(slices.Clone(ids))) != 3 {
		t.Errorf("試行 ID が重なった: %v", ids)
	}
}
