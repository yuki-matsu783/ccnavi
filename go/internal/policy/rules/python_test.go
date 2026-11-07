package rules

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"math/rand/v2"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/yaml"
)

// Python の rules と突き合わせる。リポジトリの .venv の Python が無ければ飛ばす。

func repoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, ".ccnavi")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Skip("リポジトリが見つからない")
		}
		dir = parent
	}
}

func venvPython(root string) string {
	for _, rel := range []string{".venv/bin/python", ".venv/Scripts/python.exe"} {
		p := filepath.Join(root, rel)
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return ""
}

// Python の答えは testdata に写して持つ。Python があれば聞き直して写しと比べ（食い違えば -update で取り直す）、
// 無ければ写しを読む。Python を外したあとも、同じ答えを確かめ続けるため。
var update = flag.Bool("update", false, "testdata の Python の答えを取り直す")

// answers は Python の答えを返す。name が空なら写しを持たない（Python が無ければ飛ばす）。
func answers[T any](t *testing.T, name, script string, in any) T {
	t.Helper()
	var saved T
	path := filepath.Join("testdata", name+".json")
	data, readErr := os.ReadFile(path)
	if name != "" && readErr == nil {
		if err := json.Unmarshal(data, &saved); err != nil {
			t.Fatal(err)
		}
	}
	root := repoRoot(t)
	if venvPython(root) == "" {
		if name == "" || readErr != nil {
			t.Skip("Python も testdata の写しも無い")
		}
		return saved
	}
	var fresh T
	runPython(t, script, in, &fresh)
	if name == "" {
		return fresh
	}
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

// runPython は src を読み込み先に足して、標準入力に JSON を渡したスクリプトの JSON の答えを読む。
func runPython(t *testing.T, script string, in any, out any) {
	t.Helper()
	root := repoRoot(t)
	py := venvPython(root)
	data, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(py, "-c", script)
	cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(root, "src"), "PYTHONDONTWRITEBYTECODE=1")
	cmd.Stdin = bytes.NewReader(data)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	got, err := cmd.Output()
	if err != nil {
		t.Fatalf("python: %v\n%s", err, stderr.String())
	}
	if err := json.Unmarshal(got, out); err != nil {
		t.Fatalf("%v\n%s", err, got)
	}
}

const rulesOracle = `
import json, sys
from ccnavi.infra import yamlread
from ccnavi.policy import rules
out = []
for case in json.load(sys.stdin):
    data = yamlread.safe_load(case["yaml"])
    rs, problems = rules.parse(data, case["root"])
    listed = []
    for r in rs.all():
        listed.append({
            "id": r.id, "decision": r.decision, "every": r.every,
            "pattern": r.compiled.pattern if r.compiled else "",
            "excepts": [e.compiled.pattern if e.compiled else "" for e in r.excepts],
            "when": r.when.describe() if r.when else "",
            "body": r.written_body(),
            "spoken": r.spoken_message(),
        })
    hits = []
    for tool, subject in case["calls"]:
        hits.append([r.id for r in rs.all() if r.matches(tool, subject)])
    out.append({"problems": [str(p) for p in problems], "version": rs.version == rules.VERSION, "rules": listed, "hits": hits})
json.dump(out, sys.stdout, ensure_ascii=False)
`

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
		v := ruleView{ID: r.ID, Decision: string(r.Decision), Every: r.Every, Excepts: []string{}, Body: r.WrittenBody(), Spoken: r.SpokenMessage()}
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
	want := answers[[]ruleResult](t, name, rulesOracle, cases)
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

func TestSharedRulesAgainstPython(t *testing.T) {
	repo := repoRoot(t)
	text, err := os.ReadFile(filepath.Join(repo, ".ccnavi", "config", "common", "rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	expect, err := os.ReadFile(filepath.Join(repo, ".ccnavi", "config", "common", "rule-expectations.yml"))
	if err != nil {
		t.Fatal(err)
	}
	data, err := yaml.Load(string(expect))
	if err != nil {
		t.Fatal(err)
	}
	var calls [][2]string
	for _, section := range []string{"deny", "ask", "allow"} {
		list, _ := data.(*yaml.Map).List(section)
		for _, item := range list {
			m := item.(*yaml.Map)
			calls = append(calls, [2]string{m.Text("tool"), m.Text("subject")})
		}
	}
	r := rand.New(rand.NewPCG(7, 8))
	for range 300 {
		// 期待する判定の対象の綴りを崩したもの（大文字小文字・区切り・前後の字）。
		c := calls[r.IntN(len(calls))]
		s := []rune(c[1])
		if len(s) > 0 {
			i := r.IntN(len(s))
			switch r.IntN(4) {
			case 0:
				s[i] = []rune(strings.ToUpper(string(s[i])))[0]
			case 1:
				s = append(s[:i], s[i+1:]...)
			case 2:
				s = append(s[:i], append([]rune{[]rune("\\/ \x00-.日İ")[r.IntN(8)]}, s[i:]...)...)
			case 3:
				s = append(s, '\n')
			}
		}
		calls = append(calls, [2]string{c[0], string(s)})
	}
	// 共通のルールファイルは書き換わるので写しを持たない（Python があるときだけ見る）。
	compareRules(t, "", []ruleCase{{YAML: string(text), Root: repo, Calls: calls}, {YAML: string(text), Root: "/repo", Calls: calls}})
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
