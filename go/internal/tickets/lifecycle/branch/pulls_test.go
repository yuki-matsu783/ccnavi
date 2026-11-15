package branch

import (
	"testing"

	"ccnavi/internal/infra/pyjson"
)

// issue 12 に関係する PR だけが mrs の形で残る（本文の参照・issues/ の URL・元ブランチ名の番号）。
func TestRelatedPulls(t *testing.T) {
	pr := func(n int, title, body, ref, from string) any {
		return pyjson.NewObject("number", n, "title", title, "body", body, "state", "open", "html_url", "u",
			"head", pyjson.NewObject("ref", ref, "repo", pyjson.NewObject("full_name", from)),
			"base", pyjson.NewObject("ref", "main"))
	}
	list := []any{
		pr(1, "fix #12", "", "x", "o/r"),
		pr(2, "", "see https://h/o/r/issues/12", "y", "O/R"),
		pr(3, "", "", "feature-12-login", "other/r"),
		pr(4, "a#12 &#12 #123", "issues/120", "feature-102-a-12-b", "o/r"),
		pr(5, "", "", "claude/x-12", "o/r"),
	}
	got := relatedPulls(list, 12, "o/r", []string{"feature"})
	want := map[int]bool{1: false, 2: false, 3: true, 5: false}
	if len(got) != len(want) {
		t.Fatalf("件数 %d: %v", len(got), got)
	}
	for _, raw := range got {
		m := raw.(*pyjson.Object)
		n := m.Value("number").(int)
		fork, ok := want[n]
		if !ok || m.Value("fork") != fork || m.Value("base") != "main" {
			t.Errorf("PR %d: %v", n, m)
		}
	}
}
