package agree

import (
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
)

func scoped(t *testing.T, sections map[string][]string) *ticket.Ticket {
	t.Helper()
	text := "---\nversion: 1\nticket: i0001\ntitle: 親\n"
	for _, name := range []string{"deny", "ask", "allow"} {
		if len(sections[name]) == 0 {
			continue
		}
		text += name + ":\n"
		for _, e := range sections[name] {
			text += "  - match: Write|Edit\n    " + e + "\n"
		}
	}
	found, problems := ticket.Parse(text + "---\n本文\n")
	if found == nil {
		t.Fatalf("読めない: %v", problems)
	}
	return found
}

// 範囲の差分は種別（glob と regex）を組にして比べる。文字列だけだと範囲の広がりが画面から消える。
func TestScopeDiffLines(t *testing.T) {
	cases := []struct {
		before, after map[string][]string
		want, absent  []string
	}{
		{map[string][]string{"allow": {`glob: "src/app"`}}, map[string][]string{"allow": {`regex: "src/app"`}},
			[]string{"    allow 追加: regex(src/app)", "    allow 削除: src/app"}, nil},
		{map[string][]string{"allow": {`glob: "regex:src/app"`}}, map[string][]string{"allow": {`regex: "src/app"`}},
			[]string{"    allow 追加: regex(src/app)", "    allow 削除: regex:src/app"}, nil},
		{map[string][]string{"allow": {`glob: "src/*"`, `glob: "old/*"`}},
			map[string][]string{"allow": {`glob: "src/*"`, `glob: "docs/*"`}, "deny": {`glob: "src/secret/*"`}},
			[]string{"■ 範囲の差分（改版で親に固定する範囲）", "    allow 追加: docs/*", "    allow 削除: old/*", "    deny 追加: src/secret/*"},
			[]string{"    allow 追加: src/*", "    allow 削除: src/*"}},
	}
	for i, c := range cases {
		got := scopeDiffLines(scoped(t, c.before), scoped(t, c.after))
		for _, w := range c.want {
			if !slices.Contains(got, w) {
				t.Errorf("%d: %q が無い: %q", i, w, got)
			}
		}
		for _, a := range c.absent {
			if slices.Contains(got, a) {
				t.Errorf("%d: %q がある", i, a)
			}
		}
	}
	once := scoped(t, map[string][]string{"allow": {`glob: "src/*"`}})
	twice := scoped(t, map[string][]string{"allow": {`glob: "src/*"`, `glob: "src/*"`}})
	if got := scopeDiffLines(once, twice); got != nil {
		t.Errorf("重複は差分ではない: %q", got)
	}
}

// 改版で書く frontmatter は、範囲を提案の値に差し替え、提案に無い節を外し、承認済みチケットは変えない。
func TestRevisedFront(t *testing.T) {
	current := scoped(t, map[string][]string{"allow": {`glob: "src/*"`}, "ask": {`glob: "docs/*"`}})
	revised := scoped(t, map[string][]string{"allow": {`glob: "src/*"`, `glob: "docs/*"`}, "deny": {`glob: "src/secret/*"`}})
	front := revisedFront(current, revised)
	if front.Has("ask") || front.Get("allow") == nil || front.Get("deny") == nil {
		t.Errorf("範囲の節が差し替わらない: %v", front.Keys())
	}
	if got := current.Paths("allow"); !slices.Equal(got, []string{"src/*"}) || !current.Raw.Has("ask") {
		t.Errorf("承認済みチケットが変わった: %v", got)
	}
}

func TestBullets(t *testing.T) {
	if got := bullets("allow", []string{"a/*", "b/*"}); !slices.Equal(got, []string{"    allow:", "      - a/*", "      - b/*"}) {
		t.Errorf("got %q", got)
	}
	if got := bullets("deny", nil); got != nil {
		t.Errorf("空なら何も出さない: %q", got)
	}
}

// 回帰: 指紋は解析したときに読んだバイト列（Source）から取る。読み直すと、照合した中身と承認する中身が別の読みになる。
func TestDigestUsesTheParsedBytes(t *testing.T) {
	t1 := &ticket.Ticket{ID: "i0001", Path: "/nonexistent/i0001.md", Source: []byte("a")}
	batch := []*candidates.Candidate{{Ticket: t1}}
	first := ProposalsDigest(batch)
	t1.Source = []byte("b")
	if second := ProposalsDigest(batch); second == first || len(second) != 64 || strings.ToLower(second) != second {
		t.Errorf("バイト列の違いが指紋に出ない: %s %s", first, second)
	}
}
