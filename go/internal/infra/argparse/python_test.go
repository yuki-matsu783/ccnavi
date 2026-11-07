package argparse

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"testing"

	"ccnavi/internal/infra/golden"
)

// Python の argparse の答えの写し（testdata）と突き合わせる。

// specArg は add_argument の引数を JSON の形で書いたもの（写しを取ったときに Python にも渡した）。
type specArg struct {
	Flags []string       `json:"flags"`
	Kw    map[string]any `json:"kw"`
}

type spec struct {
	Prog string    `json:"prog"`
	Args []specArg `json:"args"`
}

func a(flags ...string) specArg { return specArg{Flags: flags, Kw: map[string]any{}} }

func (s specArg) with(key string, v any) specArg {
	s.Kw[key] = v
	return s
}

// build は spec から Go のパーサを組む。
func build(s spec) *Parser {
	p := New(s.Prog)
	for _, sa := range s.Args {
		arg := Arg{Flags: sa.Flags, Default: sa.Kw["default"], Const: sa.Kw["const"]}
		switch sa.Kw["action"] {
		case "store_true":
			arg.Kind = StoreTrue
		case "append":
			arg.Kind = Append
		}
		switch n := sa.Kw["nargs"].(type) {
		case string:
			arg.Nargs = map[string]Nargs{"?": Optional, "*": ZeroOrMore}[n]
		case int:
			arg.Nargs = Nargs(n)
		case float64:
			arg.Nargs = Nargs(n)
		}
		if sa.Kw["type"] == "int" {
			arg.Type = Int
		}
		switch m := sa.Kw["metavar"].(type) {
		case string:
			arg.Metavar = []string{m}
		case []string:
			arg.Metavar = m
		}
		if list, ok := arg.Default.([]any); ok && len(list) == 0 {
			arg.Default = []string{}
		}
		p.Add(arg)
	}
	return p
}

// ccnaviSpec は ccnavi の CLI のパーサ（写しを取ったときの Python の entry/cli.py の _run）と同じ並び。
func ccnaviSpec() spec {
	str := func(flag string, def any) specArg { return a(flag).with("default", def) }
	flagOf := func(flag string) specArg { return a(flag).with("action", "store_true") }
	args := []specArg{
		a("--root").with("action", "append").with("default", nil),
		str("--mode", ""), str("--rules", ""), str("--log", nil), str("--state", nil),
		str("--restore-if-deny", ""), str("--guard-core-files", ""), str("--guard-ticket-approval", ""),
		str("--ticket-control", ""), str("--result", ""),
		flagOf("--lint"), flagOf("--agree"), flagOf("--preview"), flagOf("--verify"),
		str("--plan-order", nil).with("metavar", "PARENT"), flagOf("--fill-phases"),
		str("--order", nil).with("metavar", "JSON"), flagOf("--write"), str("--expect", nil).with("metavar", "SHA"),
		a("--yes").with("nargs", "?").with("const", "\x00bare").with("default", ""),
		str("--digest", ""), str("--placed-out", "").with("metavar", "FILE"),
		a("--test").with("nargs", 2).with("metavar", []string{"TOOL", "SUBJECT"}).with("default", nil),
		str("--permission-mode", ""), str("--agent-id", ""),
		str("--test-expectations", "").with("metavar", "FILE"),
		flagOf("--explain"), flagOf("--prune"), flagOf("--suggest"), flagOf("--json"),
		str("--tickets", ""), str("--approved", nil), str("--phases", nil), str("--risk", nil),
		str("--projects", nil), str("--project-home", ""), str("--project-rules-file", ""),
		str("--project-phases-file", ""), str("--project-risk-file", ""), str("--flow", ""),
		flagOf("--docs"),
		a("--type").with("action", "append").with("default", nil),
		a("--tag").with("action", "append").with("default", nil),
		a("--keyword").with("action", "append").with("default", nil),
		a("--path").with("action", "append").with("default", nil),
		a("--text").with("action", "append").with("default", nil),
		str("--since", nil), str("--until", nil), str("--sort", nil),
		a("-r", "--reverse").with("action", "store_true"),
		str("--limit", nil).with("type", "int"), str("--format", nil), flagOf("--no-refresh"),
		flagOf("--learnings"), flagOf("--templates"), str("--show", nil).with("metavar", "NAME"), str("--layer", ""),
		str("--check-file", nil).with("metavar", "PATH"), str("--draft", nil).with("metavar", "PARENT"),
		str("--name", ""), str("--to", ""), flagOf("--handoff"), flagOf("--mirror"),
		a("command").with("nargs", "*"),
		a("--cwd").with("action", "append").with("default", nil),
		str("--phase", nil).with("type", "int"), flagOf("--check"),
		str("--abandon", nil).with("metavar", "PARENT"), str("--body-file", ""), str("--reason", ""),
		str("--title", ""), str("--plan", ""), str("--issue", nil), str("--mr", nil).with("type", "int"),
		a("--scope").with("action", "append").with("default", []string{}),
		str("--template", ""), flagOf("--with-feedback"), flagOf("--feedback"),
		str("--note", ""), str("--target", ""), str("--signal", ""),
		str("--reviewed", nil).with("type", "int"), flagOf("--accept-unresolved"), flagOf("--close-early"),
		a("-h", "--help").with("action", "store_true"), flagOf("--all"), flagOf("--version"),
		str("--record-writes", ""), str("--record-tree", ""), str("--choose-out", ""),
		str("--accept-plan", ""), str("--accept-done", ""), str("--accept-check", ""), flagOf("--no-issue"),
		str("--actor", ""), str("--via", ""),
	}
	return spec{Prog: "ccnavi", Args: args}
}

// 使う範囲の残りの組み合わせ（必須の位置引数、`?` と整数の nargs の位置引数、値を取る短いオプション、
// 文字列の既定値を型に通す形、長い prog の折り返し）を見るための小さなパーサ。
func smallSpecs() map[string]spec {
	return map[string]spec{
		"short": {Prog: "prog", Args: []specArg{
			a("-n", "--num").with("type", "int"),
			a("-v").with("action", "store_true"),
			a("-q", "--quiet").with("action", "store_true"),
			a("-o").with("type", "int").with("default", "5"),
			a("--pair").with("nargs", 2),
			a("--opt").with("nargs", "?").with("const", "C").with("default", "D"),
			a("--many").with("nargs", "*"),
			a("src"),
			a("dst").with("nargs", "?").with("default", "def"),
		}},
		"pos": {Prog: "a-program-with-a-rather-long-name-that-takes-up-space", Args: []specArg{
			a("--alpha").with("metavar", "A"),
			a("--beta").with("action", "append"),
			a("pair").with("nargs", 2).with("metavar", []string{"X", "Y"}),
			a("tail").with("nargs", "*").with("metavar", []string{"T", "U"}),
		}},
	}
}

type pyCase struct {
	Spec    string   `json:"spec"`
	Argv    []string `json:"argv"`
	Columns *string  `json:"columns"`
	Usage   bool     `json:"usage,omitempty"`
}

type pyResult struct {
	NS    map[string]any `json:"ns,omitempty"`
	Code  int            `json:"code,omitempty"`
	Err   string         `json:"err,omitempty"`
	Usage string         `json:"usage,omitempty"`
}

func goResult(parsers map[string]*Parser, c pyCase) pyResult {
	p := parsers[c.Spec]
	lookup := func(string) (string, bool) {
		if c.Columns == nil {
			return "", false
		}
		return *c.Columns, true
	}
	width := columns(lookup, func() (int, bool) { return 0, false }) - columnsMargin
	if c.Usage {
		return pyResult{Usage: p.formatUsage(width)}
	}
	ns, err := p.Parse(c.Argv)
	if err != nil {
		return pyResult{Code: pythonErrorExit, Err: p.errorText(err.Error(), width)}
	}
	// JSON に通して、Python の答えと同じ形（数は float64）にそろえる。
	data, _ := json.Marshal(ns)
	var back map[string]any
	_ = json.Unmarshal(data, &back)
	return pyResult{NS: back}
}

func compare(t *testing.T, name string, specs map[string]spec, cases []pyCase) {
	t.Helper()
	want := golden.Load[[]pyResult](t, name)
	if len(want) != len(cases) {
		t.Fatalf("%s の件数が違う（-update で取り直す）", name)
	}
	parsers := map[string]*Parser{}
	for k, s := range specs {
		parsers[k] = build(s)
	}
	bad, errs := 0, 0
	for i, c := range cases {
		got := goResult(parsers, c)
		gj, _ := json.Marshal(got)
		wj, _ := json.Marshal(want[i])
		if want[i].Err != "" {
			errs++
		}
		if !bytes.Equal(gj, wj) {
			bad++
			if bad <= 10 {
				t.Errorf("%s %q COLUMNS=%v\n got  %s\n want %s", c.Spec, c.Argv, strOrNil(c.Columns), gj, wj)
			}
		}
	}
	t.Logf("%d 件を突き合わせた（誤り %d）", len(cases), errs)
	if bad > 0 {
		t.Errorf("%d / %d 件が Python と違う", bad, len(cases))
	}
}

// pythonErrorExit は parser.error の終了コード。ccnavi の入口は SystemExit を受けて自分の終了コードにする。
const pythonErrorExit = 2

func strOrNil(s *string) any {
	if s == nil {
		return nil
	}
	return *s
}

func ptr(s string) *string { return &s }

// 入口が実際に受ける形。
var ccnaviArgv = [][]string{
	{},
	{"--root", "/w", "ticket", "new", "T1", "--title", "x"},
	{"--root", "/a", "--root", "/b", "ticket", "status"},
	{"--root=/a", "--cwd", "/a/b", "review", "confirm", "--actor", "bob"},
	{"--test", "Bash", "ls"},
	{"--test", "Bash"},
	{"--test=Bash"},
	{"--test", "Bash", "--json"},
	{"--yes"}, {"--yes", "a,b"}, {"--yes="}, {"--yes", "--json"}, {"--yes=--json"},
	{"--docs", "--type", "guide", "--type", "x", "--limit", "3", "-r"},
	{"--limit", "x"}, {"--limit", " 7 "}, {"--limit", "٣"}, {"--limit", "1_0"}, {"--limit", "-3"}, {"--limit=-3"},
	{"--limit", "1.5"}, {"--limit", ""}, {"--phase", "+2"}, {"--reviewed", "0"}, {"--mr"},
	{"-r"}, {"-rh"}, {"-hr"}, {"-rx"}, {"-r=1"}, {"-rr"}, {"-rh=x"}, {"-r-h"}, {"-ré"},
	{"--reverse=1"}, {"--lint=x"}, {"--lint="}, {"-"}, {"--"}, {"--", "--lint"}, {"a", "--", "b", "--", "c"},
	{"ticket", "--lint", "x"}, {"--lint", "ticket", "--json"}, {"--unknown"}, {"--unknown=1"}, {"-5"}, {"-1.5"},
	{"-x y"}, {"--mode"}, {"--mode", "--lint"}, {"--mode=--lint"}, {"--mode", "-5"}, {"--mode", "a b"},
	{"--mode", "-x y"}, {"--scope", "a", "--scope", "b"}, {"--phase", "2"}, {"--cwd", "a", "--cwd", "b"},
	{"--help", "docs"}, {"-h", "--all"}, {"--reason", "--root"}, {"--reason=--root"}, {"--close"}, {"--review"},
	{"--agree", "--yes", "T1,T2", "--digest", "abc"}, {"--plan-order", "P", "--order", "[[1]]", "--write"},
	{"user", "handoff", "--yes", "--expect", "h", "--json", "--via", "board"},
	{"-z"}, {"-zr"}, {"--test", "a", "b", "c"}, {"x", "--root"}, {"--root", "--", "x"},
}

func TestCcnaviParserMatchesPython(t *testing.T) {
	specs := map[string]spec{"ccnavi": ccnaviSpec()}
	var cases []pyCase
	for _, argv := range ccnaviArgv {
		cases = append(cases, pyCase{Spec: "ccnavi", Argv: argv, Columns: ptr("80")})
	}
	for _, cols := range []*string{nil, ptr(""), ptr("1"), ptr("20"), ptr("40"), ptr("79"), ptr("120"), ptr("100000"),
		ptr("abc"), ptr("0"), ptr("-5"), ptr(" 100 "), ptr("1_00"), ptr("٨٠"), ptr("10.5")} {
		cases = append(cases, pyCase{Spec: "ccnavi", Usage: true, Columns: cols})
		cases = append(cases, pyCase{Spec: "ccnavi", Argv: []string{"--bad"}, Columns: cols})
	}
	compare(t, "ccnavi", specs, cases)
}

func TestRandomArgvMatchesPython(t *testing.T) {
	specs := smallSpecs()
	specs["ccnavi"] = ccnaviSpec()
	words := []string{"ticket", "new", "a", "1", "-1", ".5", "-.5", "-1.5", "--", "-", "", "a b", "-x y", "x\n",
		"-1\n", "١٢", " 3 ", "1_0", "-é", "é", "--unknown", "--unknown=v", "-z", "-zz", "=", "-=", "--=x"}
	pool := map[string][]string{}
	for name, s := range specs {
		var opts []string
		for _, sa := range s.Args {
			for _, f := range sa.Flags {
				if f[0] != '-' {
					continue
				}
				opts = append(opts, f, f+"=v", f+"=")
				if f[1] != '-' {
					opts = append(opts, f+"v", f+"1", f+"-q", f+"=1")
				}
			}
		}
		for i := range opts {
			for _, f := range opts[:i%7] {
				if len(f) == 2 && i%3 == 0 {
					opts = append(opts, f+opts[i][1:])
				}
			}
		}
		pool[name] = append(opts, words...)
	}
	for i := range pool["ccnavi"] {
		if i%4 == 0 {
			pool["ccnavi"] = append(pool["ccnavi"], "-rh", "-hr", "-rz", "--test")
		}
	}
	r := rand.New(rand.NewPCG(7, 11))
	columnsChoices := []*string{ptr("80"), ptr("40"), ptr("120"), ptr("15"), nil}
	names := []string{"ccnavi", "short", "pos"}
	var cases []pyCase
	for i := range 4000 {
		name := names[i%len(names)]
		words := pool[name]
		argv := []string{}
		for range r.IntN(7) {
			argv = append(argv, words[r.IntN(len(words))])
		}
		cases = append(cases, pyCase{Spec: name, Argv: argv, Columns: columnsChoices[r.IntN(len(columnsChoices))]})
	}
	for _, name := range names {
		for _, cols := range []string{"10", "30", "60", "200"} {
			cases = append(cases, pyCase{Spec: name, Usage: true, Columns: ptr(cols)})
		}
	}
	compare(t, "random", specs, cases)
}

// Python の int は桁に上限が無いが、Go は int に収まらない値を読めない値として断る。
func TestIntOutOfRange(t *testing.T) {
	p := New("ccnavi")
	p.Add(Arg{Flags: []string{"--limit"}, Type: Int})
	_, err := p.Parse([]string{"--limit", "99999999999999999999"})
	want := "argument --limit: invalid int value: '99999999999999999999'"
	if err == nil || err.Error() != want {
		t.Errorf("got %v, want %s", err, want)
	}
}

func TestNamespaceGetters(t *testing.T) {
	p := build(ccnaviSpec())
	ns, err := p.Parse([]string{"--root", "/w", "--limit", "3", "--scope", "x", "--json", "ticket", "new"})
	if err != nil {
		t.Fatal(err)
	}
	if got := ns.Strings("root"); fmt.Sprint(got) != "[/w]" {
		t.Errorf("root = %v", got)
	}
	if n, ok := ns.Int("limit"); !ok || n != 3 {
		t.Errorf("limit = %v %v", n, ok)
	}
	if _, ok := ns.Int("phase"); ok || !ns.IsNone("phase") {
		t.Error("phase は None")
	}
	if !ns.Truthy("json") || ns.Truthy("lint") || ns.Str("mode") != "" || ns.Strings("cwd") != nil {
		t.Error("真偽か文字列の取り出しが違う")
	}
	if got := ns.Strings("command"); fmt.Sprint(got) != "[ticket new]" {
		t.Errorf("command = %v", got)
	}
	var changed []string
	for _, act := range p.Actions() {
		if len(act.OptionStrings) > 0 && ns.Changed(act) {
			changed = append(changed, act.OptionStrings[len(act.OptionStrings)-1])
		}
	}
	if fmt.Sprint(changed) != "[--root --json --limit --scope]" {
		t.Errorf("changed = %v", changed)
	}
	if !p.HasOption("-r") || p.HasOption("--rev") {
		t.Error("HasOption が名前そのものだけを見ていない")
	}
}

func TestChangedComparesLikePython(t *testing.T) {
	p := New("ccnavi")
	reviewed := p.Add(Arg{Flags: []string{"--reviewed"}, Type: Int})
	scope := p.Add(Arg{Flags: []string{"--scope"}, Kind: Append, Default: []string{}})
	yes := p.Add(Arg{Flags: []string{"--yes"}, Nargs: Optional, Const: "\x00bare", Default: ""})
	for _, c := range []struct {
		argv []string
		act  *Action
		want bool
	}{
		{[]string{"--reviewed", "0"}, reviewed, true}, // 0 は None と違う
		{[]string{}, scope, false},
		{[]string{"--yes="}, yes, false}, // 空の値は既定と同じ
		{[]string{"--yes"}, yes, true},
	} {
		ns, err := p.Parse(c.argv)
		if err != nil {
			t.Fatal(err)
		}
		if got := ns.Changed(c.act); got != c.want {
			t.Errorf("%q: Changed(%s) = %v", c.argv, c.act.Dest, got)
		}
	}
}
