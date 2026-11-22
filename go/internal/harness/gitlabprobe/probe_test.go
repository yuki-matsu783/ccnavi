package gitlabprobe

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// approvedParent は probe が承認する親と同じ形（plan 1 つ・フィードバック計画なし）の全文。
func approvedParent() string {
	return TicketText("i0001", TicketOpts{Allow: []string{"src/*", "wip/*"}, Review: true, Title: "挨拶を足す", Issue: 3})
}

func TestFeedbackRevisionAddsOnlyAnEmptyFeedbackPlan(t *testing.T) {
	t.Parallel()
	text := approvedParent()
	revised, err := FeedbackRevision(text)
	if err != nil {
		t.Fatal(err)
	}
	if want := strings.Replace(text, "human_review:", "feedback: []\nhuman_review:", 1); revised != want {
		t.Errorf("改版の本文が違う:\n%s", revised)
	}
}

func TestFeedbackRevisionBlanksTheFieldsWrittenAfterApproval(t *testing.T) {
	t.Parallel()
	text := strings.NewReplacer(`started_at: ""`, `started_at: "2026-01-01T00:00:00"`, `base_sha: ""`, `base_sha: "abc123"`).Replace(approvedParent())
	revised, err := FeedbackRevision(text)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`started_at: ""`, `base_sha: ""`} {
		if !strings.Contains(revised, want) {
			t.Errorf("%s が空になっていない", want)
		}
	}
	for _, gone := range []string{"2026-01-01", "abc123"} {
		if strings.Contains(revised, gone) {
			t.Errorf("%s が残っている", gone)
		}
	}
}

func TestFeedbackRevisionRefusesATextThatAlreadyHasFeedback(t *testing.T) {
	t.Parallel()
	once, err := FeedbackRevision(approvedParent())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := FeedbackRevision(once); err == nil {
		t.Error("feedback が既にあるのに通った")
	}
}

func TestCancelledChildrenLiveInDoneNotInACancelledDirectory(t *testing.T) {
	t.Parallel()
	path := ApprovedCopyPath("/t", "done", "i0002-01-01")
	if want := filepath.Join("/t", ".ccnavi", "approved", "done", "i0002-01-01.md"); path != want {
		t.Errorf("path = %s", path)
	}
	if strings.Contains(path, "cancelled") {
		t.Error("cancelled/ という置き場は無い")
	}
}

func TestCancelledMarkNeedsAFilledCancelledAt(t *testing.T) {
	t.Parallel()
	for text, want := range map[string]bool{
		"x: 1\ncancelled_at: \"2026-01-01T00:00:00\"\n": true,
		"x: 1\ncancelled_at: \"\"\n":                    false,
		"x: 1\n":                                        false,
	} {
		if got := IsCancelledCopy(text); got != want {
			t.Errorf("%q: %v", text, got)
		}
	}
}

func TestProjectNameTakesAPrefixToTellRunsApart(t *testing.T) {
	t.Parallel()
	for prefix, want := range map[string]string{"ccnavi-probe": "ccnavi-probe-0101-120000", "p614": "p614-0101-120000"} {
		if got := ProjectName(prefix, "0101-120000"); got != want {
			t.Errorf("%s → %s", prefix, got)
		}
	}
}

func TestSummaryCountsSkipApartFromPass(t *testing.T) {
	t.Parallel()
	results := []Result{{"a", "PASS", ""}, {"b", "SKIP", ""}, {"c", "FAIL", ""}, {"d", "PASS", ""}}
	if got := SummaryLine(results); got != "PASS 2 / SKIP 1 / FAIL 1" {
		t.Errorf("まとめ = %s", got)
	}
}

func TestTicketTextShapesParentAndChild(t *testing.T) {
	t.Parallel()
	parent := TicketText("i0003", TicketOpts{Title: "親", MR: 3, Review: true})
	for _, want := range []string{"ticket: i0003\n", "mr: 3\n", "plan:", "human_review:\n  required: true"} {
		if !strings.Contains(parent, want) {
			t.Errorf("親に %q が無い:\n%s", want, parent)
		}
	}
	child := TicketText("i0003-01-01", TicketOpts{Parent: "i0003", Phase: 1, Allow: []string{"src/d/*"}, Title: "子"})
	if !strings.Contains(child, "parent: i0003\nphase: 1\n") || strings.Contains(child, "plan:") || strings.Contains(child, "mr:") {
		t.Errorf("子の形が違う:\n%s", child)
	}
	if !strings.Contains(child, `glob: "src/d/*"`) || !strings.Contains(child, "required: false") {
		t.Errorf("子の allow・review が違う:\n%s", child)
	}
}

func TestHelpNeedsNeitherTokenNorExe(t *testing.T) {
	t.Parallel()
	var out, errw bytes.Buffer
	if rc := Main([]string{"--help"}, base.Root(), []string{"PATH=" + "/nonexistent"}, &out, &errw); rc != 0 {
		t.Fatalf("rc = %d: %s", rc, errw.String())
	}
	if !strings.Contains(out.String(), "GITLAB_TOKEN") {
		t.Errorf("説明に GITLAB_TOKEN が無い: %q", out.String())
	}
}

func TestWithoutTokensTheProbeStopsWithAHintAndTouchesNothing(t *testing.T) {
	t.Parallel()
	var out, errw bytes.Buffer
	outDir := base.TempDir(t)
	rc := Main(nil, base.Root(), []string{"CCNAVI_PROBE_OUT=" + outDir}, &out, &errw)
	if rc != 2 || !strings.Contains(errw.String(), "make_gitlab_tokens.rb") {
		t.Errorf("rc = %d, stderr = %q", rc, errw.String())
	}
	if matches, _ := filepath.Glob(filepath.Join(outDir, "*")); len(matches) != 0 {
		t.Errorf("トークンが無いのに置き場を作った: %v", matches)
	}
}

func TestMissingExeIsReportedBeforeAnythingIsCreated(t *testing.T) {
	t.Parallel()
	var out, errw bytes.Buffer
	outDir := base.TempDir(t)
	environ := []string{"GITLAB_TOKEN=a", "CCNAVI_PROBE_REVIEWER_TOKEN=b", "CCNAVI_PROBE_OUT=" + outDir, "CCNAVI_ENTRY_PATH=dist/none/ccnavi"}
	if rc := Main(nil, base.TempDir(t), environ, &out, &errw); rc != 2 || !strings.Contains(errw.String(), "exe が無い") {
		t.Errorf("rc = %d, stderr = %q", rc, errw.String())
	}
	if matches, _ := filepath.Glob(filepath.Join(outDir, "*")); len(matches) != 0 {
		t.Errorf("exe が無いのに置き場を作った: %v", matches)
	}
}

func TestEntryPathIsTakenAsGivenWhenAbsolute(t *testing.T) {
	t.Parallel()
	abs := filepath.Join(base.TempDir(t), "ccnavi")
	got, err := ResolveExe("/unused", abs)
	if err != nil || got != abs {
		t.Errorf("%q, %v", got, err)
	}
}
