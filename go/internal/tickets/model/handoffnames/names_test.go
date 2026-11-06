package handoffnames

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/pathcase"
)

// 見本は tests/handoff_names_samples.py と同じ（sh の試験も同じ見本を通る）。
var samples = [][2]string{
	{".claude/settings.json", "handoff-.claude/settings.json"},
	{".claude/hooks/lint-py.sh", "handoff-.claude/hooks/lint-py.sh"},
	{"projects/web/.claude/settings.json", "projects/web/handoff-.claude/settings.json"},
	{"a/.claude/b/.claude/c.json", "a/handoff-.claude/b/handoff-.claude/c.json"},
	{"日本語/.claude/x.md", "日本語/handoff-.claude/x.md"},
	{".CLAUDE/settings.json", "handoff-.claude/settings.json"},
	{".Claude/hooks/x.sh", "handoff-.claude/hooks/x.sh"},
	{".ccnavi/docs/guide.md", ".ccnavi/docs/guide.md"},
	{"docs/claude/x.md", "docs/claude/x.md"},
	{"my.claude/x.md", "my.claude/x.md"},
	{".claude-old/x.md", ".claude-old/x.md"},
	{"x/.claude", "x/handoff-.claude"},
}

var reverseSamples = [][2]string{
	{"handoff-.claude/settings.json", ".claude/settings.json"},
	{"projects/web/handoff-.claude/hooks/x.sh", "projects/web/.claude/hooks/x.sh"},
	{"Handoff-.Claude/settings.json", ".claude/settings.json"},
	{"HANDOFF-.CLAUDE/a/handoff-.claude/b", ".claude/a/.claude/b"},
	{"handoff-.claude-old/x", "handoff-.claude-old/x"},
}

func table(t *testing.T) *Table {
	t.Helper()
	got, err := Default()
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func TestConvert(t *testing.T) {
	tb := table(t)
	for _, s := range samples {
		if got := tb.ToDraft(s[0]); got != s[1] {
			t.Errorf("ToDraft(%q) = %q, want %q", s[0], got, s[1])
		}
		if back := tb.FromDraft(tb.ToDraft(s[0])); pathcase.Fold(back) != pathcase.Fold(s[0]) {
			t.Errorf("往復 %q → %q", s[0], back)
		}
		if !strings.Contains(strings.ToLower(s[0]), "handoff-") && tb.ReservedProblem(s[0]) != "" {
			t.Errorf("予約名でないのに断った: %q", s[0])
		}
	}
	for _, s := range reverseSamples {
		if got := tb.FromDraft(s[0]); got != s[1] {
			t.Errorf("FromDraft(%q) = %q, want %q", s[0], got, s[1])
		}
	}
}

func TestReservedNamesAreRefused(t *testing.T) {
	tb := table(t)
	for _, rel := range []string{"handoff-.claude/x.json", "a/Handoff-.CLAUDE/x.json", ".claude/handoff-.claude/x.json"} {
		if got := tb.ReservedProblem(rel); !strings.Contains(got, "予約") || !strings.Contains(got, "`.claude`") {
			t.Errorf("ReservedProblem(%q) = %q", rel, got)
		}
	}
}

// sh は ASCII の大文字小文字しか畳めない。`ﬀ` は畳むと `ff` になるが、表には当てない。
func TestNonASCIIElementsAreNeverMatched(t *testing.T) {
	tb := table(t)
	weird := "handoﬀ-.claude/x.json"
	if got := tb.FromDraft(weird); got != weird {
		t.Errorf("FromDraft = %q", got)
	}
	if got := tb.ReservedProblem(weird); got != "" {
		t.Errorf("ReservedProblem = %q", got)
	}
}

func TestParse(t *testing.T) {
	got, err := Parse("# c\n\n.claude handoff-.claude\n  .vscode   handoff-.vscode  \n")
	if err != nil || len(got.pairs) != 2 || got.pairs[1] != (Pair{".vscode", "handoff-.vscode"}) {
		t.Errorf("Parse = %+v, %v", got, err)
	}
	for _, bad := range []string{".claude\n", "a b c\n", "a/b c\n", "日本 x\n"} {
		if _, err := Parse(bad); err == nil {
			t.Errorf("Parse(%q) が通った", bad)
		}
	}
	if _, err := Parse("x\ty\n日本 x\n"); err == nil || err.Error() != NamesFile+" の 2 行目を読めない: '日本 x'" {
		t.Errorf("苦情の文: %v", err)
	}
}

// 表に 1 行足せば、その名前も同じ規則で読み替える。
func TestAddedRowWorksTheSame(t *testing.T) {
	tb := table(t).With(Pair{".ccnavi", "handoff-.ccnavi"})
	if got := tb.ToDraft(".ccnavi/docs/guide.md"); got != "handoff-.ccnavi/docs/guide.md" {
		t.Errorf("ToDraft = %q", got)
	}
	if got := tb.FromDraft("handoff-.ccnavi/docs/guide.md"); got != ".ccnavi/docs/guide.md" {
		t.Errorf("FromDraft = %q", got)
	}
	if got := table(t).ToDraft(".ccnavi/x"); got != ".ccnavi/x" {
		t.Errorf("元の表が変わった: %q", got)
	}
}

// 埋め込んだ写しは、sh が読む元の表と同じ中身でなければならない。
func TestEmbeddedCopyMatchesTheScriptsTable(t *testing.T) {
	source, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "..", ".ccnavi", "scripts", NamesFile))
	if err != nil {
		t.Fatal(err)
	}
	if string(source) != embedded {
		t.Errorf("%s の写しが .ccnavi/scripts/ のものと違う。写し直す", NamesFile)
	}
	if got := table(t); len(got.pairs) != 1 || got.pairs[0] != (Pair{".claude", "handoff-.claude"}) {
		t.Errorf("表: %+v", got.pairs)
	}
}
