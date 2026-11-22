package samples

import (
	"bytes"
	"slices"
	"strings"
	"testing"
)

func TestPlaceMovesTheUserEntryRightAfterTheWrapperOptions(t *testing.T) {
	t.Parallel()
	prefix := []string{"--root", "R", "--guard-core-files", "disable", "--restore-if-deny", "disable"}
	cases := []struct {
		name string
		args []string
		want []string
	}{
		{"verb", []string{"user", "agree"}, []string{"--root", "R", "user", "agree", "--guard-core-files", "disable", "--restore-if-deny", "disable"}},
		{"verb with value", []string{"user", "reviewed", "3", "--accept-unresolved"}, []string{"--root", "R", "user", "reviewed", "3", "--guard-core-files", "disable", "--restore-if-deny", "disable", "--accept-unresolved"}},
		{"case", []string{"USER", "Agree"}, []string{"--root", "R", "USER", "Agree", "--guard-core-files", "disable", "--restore-if-deny", "disable"}},
		{"after the caller's wrapper options", []string{"--cwd", "C", "user", "prune"}, []string{"--root", "R", "--cwd", "C", "user", "prune", "--guard-core-files", "disable", "--restore-if-deny", "disable"}},
		{"not an entry", []string{"ticket", "status"}, append(slices.Clone(prefix), "ticket", "status")},
		{"unknown verb takes only user", []string{"user", "zzz", "x"}, []string{"--root", "R", "user", "--guard-core-files", "disable", "--restore-if-deny", "disable", "zzz", "x"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			if got := Place(prefix, c.args); !slices.Equal(got, c.want) {
				t.Errorf("got %v\nwant %v", got, c.want)
			}
		})
	}
}

func TestUserformMainRefusesTheSameEntryShapesAsTheExecutable(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		args []string
		code int
		msg  string
	}{
		{"no entry option", []string{"--root", "R", "user", "agree"}, 0, ""},
		{"sh", []string{"--entry", "sh", "user", "agree"}, 0, ""},
		{"board", []string{"--entry=board", "user", "agree"}, 0, ""},
		{"unknown value", []string{"--entry", "web", "user", "agree"}, 2, "--entry に渡せるのは"},
		{"twice", []string{"--entry", "sh", "--entry", "sh", "user"}, 2, "1 度しか"},
		{"without a value", []string{"--entry"}, 2, "値が要る"},
		{"without user", []string{"--entry", "sh", "ticket", "status"}, 2, "user の入口と一緒に"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			var stderr bytes.Buffer
			if code := UserformMain(c.args, &stderr); code != c.code || !strings.Contains(stderr.String(), c.msg) {
				t.Errorf("code = %d, stderr = %q", code, stderr.String())
			}
		})
	}
}

func TestLearningRecordsAreInTheShapeTheWikiReads(t *testing.T) {
	t.Parallel()
	if got := Pattern([]string{"a", "b"}, "rule"); got != "---\ntype: learning-wiki\ntarget: rule\nsources: [a, b]\ntitle: t\ndescription: d\ntags: [a]\nkeywords: [b]\n---\n\n本文\n" {
		t.Errorf("Pattern: %q", got)
	}
	got := Impact("w", "承認待ち", []Count{{"p2", 1}, {"p1", 2}}, DefaultDecided, DefaultTarget)
	want := "---\nproposal: w\ntarget: docs\nfiles: [a.md]\npatterns:\n  p2: 1\n  p1: 2\nstatus: 承認待ち\nreason: r\ndecided_at: '2026-01-01T00:00:00+00:00'\n---\n\n差分\n"
	if got != want {
		t.Errorf("Impact: %q", got)
	}
}

func TestSetFieldsRewritesInPlaceOrAddsBeforeTheClosingFence(t *testing.T) {
	t.Parallel()
	text := "---\nticket: a\nmerged_sha: \"\"\n---\n\n本文: x\n"
	got := SetFields(text, Field{"merged_sha", "abc"}, Field{"extra", "1"})
	if want := "---\nticket: a\nmerged_sha: \"abc\"\nextra: \"1\"\n---\n\n本文: x\n"; got != want {
		t.Errorf("got %q\nwant %q", got, want)
	}
	if got := SetFields("---\na: 1\n---", Field{"a", "2"}); got != "---\na: \"2\"\n---" {
		t.Errorf("末尾の改行が無い本文は付けない: %q", got)
	}
}
