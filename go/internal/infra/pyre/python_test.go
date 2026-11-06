package pyre

import (
	"bytes"
	"encoding/json"
	"flag"
	"math/rand/v2"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Python の re と突き合わせる。リポジトリの .venv の Python が無ければ飛ばす。

// Python の答えは testdata に写して持つ。Python があれば聞き直して写しと比べ（食い違えば -update で取り直す）、
// 無ければ写しを読む。Python を外したあとも、同じ答えを確かめ続けるため。
var update = flag.Bool("update", false, "testdata の Python の答えを取り直す")

func venvPython(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		for _, rel := range []string{".venv/bin/python", ".venv/Scripts/python.exe"} {
			p := filepath.Join(dir, rel)
			if _, err := os.Stat(p); err == nil {
				return p
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

// answers は Python の答え（ask）と testdata/<name>.json の写しを突き合わせて返す。
func answers[T any](t *testing.T, name string, ask func(py string) T) T {
	t.Helper()
	path := filepath.Join("testdata", name+".json")
	var saved T
	data, readErr := os.ReadFile(path)
	if readErr == nil {
		if err := json.Unmarshal(data, &saved); err != nil {
			t.Fatal(err)
		}
	}
	py := venvPython(t)
	if py == "" {
		if readErr != nil {
			t.Skip("Python も testdata の写しも無い")
		}
		return saved
	}
	fresh := ask(py)
	got, _ := json.Marshal(fresh)
	if *update {
		if err := os.MkdirAll("testdata", 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, append(got, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	} else if readErr != nil || !bytes.Equal(bytes.TrimSpace(data), got) {
		t.Errorf("%s が Python の答えと違う（go test -run %s -update で取り直す）", path, t.Name())
	}
	return fresh
}

const oracle = `
import json, re, sys
out = []
for case in json.load(sys.stdin):
    try:
        p = re.compile(case["p"], case["f"])
    except re.error as e:
        out.append({"err": str(e)}); continue
    except RecursionError:
        out.append({"err": "deep"}); continue
    except (OverflowError, ValueError) as e:
        out.append({"err": "overflow"}); continue
    res = []
    for s in case["s"]:
        found = []
        for m in p.finditer(s):
            g = m.group("k") if "k" in p.groupindex else None
            found.append([m.start(), m.end(), g])
        res.append([p.search(s) is not None, p.match(s) is not None, p.fullmatch(s) is not None, found])
    out.append({"res": res})
json.dump(out, sys.stdout)
`

type pyCase struct {
	P string   `json:"p"`
	F int      `json:"f"`
	S []string `json:"s"`
}

type pyResult struct {
	Err string  `json:"err"`
	Res [][]any `json:"res"`
}

func runOracle(t *testing.T, py string, cases []pyCase) []pyResult {
	t.Helper()
	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(py, "-I", "-W", "ignore", "-c", oracle)
	cmd.Stdin = bytes.NewReader(in)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("python: %v\n%s", err, stderr.String())
	}
	var res []pyResult
	if err := json.Unmarshal(out, &res); err != nil {
		t.Fatal(err)
	}
	return res
}

func goResult(c pyCase) pyResult {
	re, err := Compile(c.P, Flag(c.F))
	if err != nil {
		switch err {
		case ErrTooDeep:
			return pyResult{Err: "deep"}
		case ErrOverflow:
			return pyResult{Err: "overflow"}
		}
		return pyResult{Err: err.Error()}
	}
	var res [][]any
	for _, s := range c.S {
		var found []any
		for _, f := range re.FindAll(s) {
			var g any
			if _, ok := re.Group("k"); ok {
				if v, ok := f.Named("k"); ok {
					g = v
				}
			}
			found = append(found, []any{float64(f.Start), float64(f.End), g})
		}
		if found == nil {
			found = []any{}
		}
		res = append(res, []any{re.Search(s), re.Match(s), re.FullMatch(s), found})
	}
	return pyResult{Res: res}
}

func compare(t *testing.T, name string, cases []pyCase) {
	t.Helper()
	want := answers(t, name, func(py string) []pyResult { return runOracle(t, py, cases) })
	bad := 0
	for i, c := range cases {
		got := goResult(c)
		gj, _ := json.Marshal(got)
		wj, _ := json.Marshal(want[i])
		if !bytes.Equal(gj, wj) {
			bad++
			if bad <= 15 {
				t.Errorf("pattern %q flags %d subjects %q\n got  %s\n want %s", c.P, c.F, c.S, gj, wj)
			}
		}
	}
	t.Logf("%d 件を突き合わせた（読めない式 %d）", len(cases), countErr(want))
	if bad > 0 {
		t.Errorf("%d / %d 件が Python と違う", bad, len(cases))
	}
}

var tokens = []string{
	"a", "b", "A", "B", "k", "s", "/", `\\`, " ", `\n`, "é", "É", "ı", "İ", "K", "ſ", "日", "1", "_", "-", ".",
	"^", "$", `\b`, `\B`, `\w`, `\W`, `\s`, `\S`, `\d`, `\D`, `\Z`, `\A`, `\x00`, `\.`, `\-`,
	"[ab]", "[^a]", "[a-z]", "[A-Z0-9_]", `[^\s\x00]`, `[\w.-]`, `[\\/]`, "[^\\\\/]", "[ıİ]", "[K-k]", "[é-ê]", "[",
	"]", "(", ")", "(?:", "(?>", "(?P<k>", "(?=", "(?!", "(?<=a)", "(?<!b)", "|", "|", "*", "+", "?", "*?", "+?",
	"??", "*+", "++", "?+", "{2}", "{1,3}", "{,2}", "{2,}", "{", "}", "(?i:", "(?-i:", `\1`, "(?(1)a|b)", "(?P=k)",
	"(?i)", "(?x)", "#", "\\", `é`, `\p`,
}

var subjectChars = []rune{'a', 'b', 'A', 'B', 'k', 'K', 's', 'S', '/', '\\', ' ', '\n', 'é', 'É', 'ı', 'İ', 'i', 'I',
	'K', 'ſ', '日', '1', '٣', '_', '-', '\x00', '.', '\t', '　', '\x1c'}

func randomPattern(r *rand.Rand) string {
	var b strings.Builder
	for range 1 + r.IntN(7) {
		b.WriteString(tokens[r.IntN(len(tokens))])
	}
	return b.String()
}

func randomSubject(r *rand.Rand) string {
	var b strings.Builder
	for range r.IntN(9) {
		b.WriteRune(subjectChars[r.IntN(len(subjectChars))])
	}
	return b.String()
}

var flagChoices = []int{0, int(IgnoreCase), int(IgnoreCase | Multiline), int(DotAll | IgnoreCase), int(ASCII | IgnoreCase), int(Verbose)}

func TestAgainstPythonRandom(t *testing.T) {
	r := rand.New(rand.NewPCG(1, 2))
	var cases []pyCase
	for range 4000 {
		c := pyCase{P: randomPattern(r), F: flagChoices[r.IntN(len(flagChoices))]}
		for range 6 {
			c.S = append(c.S, randomSubject(r))
		}
		cases = append(cases, c)
	}
	compare(t, "random", cases)
}

func countErr(rs []pyResult) int {
	n := 0
	for _, r := range rs {
		if r.Err != "" {
			n++
		}
	}
	return n
}

var atoms = []string{
	"a", "b", "A", "k", "s", "/", `\\`, " ", "é", "İ", "ı", "K", "ſ", "日", "1", "_", "-", ".", `\n`, `\x00`,
	`\w`, `\W`, `\s`, `\S`, `\d`, `\b`, `\B`, "^", "$", `\Z`, `\A`, "[ab]", "[^a]", "[a-z]", `[^\s\x00]`, `[\w.-]`,
	`[\\/]`, "[ıİ]", "[K-k]", "[^K-k]", "[A-Z0-9]", "[é-ê]", `[Ā-\U00010428]`, `[\U00010400-\U00010428]`,
}

var quantifiers = []string{"", "", "", "*", "+", "?", "*?", "+?", "??", "*+", "++", "?+", "{2}", "{1,3}", "{,2}", "{2,}", "{0}"}

func validPattern(r *rand.Rand, depth int) string {
	var b strings.Builder
	for range 1 + r.IntN(4) {
		var atom string
		if depth < 2 && r.IntN(4) == 0 {
			inner := validPattern(r, depth+1)
			if r.IntN(3) == 0 {
				inner += "|" + validPattern(r, depth+1)
			}
			open := []string{"(", "(?:", "(?>", "(?P<k>", "(?i:", "(?-i:", "(?=", "(?!", "(?s:", "(?m:", "(?a:"}[r.IntN(11)]
			if open == "(?P<k>" && strings.Contains(inner, "(?P<k>") {
				open = "("
			}
			atom = open + inner + ")"
		} else {
			atom = atoms[r.IntN(len(atoms))]
		}
		b.WriteString(atom)
		q := quantifiers[r.IntN(len(quantifiers))]
		if strings.HasSuffix(atom, ")") && r.IntN(2) == 0 {
			q = ""
		}
		if atom == "^" || atom == "$" || atom == `\b` || atom == `\B` || atom == `\Z` || atom == `\A` {
			q = ""
		}
		b.WriteString(q)
	}
	return b.String()
}

func TestAgainstPythonValid(t *testing.T) {
	r := rand.New(rand.NewPCG(3, 4))
	var cases []pyCase
	for range 2500 {
		c := pyCase{P: validPattern(r, 0), F: flagChoices[r.IntN(len(flagChoices)-1)]}
		for range 8 {
			c.S = append(c.S, randomSubject(r))
		}
		cases = append(cases, c)
	}
	compare(t, "valid", cases)
}

const globOracle = `
import fnmatch, json, sys
out = []
for g in json.load(sys.stdin):
    try:
        out.append(fnmatch.translate(g).replace("/", r"[\\/]"))
    except Exception as e:
        out.append("!" + type(e).__name__)
json.dump(out, sys.stdout)
`

func TestTranslateGlobAgainstPython(t *testing.T) {
	r := rand.New(rand.NewPCG(5, 6))
	chars := []rune("*?[]!-^\\/a.bz &~|é")
	globs := []string{"*", "*git push*", "*/.ccnavi/*", "*.pem", "[a/b]", "[]", "[!]", "[z-a]", "[a-c-e]"}
	for range 3000 {
		var b strings.Builder
		for range r.IntN(10) {
			b.WriteRune(chars[r.IntN(len(chars))])
		}
		globs = append(globs, b.String())
	}
	want := answers(t, "glob", func(py string) []string {
		in, _ := json.Marshal(globs)
		cmd := exec.Command(py, "-I", "-c", globOracle)
		cmd.Stdin = bytes.NewReader(in)
		out, err := cmd.Output()
		if err != nil {
			t.Fatal(err)
		}
		var want []string
		if err := json.Unmarshal(out, &want); err != nil {
			t.Fatal(err)
		}
		return want
	})
	for i, g := range globs {
		if strings.HasPrefix(want[i], "!") {
			t.Logf("Python が翻訳できない glob %q: %s", g, want[i])
			continue
		}
		if got := TranslateGlob(g); got != want[i] {
			t.Errorf("TranslateGlob(%q) = %q, want %q", g, got, want[i])
		}
	}
}

const windowOracle = `
import json, re, sys
def span(m):
    return None if m is None else [m.start(), m.end()]
out = []
for c in json.load(sys.stdin):
    p = re.compile(c["p"], c["f"])
    s, pos, end = c["s"], c["pos"], c["end"]
    out.append({
        "search": span(p.search(s, pos, end)), "match": span(p.match(s, pos, end)),
        "all": [span(m) for m in p.finditer(s, pos, end)], "sub": p.sub(lambda m: "<" + m.group(0) + ">", s),
    })
json.dump(out, sys.stdout)
`

type windowCase struct {
	P   string `json:"p"`
	F   int    `json:"f"`
	S   string `json:"s"`
	Pos int    `json:"pos"`
	End int    `json:"end"`
}

type windowResult struct {
	Search []int   `json:"search"`
	Match  []int   `json:"match"`
	All    [][]int `json:"all"`
	Sub    string  `json:"sub"`
}

// pos・endpos は文字列を切るのとは違う: 後読み・\b は pos の手前を見て、^・\A は pos に当たらず、$ は endpos に当たる。
var windowCases = []windowCase{
	{`(?<=b)a`, 0, "baba", 1, 4},
	{`^a`, 0, "ba", 1, 2},
	{`^a`, int(Multiline), "b\na", 2, 3},
	{`\Aa`, 0, "ba", 1, 2},
	{`a$`, 0, "abc", 0, 1},
	{`\ba`, 0, "xa a", 1, 4},
	{`x*`, 0, "axxb", 0, 4},
	{``, 0, "abc", 1, 2},
	{`a`, 0, "abc", 2, 1},
	{`a`, 0, "aaa", -5, 99},
	{`日.`, 0, "日本日本", 1, 4},
	{`(?i)prune`, 0, "x PRUNE y prune", 3, 15},
	{`(?=c)`, 0, "abc", 0, 2},
}

func spanOf(f Found, ok bool) []int {
	if !ok {
		return nil
	}
	return []int{f.Start, f.End}
}

func TestWindowMatchesPython(t *testing.T) {
	want := answers(t, "window", func(py string) []windowResult {
		in, _ := json.Marshal(windowCases)
		cmd := exec.Command(py, "-I", "-c", windowOracle)
		cmd.Stdin = bytes.NewReader(in)
		out, err := cmd.Output()
		if err != nil {
			t.Fatal(err)
		}
		var res []windowResult
		if err := json.Unmarshal(out, &res); err != nil {
			t.Fatal(err)
		}
		return res
	})
	for i, c := range windowCases {
		re, err := Compile(c.P, Flag(c.F))
		if err != nil {
			t.Fatal(err)
		}
		all := [][]int{}
		for _, f := range re.FindAllIn(c.S, c.Pos, c.End) {
			all = append(all, []int{f.Start, f.End})
		}
		got := windowResult{
			Search: spanOf(re.FindIn(c.S, c.Pos, c.End)), Match: spanOf(re.MatchIn(c.S, c.Pos, c.End)), All: all,
			Sub: re.Sub(c.S, func(f Found) string { return "<" + f.Text() + ">" }),
		}
		gj, _ := json.Marshal(got)
		wj, _ := json.Marshal(want[i])
		if !bytes.Equal(gj, wj) {
			t.Errorf("%+v\n got  %s\n want %s", c, gj, wj)
		}
	}
}
