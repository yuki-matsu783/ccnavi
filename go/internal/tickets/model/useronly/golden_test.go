package useronly

import (
	"bytes"
	"encoding/json"
	"math/rand/v2"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/shell"

	"ccnavi/internal/tickets/testkit"
)

// 見本（testdata）と突き合わせる。表から組む式の文字列が 1 字も違わないこと、同じコマンドに同じ答え（見つけた種類と
// 文字列）を返すことを、決めた入力と乱数の入力で見る。シェルの読み（shellread.read の text・層）は見本の答えを
// そのまま当てる（読みの突き合わせは infra/shell が受け持つ）。
// 文は root の文面ファイルから引くので見本の JSON には入れず、Go の答えの見本（golden.Check）で見る。

type formsAnswer struct {
	CLI, Script, Hold, Forbidden, Entry, Verb, UserScript string
	Board                                                 [][]string
	Rules                                                 map[string]string
}

var bins = []string{"", "/opt/x/bin/ccnavi", `C:\tools\ccnavi.exe`, "/w/.ccnavi/bin/my-ccnavi"}

func TestFormsMatchGolden(t *testing.T) {
	want := golden.Load[formsAnswer](t, "forms")
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

// stopIterationMark は見本の中で、HiddenUserEntry の答えが「判定が例外で止まった」ことを表す印。
const stopIterationMark = "!StopIteration"

type judgeIn struct {
	Commands []string `json:"commands"`
	Bins     []string `json:"bins"`
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
	want := golden.Load[[]judgeRow](t, name)
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
			// 見本が例外で止まる形（stopIterationMark）は、Go が止める側に答えれば合う。
			if got := [2]string{entry, word}; got != w.Hidden[b] && !(w.Hidden[b][0] == stopIterationMark && entry != "") {
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
	}
	if bad > 0 {
		t.Errorf("%d 件が写しと違う", bad)
	}
}

func TestJudgeMatchesGoldenOnTheTables(t *testing.T) {
	compareJudge(t, "judge", judgeIn{Commands: seedCommands, Bins: bins})
}

func TestJudgeMatchesGoldenOnRandomCommands(t *testing.T) {
	rnd := rand.New(rand.NewPCG(5, 17))
	compareJudge(t, "random", judgeIn{Commands: slices.Concat(randomCommands(rnd, 300), mutatedCommands(rnd, 200)), Bins: bins})
}

// 止めた文は、表のコマンド（読みは見本の judge のもの）で Go の答えの見本と比べる。
func TestMessagesOnTheTables(t *testing.T) {
	root := testkit.RepoRoot(t)
	rows := golden.Load[[]judgeRow](t, "judge")
	var said [][2]string
	for i, c := range seedCommands {
		var inner []Layer
		for _, l := range rows[i].Inner {
			inner = append(inner, Layer{Runner: l[0], Text: l[1]})
		}
		var human, hidden string
		if kind, found := HumanPathForm(c); kind != humanNone {
			human = HumanMessage(root, kind, found)
		}
		if entry, word := HiddenUserEntry(c, inner, ""); entry != "" {
			hidden = HiddenEntryMessage(root, entry, word)
		}
		said = append(said, [2]string{human, hidden})
	}
	golden.Check(t, "messages", said)
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

func TestEntryMatchesGolden(t *testing.T) {
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
	want := golden.Load[[]argvRow](t, "argv")
	for i, c := range cases {
		argv, given := c[0], c[1]
		var rewrite [3]any
		if rest, verb, err := Rewrite(slices.Clone(argv)); err != nil {
			rewrite = [3]any{nil, nil, err.Error()}
		} else {
			var words any
			if verb != nil {
				words = verb.words
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
