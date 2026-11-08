package docs

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/catalog/docindex"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/testkit"
)

// 配る文書（.ccnavi/docs/*.md と CCNAVI.md）が案内する `--keyword <語>` が、どれかの文書の keywords に当たる。
// --keyword は完全一致なので、案内に書いた語が keywords に無いと、引いても 0 件になる。

// guidedKeyword は `--keyword <語>` の語（`<語>` のような置き換え記号は除く）。
var guidedKeyword = regexp.MustCompile("--keyword[ =]+([^\\s`'\"<>|)）、。]+)")

// notMeantToMatch は当たらない例として書いている語（frontmatter.md の「keywordsの注意」）。
var notMeantToMatch = map[[2]string]bool{{"frontmatter.md", "ログ"}: true}

func keywordFold(s string) string { return pyfmt.Casefold(pyfmt.NFC(s)) }

type shippedDocs struct {
	dir   string
	names []string
}

func loadShipped(t *testing.T) shippedDocs {
	dir := filepath.Join(testkit.RepoRoot(t), ".ccnavi", "docs")
	paths, _ := filepath.Glob(filepath.Join(dir, "*.md"))
	sort.Strings(paths)
	return shippedDocs{dir, paths}
}

func (s shippedDocs) matched(t *testing.T, word string) []string {
	var found []string
	for _, p := range s.names {
		data, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		for _, k := range arrOf(docindex.FrontMatter(data).Value("keywords")) {
			if keywordFold(k) == keywordFold(word) {
				found = append(found, strings.TrimSuffix(filepath.Base(p), ".md"))
				break
			}
		}
	}
	return found
}

func arrOf(v any) []string {
	switch x := v.(type) {
	case nil:
		return nil
	case []any:
		out := make([]string, len(x))
		for i, item := range x {
			out[i] = pyfmt.Str(item)
		}
		return out
	}
	return []string{pyfmt.Str(v)}
}

func TestEveryGuidedKeywordMatchesADoc(t *testing.T) {
	s := loadShipped(t)
	guides := append(slices.Clone(s.names), filepath.Join(filepath.Dir(s.dir), "CCNAVI.md"))
	for _, path := range guides {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range guidedKeyword.FindAllStringSubmatch(string(data), -1) {
			if notMeantToMatch[[2]string{filepath.Base(path), m[1]}] {
				continue
			}
			if len(s.matched(t, m[1])) == 0 {
				t.Errorf("%s: --keyword %s が当たる文書が無い", filepath.Base(path), m[1])
			}
		}
	}
}

// lint と ticket start の案内が書く語は `--keyword` の形でないので別に見る。
func TestTheProposalGuidanceWordsMatchTheExpectedDocs(t *testing.T) {
	s := loadShipped(t)
	for word, want := range map[string][]string{
		"proposal-format": {"proposal-format"},
		"提案書式":            {"proposal-format"},
		"phases":          {"grow-phases", "proposal-format"},
	} {
		if got := s.matched(t, word); !slices.Equal(got, want) {
			t.Errorf("%s: %v, want %v", word, got, want)
		}
	}
	data, _ := os.ReadFile(filepath.Join(filepath.Dir(s.dir), "CCNAVI.md"))
	if !strings.Contains(string(data), "`proposal-format.md`") {
		t.Error("CCNAVI.md が proposal-format.md を案内しない")
	}
}
