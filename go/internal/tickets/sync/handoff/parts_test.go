package handoff

import (
	"encoding/base64"
	"strings"
	"testing"

	"ccnavi/internal/infra/difflib"
	"ccnavi/internal/infra/golden"
)

// 差分（difflib.unified_diff の写し）と、rules.yml の下書きが防御の記述を動かすかの判定を、記録した答え（testdata/parts.json）と突き合わせる。

type partsAnswers struct {
	Diff    [][]string `json:"diff"`
	Lines   [][]string `json:"lines"`
	Defense []string   `json:"defense"`
}

func linesOf(n int, f func(i int) string) []string {
	out := make([]string, n)
	for i := range out {
		out[i] = f(i)
	}
	return out
}

var diffCases = [][2][]string{
	{{}, {}},
	{{}, {"a"}},
	{{"a"}, {}},
	{{"a", "b", "c"}, {"a", "b", "c"}},
	{{"a", "b", "c", "d"}, {"a", "x", "c", "d", "e"}},
	{linesOf(30, func(i int) string { return string(rune('a' + i%26)) }),
		linesOf(30, func(i int) string {
			if i == 3 || i == 20 {
				return "changed"
			}
			return string(rune('a' + i%26))
		})},
	// 200 行以上で、よく出る行（空行）を手がかりから外す（autojunk）。
	{linesOf(240, func(i int) string {
		if i%2 == 0 {
			return ""
		}
		return "l" + string(rune('A'+i%50))
	}), linesOf(250, func(i int) string {
		if i%2 == 0 {
			return ""
		}
		if i%37 == 1 {
			return "new"
		}
		return "l" + string(rune('A'+i%50))
	})},
	{{"x", "y", "x", "y", "x"}, {"y", "x", "y", "x", "y"}},
}

var linesCases = []any{
	nil, "", "a", "a\n", "a\r\nb\rc\n", "\ufeffa\n", "e\u0301\u200b\u202e\x01\tb\n", "\xff\xe3\x81\n\xed\xa0\x80",
}

const guardRules = "version: 1\ndeny:\n  - id: guard-a\n    match: Write\n    glob: '*/a/*'\n    except: [{glob: '*/a/ok/*'}]\n" +
	"    message: m\n  - id: plain\n    match: Bash\n    regex: x\n    message: m\n  - {match: Bash, regex: y, message: m}\n" +
	"allow:\n  - id: ok\n    match: Read\n"

var defenseCases = [][2]any{
	{guardRules, guardRules},
	{nil, guardRules},
	{guardRules, nil},
	{guardRules, strings.Replace(guardRules, "*/a/ok/*", "*/a/*", 1)},
	{guardRules, strings.Replace(guardRules, "  - id: plain\n    match: Bash\n    regex: x\n    message: m\n", "", 1)},
	{guardRules, strings.Replace(guardRules, "regex: x", "regex: z", 1)},
	{guardRules, strings.Replace(guardRules, "regex: y", "regex: w", 1)},
	{guardRules, guardRules + "  - id: GUARD-new\n    match: Edit\n"},
	{guardRules, strings.Replace(guardRules, "allow:\n", "allow:\n  - id: guard-a\n    match: Write\n", 1)},
	{guardRules, strings.Replace(guardRules, "  - id: plain", "  - id: more\n    match: Bash\n    regex: q\n    message: m\n  - id: plain", 1)},
	{"deny: [\n", guardRules},
	{guardRules, "- a\n"},
	{guardRules, "deny: 3\n"},
	{guardRules, "\ufeffdeny: []\n"},
	{"", guardRules},
	{guardRules, "deny:\n  - {id: guard-x, when: 2026-01-01, n: 1.5, s: !!set {a: null}}\n"},
	{guardRules, "\xff"},
	{"deny:\n" + strings.Repeat("  - {id: d, match: Bash, regex: a}\n", 1) +
		"  - {id: guard-1}\n  - {id: guard-2}\n  - {id: guard-3}\n  - {id: guard-4}\n  - {id: guard-5}\n  - {id: guard-6}\n", ""},
}

func bytesOf(v any) []byte {
	if v == nil {
		return nil
	}
	return []byte(v.(string))
}

// b64 は JSON で渡せないバイト列（UTF-8 でないもの）を渡す形。nil は null。
func b64(v any) any {
	if v == nil {
		return nil
	}
	return base64.StdEncoding.EncodeToString([]byte(v.(string)))
}

func TestPartsMatchGolden(t *testing.T) {
	var lines []any
	for _, c := range linesCases {
		lines = append(lines, b64(c))
	}
	var defense [][2]any
	for _, c := range defenseCases {
		defense = append(defense, [2]any{b64(c[0]), b64(c[1])})
	}
	answers := golden.Load[partsAnswers](t, "parts")
	for i, c := range diffCases {
		got := difflib.UnifiedDiff(c[0], c[1], "a/x", "b/x", diffContext)
		if strings.Join(got, "\n") != strings.Join(answers.Diff[i], "\n") {
			t.Errorf("差分 %d が違う\n--- go\n%s\n--- golden\n%s", i, strings.Join(got, "\n"), strings.Join(answers.Diff[i], "\n"))
		}
	}
	for i, c := range linesCases {
		got := visibleLines(bytesOf(c))
		if strings.Join(got, "\n") != strings.Join(answers.Lines[i], "\n") || len(got) != len(answers.Lines[i]) {
			t.Errorf("行 %q: %q, golden は %q", c, got, answers.Lines[i])
		}
	}
	for i, c := range defenseCases {
		if got := defenseChange(bytesOf(c[0]), bytesOf(c[1])); got != answers.Defense[i] {
			t.Errorf("防御 %d: %q, golden は %q", i, got, answers.Defense[i])
		}
	}
}
