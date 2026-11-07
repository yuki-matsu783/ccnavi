package rules

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/yaml"
)

// Python の rules の答えを写した見本（testdata）と突き合わせる。

type ruleCase struct {
	YAML  string      `json:"yaml"`
	Root  string      `json:"root"`
	Calls [][2]string `json:"calls"`
}

type ruleView struct {
	ID       string   `json:"id"`
	Decision string   `json:"decision"`
	Every    int      `json:"every"`
	Pattern  string   `json:"pattern"`
	Excepts  []string `json:"excepts"`
	When     string   `json:"when"`
	Body     string   `json:"body"`
	Spoken   string   `json:"spoken"`
}

type ruleResult struct {
	Problems []string   `json:"problems"`
	Version  bool       `json:"version"`
	Rules    []ruleView `json:"rules"`
	Hits     [][]string `json:"hits"`
}

func goRules(c ruleCase) ruleResult {
	data, err := yaml.Load(c.YAML)
	if err != nil {
		return ruleResult{Problems: []string{"yaml: " + err.Error()}}
	}
	m, _ := data.(*yaml.Map)
	rs, problems := Parse(m, c.Root)
	res := ruleResult{Problems: []string{}, Rules: []ruleView{}, Hits: [][]string{}, Version: rs.Version == Version}
	for _, p := range problems {
		res.Problems = append(res.Problems, p.String())
	}
	for _, r := range rs.All() {
		_, spoken := r.Speech()
		v := ruleView{ID: r.ID, Decision: string(r.Decision), Every: r.Every, Excepts: []string{}, Body: r.WrittenBody(), Spoken: spoken}
		if r.Compiled != nil {
			v.Pattern = r.Compiled.Pattern()
		}
		for _, e := range r.Excepts {
			p := ""
			if e.Compiled != nil {
				p = e.Compiled.Pattern()
			}
			v.Excepts = append(v.Excepts, p)
		}
		if r.When != nil {
			v.When = r.When.Describe()
		}
		res.Rules = append(res.Rules, v)
	}
	for _, call := range c.Calls {
		hit := []string{}
		for _, r := range rs.All() {
			if r.Matches(call[0], call[1]) {
				hit = append(hit, r.ID)
			}
		}
		res.Hits = append(res.Hits, hit)
	}
	return res
}

func compareRules(t *testing.T, name string, cases []ruleCase) {
	t.Helper()
	want := golden.Load[[]ruleResult](t, name)
	bad := 0
	for i, c := range cases {
		got := goRules(c)
		gj, _ := json.Marshal(got)
		wj, _ := json.Marshal(want[i])
		if !bytes.Equal(gj, wj) {
			bad++
			if bad <= 10 {
				t.Errorf("case %d root %q\n%s\n got  %s\n want %s", i, c.Root, c.YAML, gj, wj)
			}
		}
	}
	if bad > 0 {
		t.Errorf("%d / %d 件が Python と違う", bad, len(cases))
	}
}

// doc はルール 1 件（JSON の値で書く）を 1 つのタイプに置いたルールファイル（JSON は YAML として読める）。
func doc(section string, rule any) string {
	data, _ := json.Marshal(map[string]any{"version": 1, section: []any{rule}})
	return string(data)
}

func r(kv ...any) map[string]any {
	m := map[string]any{}
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i].(string)] = kv[i+1]
	}
	return m
}

var pathCalls = [][2]string{
	{"Write", "/a/k.crt"}, {"Write", "/a/vault/token"}, {"Write", "/a/vault/README.MD"}, {"Write", "/a/public/token"},
	{"Edit", "/a/.Ccnavi/config/project/rules.yml"}, {"Write", `C:\a\.ccnavi\x`}, {"Read", "/a/k.crt"},
	{"Write", "/a/fixtures/k.crt"}, {"Write", "/a/secrets/x.md"}, {"Write", "/a/x.pem"}, {"Write", "/a/X.PEM.bak"},
	{"Stop", StopSubject}, {"Write", ""}, {"Write", "/tmp/claude-0/-repo/0a1b2c/scratchpad/a.md"},
	{"Write", "/work/a"}, {"Write", `\work\a`}, {"Write", "/other/a"}, {"Write", "/elsewhere/tmp/a"},
}

var commandCalls = [][2]string{
	{"Bash", "git push --force"}, {"Bash", "git push"}, {"Bash", "git fetch --force"}, {"Bash", "legit push"},
	{"Bash", "npm publish"}, {"Bash", "npm publish --dry-run"}, {"Bash", "npm publish; npm publish --dry-run"},
	{"Bash", "GIT PUSH"}, {"Bash", "rm -rf /"}, {"Bash", "echo 日git push"}, {"Bash", "git\npush"}, {"Bash", "a\x00git push"},
	{"PowerShell", "git push"}, {"Bash", "git push\n"},
}

func baseCases(root string) []ruleCase {
	calls := append(append([][2]string{}, pathCalls...), commandCalls...)
	for _, sub := range []string{"", "/a", "/wip/handoff/x", "/scratchpad", "/.claude/worktrees/w/x", "/wip/handoffx", "-fork/x"} {
		calls = append(calls, [2]string{"Write", root + sub}, [2]string{"Write", strings.ToUpper(root) + sub})
	}
	vault := r("id", "vault", "match", "Write|Edit", "message", "キーは書かない",
		"when", r("any", []any{r("glob", "*.crt"), r("all", []any{r("glob", "*/vault/*"), r("not", r("glob", "*.md"))})}))
	rules := []any{
		vault,
		r("id", "x", "match", "Write", "when", r("regex", `\.crt$`)),
		r("id", "x", "match", "Write", "when", r("all", []any{r("glob", "{root}/*")}), "message", "m"),
		r("id", "x", "match", "Write", "when", r("all", []any{r("regex", "^{!root}.*"), r("not", r("glob", "*/tmp/*"))}), "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*"), "glob", "*", "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*"), "regex", "x", "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*"), "glob", "*", "regex", "x", "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*"), "glob", "", "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*"), "glob", 0, "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*"), "regex", nil, "message", "m"),
		r("id", "x", "match", "Write", "message", "m"),
		r("id", "x", "match", "Write", "when", nil, "message", "m"),
		r("id", "x", "match", "Write", "when", "*.crt", "message", "m"),
		r("id", "x", "match", "Write", "when", []any{"*.crt"}, "message", "m"),
		r("id", "x", "match", "Write", "when", r(), "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*", "regex", "x"), "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*", "why", "x"), "message", "m"),
		r("id", "x", "match", "Write", "when", r("none", []any{r("glob", "*")}), "message", "m"),
		r("id", "x", "match", "Write", "when", r("all", []any{r("glob", "*"), r("match", "Write")}), "message", "m"),
		r("id", "x", "match", "Write", "when", r("all", []any{}), "message", "m"),
		r("id", "x", "match", "Write", "when", r("any", "*.crt"), "message", "m"),
		r("id", "x", "match", "Write", "when", r("not", []any{r("glob", "*")}), "message", "m"),
		r("id", "x", "match", "Write", "when", r("not", nil), "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", ""), "message", "m"),
		r("id", "x", "match", "Write", "when", r("regex", 3), "message", "m"),
		r("id", "x", "match", "Write", "when", r("all", []any{r("glob", nil)}), "message", "m"),
		r("id", "x", "match", "Write", "when", r("regex", "a(?=b)"), "message", "m"),
		r("id", "x", "match", "Write", "when", r("all", []any{r("regex", `(a)\1`)}), "message", "m"),
		r("id", "x", "match", "Write", "when", r("not", r("regex", "a(")), "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "{root}/x"), "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "{!root}/x"), "message", "m"),
		r("id", "x", "match", "Write", "when", r("regex", "x{!root}"), "message", "m"),
		r("id", "x", "match", "Write", "when", r("glob", "*"), "message", "m", "except", []any{}),
		r("id", "x", "match", "Write", "when", nested(7), "message", "m"),
		r("id", "x", "match", "Write", "when", nested(8), "message", "m"),
		r("id", "x", "match", "Write", "when", r("any", leaves(63)), "message", "m"),
		r("id", "x", "match", "Write", "when", r("any", leaves(64)), "message", "m"),
		r("id", "push", "match", "Bash", "when", r("all", []any{r("glob", "*git push*"), r("glob", "*--force*")}), "message", "m"),
		func() any {
			v := r()
			for k, x := range vault {
				v[k] = x
			}
			v["except"] = []any{r("glob", "*/fixtures/*")}
			return v
		}(),
		r("id", "x", "match", "Write|Edit", "glob", "*/.ccnavi/*", "message", "m"),
		r("id", "x", "match", "Write", "glob", "*.pem", "except", []any{r("glob", "*")}, "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", "x", "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", nil, "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", []any{"*"}, "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", []any{r("glob", "*", "regex", "x")}, "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", []any{r()}, "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", []any{r("glob", "*", "why", 1)}, "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", []any{r("glob", true)}, "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", []any{r("regex", "(?<=a)b")}, "message", "m"),
		r("id", "x", "match", "Write", "glob", "*", "except", []any{r("regex", "^{!root}")}, "message", "m"),
		r("id", "workspace-root", "match", "Write|Edit|NotebookEdit", "regex", `^{root}[\\/]`, "message", "m",
			"except", []any{r("regex", `^{root}[\\/]wip[\\/]handoff(?:[\\/]|$)`), r("regex", `^{root}[\\/]scratchpad(?:[\\/]|$)`),
				r("regex", `^{root}[\\/]\.claude[\\/]worktrees(?:[\\/]|$)`)}),
		r("id", "publish", "match", "Bash", "glob", "*npm publish*", "except", []any{r("glob", "*--dry-run*")}, "message", "m"),
		r("id", "outside", "match", "Write", "regex", "^{!root}", "message", "外 {root}"),
		r("id", "outside", "match", "Write", "regex", "^{!root}.*", "message", "m"),
		r("id", "outside", "match", "Write", "regex", `^{!root}[\\/]foo`, "message", "m"),
		r("id", "outside", "match", "Write", "regex", "^{!root}?", "message", "m"),
		r("id", "outside", "match", "Write", "regex", "^{!root}^{!root}", "message", "m"),
		r("id", "outside", "match", "Write", "regex", "a^{!root}", "message", "m"),
		r("id", "outside", "match", "Write", "glob", "{!root}", "message", "m"),
		r("id", "push", "match", "Bash", "glob", "*git push*", "message", "`{script:ccnavi-git.sh}` で {root} {script:bad name}"),
		r("id", "push", "match", "Bash", "regex", `\bgit\s+push\b`, "message", "m"),
		r("id", "push", "match", "Bash", "regex", `(?-i:GIT) push$`, "message", "m"),
		r("id", "push", "match", "Bash", "regex", `git push\Z`, "message", "m"),
		r("id", "push", "match", "Bash", "regex", "a{,3}(", "message", "m"),
		r("id", "push", "match", "Bash", "regex", `\p{L}`, "message", "m"),
		r("id", "push", "match", "Bash", "regex", "(?i)x(?i)", "message", "m"),
		r("id", "x", "match", "Bash", "regex", "^(.*:)?process-"),
		r("id", "", "match", "Bash", "glob", "*", "message", "m"),
		r("id", "  ", "match", "Bash", "glob", "*", "message", "m"),
		r("id", "Builtin:x", "match", "Bash", "glob", "*", "message", "m"),
		r("id", "a:b", "match", "Bash", "glob", "*", "message", "m"),
		r("id", "x", "match", "Bash", "glob", "*"),
		r("id", "x", "glob", "*", "message", "m"),
		r("id", "x", "match", "Bash | Write", "glob", "*", "message", "m"),
		r("id", "nudge", "match", "Stop", "glob", "*", "every", 5, "additionalContext", "x {root}"),
		r("id", "nudge", "match", "Stop", "glob", "*", "every", "5"),
		r("id", "nudge", "match", "Stop", "glob", "*", "every", true),
		r("id", "nudge", "match", "Stop", "glob", "*", "every", 0),
		r("id", "nudge", "match", "Stop", "glob", "*", "every", -2),
		r("id", "nudge", "match", "Stop", "glob", "*", "every", 2.5),
		r("id", 12, "match", "Bash", "glob", true, "message", 3),
	}
	var out []ruleCase
	for _, rule := range rules {
		for _, section := range []string{"deny", "ask", "allow"} {
			out = append(out, ruleCase{YAML: doc(section, rule), Root: root, Calls: calls})
		}
	}
	return out
}

func nested(depth int) any {
	var node any = r("glob", "*.crt")
	for range depth {
		node = r("not", node)
	}
	return node
}

func leaves(n int) []any {
	out := make([]any, n)
	for i := range n {
		out[i] = r("glob", fmt.Sprintf("*.%d", i))
	}
	return out
}

// fixedRoot は写しを機械に依らせないための、無い場所のルート（realpath が綴りを変えない）。
const fixedRoot = "/nonexistent/work/ccnavi"

func TestRulesAgainstPython(t *testing.T) {
	if ospath.Windows {
		t.Skip("写しは POSIX のパスで取った")
	}
	root := fixedRoot
	var cases []ruleCase
	cases = append(cases, baseCases(root)...)
	cases = append(cases, baseCases("")...)
	long := "/nonexistent/" + strings.Repeat("a", 300)
	cases = append(cases, baseCases(long)...)
	cases = append(cases, baseCases("/")...)
	for _, text := range []string{
		"version: 2\ndeny: []\n",
		"version: true\ndeny:\n  - {id: x, match: Write, glob: '*', message: m}\n",
		"version: '1'\n",
		"deny: x\nask: [1, null]\n",
		"version: 1\ndeny:\n  - id: on\n    match: Write\n    glob: '*.yml'\n    message: m\n    except:\n      - glob: on\n",
	} {
		cases = append(cases, ruleCase{YAML: text, Root: root, Calls: pathCalls})
	}
	compareRules(t, "rules", cases)
}

func TestRandomRulesAgainstPython(t *testing.T) {
	r := rand.New(rand.NewPCG(9, 10))
	parts := []string{"*", "?", "[a-c]", "[!x]", "/", "git", " ", "push", ".pem", "{root}", "日", "İ", "\\", "-", "[", "]"}
	regexParts := []string{`\b`, `\s+`, `git`, `push`, `(?:a|b)`, `[\\/]`, `$`, `^`, `.*`, `\S*+`, `(?-i:G)`, `{root}`, `\w`, `x?`}
	subjects := []string{"git push", "GIT PUSH", "/a/git/push.pem", "legit push", "a\\b", "日git", "İgit", "x", "", "git  push\n"}
	var cases []ruleCase
	for range 400 {
		var b strings.Builder
		var rule map[string]any
		if r.IntN(2) == 0 {
			for range 1 + r.IntN(5) {
				b.WriteString(parts[r.IntN(len(parts))])
			}
			rule = r2("glob", b.String())
		} else {
			for range 1 + r.IntN(5) {
				b.WriteString(regexParts[r.IntN(len(regexParts))])
			}
			rule = r2("regex", b.String())
		}
		rule["id"], rule["match"], rule["message"] = "x", "Bash|Write", "m"
		var calls [][2]string
		for _, s := range subjects {
			calls = append(calls, [2]string{"Bash", s})
		}
		cases = append(cases, ruleCase{YAML: doc("deny", rule), Root: fixedRoot, Calls: calls})
	}
	compareRules(t, "random", cases)
}

func r2(k string, v any) map[string]any { return map[string]any{k: v} }
