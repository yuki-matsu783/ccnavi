package useronly

import (
	"bytes"
	"encoding/json"
	"math/rand/v2"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/shell"

	"ccnavi/internal/tickets/testkit"
)

// Python の user_only・phase_forms と突き合わせる。表から組む式の文字列が 1 字も違わないこと、同じコマンドに
// 同じ答え（見つけた種類と文字列・止めた文）を返すことを、決めた入力と乱数の入力で見る。シェルの読み
// （shellread.read の text・層）は Python の答えをそのまま当てる（突き合わせは infra/shell が持つ）。
// リポジトリの .venv の Python が無ければ testdata の写しと比べる。

// answers は testkit.Answers に、文面ファイルを読むリポジトリのルートを添えて渡す（Python の側は c["root"] と
// c["in"] で読む）。name が空なら写しを持たない。
func answers[T any](t *testing.T, name, script string, in any) T {
	t.Helper()
	return testkit.Answers[T](t, name, script, map[string]any{"root": testkit.RepoRoot(t), "in": in})
}

// 文は root の文面ファイルから引くので、写しにはリポジトリの外の答え（文の中身）を入れない。文の突き合わせは
// 写しを持たない回（name が空）だけで見る。

const formsOracle = `
import json, sys
from ccnavi.tickets.model import user_only as u, phase_forms as p
json.dump({
    "cli": u.cli_forms(p._NOT_PREVIEW),
    "script": u.script_forms(),
    "hold": u.hold_exempt_forms(),
    "forbidden": u.subagent_forbidden_forms(),
    "entry": u.user_entry_forms(excluding="prune"),
    "verb": u.user_verb_forms("prune"),
    "userScript": u.user_script_pattern().pattern,
    "board": [[rx.pattern for rx in combo] for combo in u.board_combos()],
    "rules": {b: p.ticket_approval_rule(b, "/w").regex for b in json.load(sys.stdin)["in"]},
}, sys.stdout, ensure_ascii=False)
`

type formsAnswer struct {
	CLI, Script, Hold, Forbidden, Entry, Verb, UserScript string
	Board                                                 [][]string
	Rules                                                 map[string]string
}

var bins = []string{"", "/opt/x/bin/ccnavi", `C:\tools\ccnavi.exe`, "/w/.ccnavi/bin/my-ccnavi"}

func TestFormsMatchPython(t *testing.T) {
	want := answers[formsAnswer](t, "forms", formsOracle, bins)
	var board [][]string
	for _, combo := range boardRx {
		var row []string
		for _, x := range combo {
			row = append(row, x.re.Pattern())
		}
		board = append(board, row)
	}
	got := formsAnswer{
		CLI: cliForms(notPreview), Script: scriptForms(), Hold: holdExemptForms(), Forbidden: subagentForbiddenForms(),
		Entry: userEntryForms(pruneVerb), Verb: userVerbForms(pruneVerb), UserScript: userScriptPattern(), Board: board,
		Rules: map[string]string{},
	}
	for _, b := range bins {
		rule, err := TicketApprovalRule(b, "/w")
		if err != nil {
			t.Fatal(err)
		}
		got.Rules[b] = rule.Regex
	}
	gj, _ := json.Marshal(got)
	wj, _ := json.Marshal(want)
	if !bytes.Equal(gj, wj) {
		at := 0
		for at < min(len(gj), len(wj)) && gj[at] == wj[at] {
			at++
		}
		t.Errorf("式が違う（%d バイト目から）\n got  …%.200s\n want …%.200s", at, gj[at:], wj[at:])
	}
}

const judgeOracle = `
import json, sys
from ccnavi.infra import shellread
from ccnavi.tickets.model import phase_forms as p
STOP = "!StopIteration"
data = json.load(sys.stdin)
root, messages = data["root"], data["in"]["messages"]
rules = {b: p.ticket_approval_rule(b, root) for b in data["in"]["bins"]}
out = []
for c in data["in"]["commands"]:
    r = shellread.read(c)
    inner = []
    if r.unwrapped:
        inner = [[a, b] for a, b in zip(r.runners, r.unwrapped.split(shellread.SEP))]
    inner += [[shellread.MOVED, x] for x in r.moved.split(shellread.SEP) if x]
    subject = c if r.degraded else r.text
    reason = r.reason if r.degraded else ""
    unwrapped = shellread.SEP.join(x for _, x in inner)
    row = {"subject": subject, "reason": reason, "inner": inner,
           "exempt": p.exempt(subject, reason), "forbidden": p.forbidden(subject, unwrapped),
           "human": list(p.human_path_form(c)), "hidden": {}, "prune": {}, "userPrune": {}, "rule": {}}
    for b in data["in"]["bins"]:
        try:
            row["hidden"][b] = list(p.hidden_user_entry(c, [tuple(x) for x in inner], b))
        except StopIteration:
            row["hidden"][b] = [STOP, ""]
        row["prune"][b] = p.prune_form(c, b)
        row["userPrune"][b] = p.user_prune_form(c, b)
        row["rule"][b] = rules[b].compiled.search(subject) is not None
    if messages:
        kind, found = row["human"]
        row["humanText"] = "" if not kind else (
            p.guard_off_message(root, found) if kind == "guard-off"
            else p.user_script_message(root, found) if kind == "user-script"
            else p.expanded_option_message(root, found) if kind == "expanded"
            else p.board_form_message(root, found))
        entry, found = row["hidden"][""]
        row["hiddenText"] = p.hidden_entry_message(root, entry, found) if entry and entry != STOP else entry
    out.append(row)
json.dump(out, sys.stdout, ensure_ascii=False)
`

// pythonStop は Python の hidden_user_entry が StopIteration で落ちる形の印（judgeOracle の STOP）。
const pythonStop = "!StopIteration"

type judgeIn struct {
	Commands []string `json:"commands"`
	Bins     []string `json:"bins"`
	Messages bool     `json:"messages"`
}

type judgeRow struct {
	Subject    string               `json:"subject"`
	Reason     string               `json:"reason"`
	Inner      [][2]string          `json:"inner"`
	Exempt     bool                 `json:"exempt"`
	Forbidden  bool                 `json:"forbidden"`
	Human      [2]string            `json:"human"`
	Hidden     map[string][2]string `json:"hidden"`
	Prune      map[string]string    `json:"prune"`
	UserPrune  map[string]string    `json:"userPrune"`
	Rule       map[string]bool      `json:"rule"`
	HumanText  string               `json:"humanText"`
	HiddenText string               `json:"hiddenText"`
}

func compareJudge(t *testing.T, name string, in judgeIn) {
	t.Helper()
	want := answers[[]judgeRow](t, name, judgeOracle, in)
	root := testkit.RepoRoot(t)
	ruleFor := map[string]func(string) bool{}
	for _, b := range in.Bins {
		rule, err := TicketApprovalRule(b, root)
		if err != nil {
			t.Fatal(err)
		}
		ruleFor[b] = rule.Compiled.Search
	}
	bad := 0
	report := func(c string, what string, got, want any) {
		bad++
		if bad <= 30 {
			t.Errorf("%q %s: got %v want %v", c, what, got, want)
		}
	}
	for i, c := range in.Commands {
		w := want[i]
		var inner []Layer
		var layers []string
		for _, l := range w.Inner {
			inner = append(inner, Layer{Runner: l[0], Text: l[1]})
			layers = append(layers, l[1])
		}
		if got := Exempt(w.Subject, w.Reason); got != w.Exempt {
			report(c, "Exempt", got, w.Exempt)
		}
		if got := Forbidden(w.Subject, strings.Join(layers, shell.Sep)); got != w.Forbidden {
			report(c, "Forbidden", got, w.Forbidden)
		}
		kind, found := HumanPathForm(c)
		if got := [2]string{string(kind), found}; got != w.Human {
			report(c, "HumanPathForm", got, w.Human)
		}
		for _, b := range in.Bins {
			entry, word := HiddenUserEntry(c, inner, b)
			// Python が例外で止める形（pythonStop）は、Go が止める側に答えれば合う。
			if got := [2]string{entry, word}; got != w.Hidden[b] && !(w.Hidden[b][0] == pythonStop && entry != "") {
				report(c, "HiddenUserEntry "+b, got, w.Hidden[b])
			}
			if got := PruneForm(c, b); got != w.Prune[b] {
				report(c, "PruneForm "+b, got, w.Prune[b])
			}
			if got := UserPruneForm(c, b); got != w.UserPrune[b] {
				report(c, "UserPruneForm "+b, got, w.UserPrune[b])
			}
			if got := ruleFor[b](w.Subject); got != w.Rule[b] {
				report(c, "TicketApprovalRule "+b, got, w.Rule[b])
			}
		}
		if in.Messages {
			text := ""
			if kind != HumanNone {
				text = HumanMessage(root, kind, found)
			}
			if text != w.HumanText {
				report(c, "HumanMessage", text, w.HumanText)
			}
			text = ""
			if entry, word := HiddenUserEntry(c, inner, ""); entry != "" {
				text = HiddenEntryMessage(root, entry, word)
			}
			if text != w.HiddenText && !(w.HiddenText == pythonStop && text != "") {
				report(c, "HiddenEntryMessage", text, w.HiddenText)
			}
		}
	}
	if bad > 0 {
		t.Errorf("%d 件が Python と違う", bad)
	}
}

func TestJudgeMatchesPythonOnTheTables(t *testing.T) {
	compareJudge(t, "judge", judgeIn{Commands: seedCommands, Bins: bins})
}

func TestJudgeMatchesPythonOnRandomCommands(t *testing.T) {
	rnd := rand.New(rand.NewPCG(5, 17))
	compareJudge(t, "random", judgeIn{Commands: slices.Concat(randomCommands(rnd, 300), mutatedCommands(rnd, 200)), Bins: bins})
}

// 写しを持たない回。乱数の種を変え、文も突き合わせる。
func TestJudgeMatchesPythonFresh(t *testing.T) {
	rnd := rand.New(rand.NewPCG(uint64(time.Now().UnixNano()), 3))
	commands := slices.Concat(seedCommands, randomCommands(rnd, 1500), mutatedCommands(rnd, 1500))
	compareJudge(t, "", judgeIn{Commands: commands, Bins: bins[:2], Messages: true})
}

// 乱数のコマンドの部品。止める形の語と、それを崩す引用・展開・区切りを混ぜる。
var (
	heads = []string{
		"ccnavi", "CCNAVI.EXE", ".ccnavi/bin/ccnavi", "sh .ccnavi/scripts/ccnavi-launcher.sh", "/opt/x/bin/ccnavi",
		"python3 -m ccnavi", "uv run python -m ccnavi", "py -3 -m ccnavi", "python3 main.py", "/tmp/x", "my-ccnavi",
		"sh .ccnavi/scripts/ccnavi-review.sh", "bash -x .ccnavi/scripts/ccnavi-ticket.sh", "sh .ccnavi/scripts/ccnavi-git.sh",
		"sh .ccnavi/scripts/ccnavi-user-agree.sh", "ccnavi-user-decide.sh", "env", "xargs", "sh -c", "cat", "grep -n",
		"echo", "git", "git commit -m", "sudo -u me", "cd /w", "head -n 3", "tail -3", "bash", "sh -s", "exec", ". ",
	}
	words = []string{
		"user", "USER", "agree", "reviewed", "3", "handoff", "mirror", "prune", "close-early", "--agree", "--yes", "--y$X",
		"--y${X}es", "--preview", "--prune", "--reviewed", "--handoff", "--mirror", "--via", "board", "--via=board",
		"--choices", "--digest", "--digest=d", "decide", "--guard-ticket-approval", "--guard-ticket-approval=enable",
		"CCNAVI_GUARD_TICKET_APPROVAL=disable", "$CCNAVI_GUARD_TICKET_APPROVAL", "CCNAVI_GUARD_${X}=1", "--root", "/r",
		"--root=/r", "--entry", "sh", "--cwd", "''", `""`, "--", "ticket", "finish", "review", "ready", "status", "sync",
		"tidy", "ops", "begin", "pending", "worktree", "retire", "branch", "--abandon", "$X", "u${X}ser", "us*r", "?",
		"[a]", "--docs", "--lint", "--json", "--reason=--preview", "x--preview", "'--yes'", `"--y""es"`, `--y\es`, "a.md",
		"i0001", "--keyword", "{}", "$(echo es)", "`echo es`", "<", "/dev/null", "2>&1", ">", "out", "push", "integrate",
		"--help", "fetch", "next-id", "start", "--list-open", "learn", "-m", "'msg --yes'", `"$(cat f)"`, "ſ", "ＵＳＥＲ",
	}
	joins = []string{" ", " ", " ", " ", "  ", "\t", " ; ", " && ", " | ", "\n", " || ", " & "}
)

func randomCommands(rnd *rand.Rand, n int) []string {
	out := make([]string, n)
	for i := range out {
		var b strings.Builder
		for part := rnd.IntN(2) + 1; part > 0; part-- {
			b.WriteString(heads[rnd.IntN(len(heads))])
			for k := rnd.IntN(6); k > 0; k-- {
				b.WriteString(" ")
				w := words[rnd.IntN(len(words))]
				switch rnd.IntN(10) {
				case 0:
					w = "'" + w + "'"
				case 1:
					w = `"` + w + `"`
				}
				b.WriteString(w)
			}
			if part > 1 {
				b.WriteString(joins[rnd.IntN(len(joins))])
			}
		}
		out[i] = b.String()
	}
	return out
}

// mutatedCommands は表のコマンドの語の間に、部品の語か区切りを 1〜2 個差し込んだもの（止める形の境目を揺らす）。
func mutatedCommands(rnd *rand.Rand, n int) []string {
	out := make([]string, n)
	for i := range out {
		parts := strings.Split(seedCommands[rnd.IntN(len(seedCommands))], " ")
		for k := rnd.IntN(2) + 1; k > 0; k-- {
			at := rnd.IntN(len(parts) + 1)
			insert := words[rnd.IntN(len(words))]
			if rnd.IntN(4) == 0 {
				insert = strings.TrimSpace(joins[rnd.IntN(len(joins))])
			}
			parts = slices.Insert(parts, at, insert)
		}
		out[i] = strings.Join(parts, " ")
	}
	return out
}

const argvOracle = `
import json, sys, types
from ccnavi.tickets.model import user_only as u
out = []
for argv, given in json.load(sys.stdin)["in"]:
    row = {}
    try:
        rest, verb = u.rewrite(list(argv))
        row["rewrite"] = [rest, list(verb.words) if verb else None, ""]
    except u.UserEntryError as e:
        row["rewrite"] = [None, None, str(e)]
    try:
        rest, mark = u.take_entry(list(argv))
        row["take"] = [rest, mark, ""]
    except u.UserEntryError as e:
        row["take"] = [None, "", str(e)]
    args = types.SimpleNamespace(**{k[2:].replace("-", "_"): True for k in given})
    row["refused"] = u.refused(args)
    out.append(row)
json.dump(out, sys.stdout, ensure_ascii=False)
`

type argvRow struct {
	Rewrite [3]any `json:"rewrite"`
	Take    [3]any `json:"take"`
	Refused string `json:"refused"`
}

var argvWords = []string{
	"user", "USER", "User", "uſer", "ＵＳＥＲ", "agree", "AGREE", "reviewed", "3", "close-early", "prune", "handoff",
	"hando\ufb00", "mirror", "bogus", "--root", "R", "--root=R", "--cwd", "--cwd=C", "--record-writes", "W", "--record-tree",
	"--entry", "--entry=sh", "--entry=board", "sh", "board", "x", "--", "--agree", "--preview", "--yes", "-v", "--json",
	"--docs", "--keyword", "--title", "ticket", "a.md", "agreé", "ユーザ",
}

var gatedOptions = []string{"--yes", "--agree", "--reviewed", "--close-early", "--prune", "--handoff", "--mirror", "--preview", "--json"}

func TestEntryMatchesPython(t *testing.T) {
	rnd := rand.New(rand.NewPCG(9, 2))
	var cases [][2][]string
	for range 600 {
		argv := []string{}
		for k := rnd.IntN(6); k > 0; k-- {
			argv = append(argv, argvWords[rnd.IntN(len(argvWords))])
		}
		given := []string{}
		for _, o := range gatedOptions {
			if rnd.IntN(4) == 0 {
				given = append(given, o)
			}
		}
		cases = append(cases, [2][]string{argv, given})
	}
	want := answers[[]argvRow](t, "argv", argvOracle, cases)
	for i, c := range cases {
		argv, given := c[0], c[1]
		var rewrite [3]any
		if rest, verb, err := Rewrite(slices.Clone(argv)); err != nil {
			rewrite = [3]any{nil, nil, err.Error()}
		} else {
			var words any
			if verb != nil {
				words = verb.Words
			}
			rewrite = [3]any{rest, words, ""}
		}
		var take [3]any
		if rest, mark, err := TakeEntry(slices.Clone(argv)); err != nil {
			take = [3]any{nil, "", err.Error()}
		} else {
			take = [3]any{rest, mark, ""}
		}
		refused := Refused(func(o string) bool { return slices.Contains(given, o) })
		gj, _ := json.Marshal(argvRow{rewrite, take, refused})
		wj, _ := json.Marshal(want[i])
		if !bytes.Equal(gj, wj) {
			t.Errorf("%q %v:\n got  %s\n want %s", argv, given, gj, wj)
		}
	}
}
