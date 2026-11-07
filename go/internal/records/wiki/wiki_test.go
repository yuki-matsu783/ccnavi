package wiki

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

func pattern(target string, sources ...string) string {
	return fmt.Sprintf("---\ntype: learning-wiki\ntarget: %s\nsources: [%s]\n"+
		"title: t\ndescription: d\ntags: [a]\nkeywords: [b]\n---\n\n本文\n", target, strings.Join(sources, ", "))
}

// impact の patterns は "名前: 数" を並べた行。
func impact(proposal string, status Decision, decided string, patterns ...string) string {
	var counts strings.Builder
	for _, p := range patterns {
		counts.WriteString("  " + p + "\n")
	}
	return fmt.Sprintf("---\nproposal: %s\ntarget: docs\nfiles: [a.md]\npatterns:\n%s"+
		"status: %s\nreason: r\ndecided_at: '%s'\n---\n\n差分\n", proposal, counts.String(), status, decided)
}

const jan1 = "2026-01-01T00:00:00+00:00"

func statusOf(sources []string, impacts ...string) State {
	texts := map[string]string{}
	for n, text := range impacts {
		texts[fmt.Sprint("i", n)] = text
	}
	return Rows(Build(map[string]string{"p": pattern("docs", sources...)}, texts, true))[0].Status
}

func TestStatus(t *testing.T) {
	abc := []string{"a", "b", "c"}
	cases := []struct {
		name    string
		sources []string
		impacts []string
		want    State
	}{
		{"記録が無ければ候補", []string{"a"}, nil, Candidate},
		{"承認待ちが何より先", []string{"a"}, []string{
			impact("x", Rejected, "2026-02-01T00:00:00+00:00", "p: 1"), impact("y", Pending, "", "p: 1")}, Proposing},
		{"新しい却下", abc, []string{
			impact("a", Deferred, jan1, "p: 1"), impact("b", Rejected, "2026-02-01T00:00:00+00:00", "p: 1")}, Settled},
		{"新しい延期で数が増えた", abc, []string{
			impact("b", Rejected, "2026-02-01T00:00:00+00:00", "p: 1"), impact("c", Deferred, "2026-03-01T00:00:00+00:00", "p: 1")}, Candidate},
		// 09:00+09:00 は 00:00Z。UTC の 00:30 のほうが新しい。
		{"時差を揃えて比べる", []string{"a", "b"}, []string{
			impact("a", Rejected, "2026-01-01T09:00:00+09:00", "p: 1"), impact("b", Deferred, "2026-01-01T00:30:00+00:00", "p: 1")}, Candidate},
		{"同じ日時は提案の名前で後のもの", []string{"a", "b"}, []string{
			impact("aaa", Deferred, jan1, "p: 1"), impact("zzz", Rejected, jan1, "p: 1")}, Settled},
		// 決着のときに数を今の値へ書き直した延期は、すぐ候補へ戻らない。
		{"書き直した延期", abc, []string{impact("d", Deferred, jan1, "p: 3")}, Settled},
		{"書き直した延期の後に増えた", []string{"a", "b", "c", "d"}, []string{impact("d", Deferred, jan1, "p: 3")}, Candidate},
		{"大文字小文字を区別しない", abc, []string{impact("d", Deferred, jan1, "P: 3")}, Settled},
	}
	for _, c := range cases {
		if got := statusOf(c.sources, c.impacts...); got != c.want {
			t.Errorf("%s: %s, want %s", c.name, got, c.want)
		}
	}
	// 延期はパターンごとに比べる。
	w := Build(map[string]string{"p": pattern("docs", "a", "b", "c"), "q": pattern("docs", "a", "b", "c")},
		map[string]string{"d": impact("d", Deferred, jan1, "p: 2", "q: 5")}, true)
	if rows := Rows(w); rows[0].Status != Candidate || rows[1].Status != Settled {
		t.Errorf("rows = %+v", rows)
	}
}

func TestDuplicateSourcesCountOnce(t *testing.T) {
	w := Build(map[string]string{"p": pattern("docs", "a", "A", "a", "b")}, nil, true)
	if p := w.Patterns[0]; p.Count() != 2 || strings.Join(p.Sources, ",") != "a,b" {
		t.Errorf("sources = %v", p.Sources)
	}
}

func TestBrokenFilesAreNamedAndNotCounted(t *testing.T) {
	w := Build(
		map[string]string{"ok": pattern("docs", "a"), "bad": "no front", "OK": pattern("docs", "b")},
		map[string]string{
			"i":     "---\nstatus: x\n---\n",
			"good":  impact("g", Rejected, jan1, "ok: 1"),
			"naive": impact("n", Rejected, "2026-01-01T00:00:00", "p: 1"),
			"w":     impact("w", Pending, "", "p: 1"),
		}, true)
	if rows := Rows(w); len(rows) != 1 || rows[0].Name != "OK" {
		t.Errorf("rows = %+v", rows)
	}
	got := map[string]string{}
	for _, b := range w.Broken {
		got[b.File] = b.Problem
	}
	want := map[string]string{
		"docs/wiki/patterns/bad.md": "frontmatter が無い",
		"docs/wiki/patterns/ok.md":  "OK と大文字小文字だけが違う同名",
		"docs/wiki/impact/i.md":     "proposal が無い",
		"docs/wiki/impact/naive.md": "decided_at にタイムゾーンが無い",
	}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Errorf("broken = %v", got)
	}
	if p := PendingImpacts(w); len(p) != 1 || p[0].Proposal != "w" || len(Candidates(w)) != 0 {
		t.Errorf("pending = %+v", p)
	}
}

func TestParseProblems(t *testing.T) {
	cases := []struct{ text, want string }{
		{"---\ntype: x\n", "frontmatter が閉じていない"},
		{"---\n- a\n---\n", "frontmatter が対応表（key: value）でない"},
		{"---\na: [\n---\n", "frontmatter を YAML として読めない (ParserError)"},
		{"---\na: b: c\n---\n", "frontmatter を YAML として読めない (ScannerError)"},
		{"---\ntype: other\n---\n", "type が learning-wiki でない"},
		{"---\ntype: learning-wiki\ntarget: x\n---\n", "target が rule risk phase template skill docs のどれでもない"},
		{"---\ntype: learning-wiki\ntarget: docs\nsources: []\n---\n", "sources が空（観測元が無い）"},
		{"---\ntype: learning-wiki\ntarget: docs\nsources: [' ']\n---\n", "sources が空でない文字列の一覧でない"},
		{"---\ntype: learning-wiki\ntarget: docs\nsources: [a]\ntitle: 1\n---\n", "title が文字列でない"},
		{"\uFEFF---\ntype: learning-wiki\ntarget: docs\nsources: [a]\n...\n", ""},
	}
	for _, c := range cases {
		if _, got := ParsePattern("p", c.text); got != c.want {
			t.Errorf("ParsePattern(%q) = %q, want %q", c.text, got, c.want)
		}
	}
	impacts := []struct{ text, want string }{
		{impact("x", "x", jan1), "status が 承認待ち 承認 却下 延期 のどれでもない"},
		{impact("x", Rejected, ""), "decided_at が無い（承認待ちのほかは日時が要る）"},
		{impact("x", Rejected, "yesterday"), "decided_at が日時として読めない"},
		{impact("x", Rejected, jan1, "p: -1"), "patterns の p の数が 0 以上の整数でない"},
		{impact("x", Rejected, jan1, "p: true"), "patterns の p の数が 0 以上の整数でない"},
		{strings.Replace(impact("x", Rejected, jan1), "'"+jan1+"'", "2026-01-01", 1), "decided_at が日時でない"},
		{strings.Replace(impact("x", Rejected, jan1), "'"+jan1+"'", "2026-01-01 09:00:00+09:00", 1), ""},
		{impact("x", Rejected, "2026-01-01T00:00Z"), ""},
		{impact("x", Rejected, "20260101T000000.5+0900"), ""},
	}
	for _, c := range impacts {
		if _, got := parseImpact(c.text); got != c.want {
			t.Errorf("parseImpact(%q) = %q, want %q", c.text, got, c.want)
		}
	}
}

func TestThresholds(t *testing.T) {
	if SourcesThreshold("") != 3 || SourcesThreshold("zero") != 3 || SourcesThreshold("0") != 3 || SourcesThreshold(" 1 ") != 1 {
		t.Error("SourcesThreshold")
	}
	if NoticeCount("") != 5 || NoticeCount("2") != 2 {
		t.Error("NoticeCount")
	}
}

func write(t *testing.T, root, rel, text string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(rel))
	os.MkdirAll(filepath.Dir(path), 0o777)
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

func names(w *Wiki) []string {
	var out []string
	for _, p := range w.Patterns {
		out = append(out, p.Name)
	}
	sort.Strings(out)
	return out
}

func TestLoadTreeAndCommit(t *testing.T) {
	root := t.TempDir()
	if w := LoadTree(root); w.Present || len(w.Patterns) != 0 {
		t.Errorf("empty = %+v", w)
	}
	write(t, root, "docs/wiki/patterns/p1.md", pattern("rule", "a", "b", "c"))
	write(t, root, "docs/wiki/patterns/p2.MD", pattern("skill", "a"))
	write(t, root, "docs/wiki/patterns/notes.txt", "x")
	write(t, root, "docs/wiki/patterns/broken.md", "x\n")
	write(t, root, "docs/wiki/patterns/big.md", strings.Repeat("x", fileLimit+1))
	write(t, root, "docs/wiki/impact/w.md", impact("w", Pending, "", "p2: 1"))
	w := LoadTree(root)
	if !w.Present || strings.Join(names(w), ",") != "p1,p2" {
		t.Errorf("tree = %+v", w)
	}
	var files []string
	for _, b := range w.Broken {
		files = append(files, b.File+" "+b.Problem)
	}
	if strings.Join(files, "|") != "docs/wiki/patterns/big.md 大きすぎて読まない|docs/wiki/patterns/broken.md frontmatter が無い" {
		t.Errorf("broken = %q", files)
	}

	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	git := func(args ...string) {
		cmd := exec.Command("git", append([]string{"-C", root, "-c", "user.email=a@b", "-c", "user.name=t"}, args...)...)
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v %s", args, err, out)
		}
	}
	git("init", "--quiet", "-b", "main")
	git("add", "-A")
	git("commit", "--quiet", "-m", "wiki")
	sha := CommitOf(root, "main", time.Time{})
	if len(sha) < 40 {
		t.Fatalf("sha = %q", sha)
	}
	os.Remove(filepath.Join(root, "docs/wiki/patterns/p1.md"))
	found := LoadCommit(root, sha, time.Now().Add(10*time.Second))
	if found == nil || strings.Join(names(found), ",") != "p1,p2" || len(found.Broken) != 2 {
		t.Errorf("commit = %+v", found)
	}
	if LoadCommit(root, strings.Repeat("0", 40), time.Time{}) != nil {
		t.Error("無いコミットを読めた")
	}
	if CommitOf(root, "nope", time.Time{}) != "" {
		t.Error("無い参照を読めた")
	}
}
