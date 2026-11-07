package branch

import (
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func refsOfText(text string) [][2]any {
	out := [][2]any{}
	for _, r := range PromptRefs(text) {
		out = append(out, [2]any{r.Kind, r.Number})
	}
	return out
}

// 依頼文から issue・MR の指定を見つける式のヒットと外れ（Python の tests/ticket/test_branchfind_refs.py）。外れで
// 指示を足すと、関係の無い依頼のたびにブランチを探させることになるので、外れも表で固定する。
var refHits = map[string][][2]any{
	"#152 を直して":       {{Issue, 152}},
	"＃152 を直して":       {{Issue, 152}},
	"issue 152 をお願い":  {{Issue, 152}},
	"Issue #7 の続き":    {{Issue, 7}},
	"issue: 42":       {{Issue, 42}},
	"issue-152 のブランチ": {{Issue, 152}},
	"issues/152 を見て":  {{Issue, 152}},
	"(#9) を直す":        {{Issue, 9}},
	"https://github.com/acme/w/issues/152 を直して":     {{Issue, 152}},
	"https://gitlab.com/g/p/-/issues/31":            {{Issue, 31}},
	"https://github.com/acme/w/pull/5/files を見て":    {{MR, 5}},
	"https://gitlab.com/g/sub/p/-/merge_requests/9": {{MR, 9}},
	"!5 のレビュー":                                      {{MR, 5}},
	"（!6）":                                          {{MR, 6}},
	"MR 5 を直して":                                     {{MR, 5}},
	"MR !5":                                         {{MR, 5}},
	"PR #12 の指摘":                                    {{MR, 12}},
	"このPR 3を":                                       {{MR, 3}},
	"pull request 4":                                {{MR, 4}},
	"マージリクエスト!4":                                    {{MR, 4}},
	"プルリク 8 の続き":                                    {{MR, 8}},
	"#3 と !4":                                       {{Issue, 3}, {MR, 4}},
	"#152を直して":                                      {{Issue, 152}},
	"「#152」の件":                                      {{Issue, 152}},
	"!5 の指摘":                                        {{MR, 5}},
	"#12、#13。":                                      {{Issue, 12}, {Issue, 13}},
	"#152から着手":                                      {{Issue, 152}},
	"#152です":                                        {{Issue, 152}},
	"#152！":                                         {{Issue, 152}},
	"これは#152です":                                     {{Issue, 152}},
	"#152と#153":                                     {{Issue, 152}, {Issue, 153}},
	"issue 2のうち1つ":                                  {{Issue, 2}},
	"#7 と issue 7 と #7":                             {{Issue, 7}},
	"https://github.com/acme/w/issues/152#issuecomment-1 と issue 152": {{Issue, 152}},
}

var refMisses = []string{
	"# 見出し\n本文", "## 2. 手順", "色は #fff と #a1b2c3", "color: #333333; background-color: #123456",
	"#000 と #012345（0 で始まる）", "C# 5 と F# の話", "C#5", "&#123; は文字参照", "##12", "page#5 の節", "すごい!5", "!!5",
	"```\n# コメント #99\n```", "Mr. Smith", "issue を探して", "#12a", "", "手順#2を見直して", "issue 2つ目", "PR 3本", "MR 3件",
	"#2つ目", "#2-3 の範囲", "issue 152(仮)", "`#12` は設定の例", "`!5` と `issue 7`",
	"<system-reminder>#12 を直す</system-reminder>", "[ccnavi] #4 の件\n\n", "Another Claude session sent a message: #8",
}

func TestPromptRefs(t *testing.T) {
	for text, want := range refHits {
		if got := refsOfText(text); !reflect.DeepEqual(got, want) {
			t.Errorf("%q: %v, want %v", text, got, want)
		}
	}
	for _, text := range refMisses {
		if got := refsOfText(text); len(got) != 0 {
			t.Errorf("%q: %v", text, got)
		}
	}
	if r := PromptRefs("https://github.com/acme/w/issues/152#issuecomment-1 と issue 152"); r[0].Repo != "acme/w" {
		t.Errorf("repo %q", r[0].Repo)
	}
	var many []string
	for n := 1; n < 20; n++ {
		many = append(many, "#"+strconv.Itoa(n))
	}
	if got := PromptRefs(strings.Join(many, " ")); len(got) != maxRefs {
		t.Errorf("%d 件", len(got))
	}
}

func TestLabel(t *testing.T) {
	if got := (Ref{Kind: Issue, Number: 152}).Label(); got != "issue #152" {
		t.Error(got)
	}
	if got := (Ref{MR, 5, "acme/w"}).Label(); got != "MR !5（acme/w）" {
		t.Error(got)
	}
}

// sh 側（tests/sh/test_branches_sh.py）にも同じ表がある。規則を変えたら両方を直す。
func TestNamesNumber(t *testing.T) {
	prefixes := []string{"feature", "fix", "chore", "docs", "refactor", "test"}
	slug := "feature-102-main-sh-の使い方の表示を-1-行足す-c3-検証用"
	cases := []struct {
		name   string
		number int
		want   bool
	}{
		{slug, 1, false}, {slug, 102, true}, {"feature-1-login", 1, true}, {"fix-1-typo", 1, true}, {"fix-1-typo", 2, false},
		{"fix/152-y", 152, true}, {"issue-152", 152, true}, {"x-152", 152, true}, {"other-102-a-1-b", 1, true},
		{"feature-152-login", 152, true}, {"152-login", 152, true}, {"feature_152_x", 152, true}, {"v1.152.x", 152, true},
		{"feature-1520-x", 152, false}, {"feature-2152-x", 152, false}, {"v15-2", 152, false}, {"main", 152, false},
		{"i152", 152, false}, {"152a", 152, false}, {"claude/tender-volta-qmy99q", 99, false}, {"claude/zesty-a5b-x5y7z9", 5, false},
		{"x-1520-152", 152, true},
	}
	for _, c := range cases {
		if got := namesNumber(c.name, c.number, prefixes); got != c.want {
			t.Errorf("%s %d: %v", c.name, c.number, got)
		}
	}
	if namesNumber("story-102-a-1-b", 1, []string{"story"}) || !namesNumber("story-102-a-1-b", 1, prefixes) {
		t.Error("先頭の語を prefixes から読んでいない")
	}
}
