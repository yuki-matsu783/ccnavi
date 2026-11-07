package useronly

import (
	"errors"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"

	"ccnavi/internal/tickets/testkit"
)

// 表と読み替え。CLI を通さない部品の答えを見る。

// 空のグループから組んだ式は何にでも当たるので、使うグループにはどれも語がある。
func TestEveryUsedGroupHasWords(t *testing.T) {
	for name, got := range map[string][]string{
		"approval": approvalFlags, "yes": yesFlags, "handoff": importFlags, "guard": guardFlags,
		"abandon": abandonFlags, "choices": choicesFlags, "prune": {pruneFlag},
	} {
		if len(got) == 0 || got[0] == "" {
			t.Errorf("%s: 語が無い", name)
		}
	}
	for _, combo := range boardCombos {
		for _, g := range combo {
			if len(names(anywhere, g)) == 0 {
				t.Errorf("%s: 語が無い", g)
			}
		}
	}
}

func TestEveryVerbReadsIntoAGatedOption(t *testing.T) {
	gated := map[string]bool{}
	for _, f := range flags {
		gated[f.name] = f.gated
	}
	internal := map[string]bool{}
	for _, v := range verbs {
		argv := append([]string{"user"}, v.words...)
		want := slices.Clone(v.internal)
		if v.value {
			argv = append(argv, "VALUE")
			want = append(want, "VALUE")
		}
		got, verb, err := Rewrite(argv)
		if err != nil || verb == nil || verb.op != v.op || !slices.Equal(got, want) {
			t.Errorf("%v: %v %v %v", v.words, got, verb, err)
		}
		last := v.internal[len(v.internal)-1]
		internal[last] = true
		if !gated[last] {
			t.Errorf("%s: user の外で断らない", last)
		}
	}
	// `--yes` はどの操作にも付く。ほかの断るオプションは、読み替えの先として `user` の操作に入っている。
	for _, f := range flags {
		if f.gated && f.name != optYes && !internal[f.name] {
			t.Errorf("%s: user の操作が無い", f.name)
		}
	}
}

func TestTerminal(t *testing.T) {
	for _, op := range []Op{OpApprove, OpReviewed, OpCloseEarly, OpPrune, OpHandoff, OpMirror} {
		if shown, err := Terminal(op); err != nil || !strings.HasPrefix(shown, UserScriptPrefix) {
			t.Errorf("%s: %q %v", op, shown, err)
		}
	}
	if _, err := Terminal("decide"); !errors.Is(err, errNotTerminal) {
		t.Errorf("知らない操作: %v", err)
	}
}

func TestBoardVerbs(t *testing.T) {
	for _, w := range []string{"handoff", "mirror"} {
		if !slices.Contains(wordGroups[groupUserBoardVerb], w) {
			t.Errorf("%s がボードの操作に無い", w)
		}
	}
}

func TestRewrite(t *testing.T) {
	cases := []struct {
		argv, want []string
		words      []string
	}{
		{[]string{"--root", "R", "--cwd=C", "--record-writes", "W", "user", "agree", "A"},
			[]string{"--root", "R", "--cwd=C", "--record-writes", "W", "--agree", "A"}, []string{"agree"}},
		{[]string{"USER", "Close-Early", "--reason", "x"}, []string{"--close-early", "--reason", "x"}, []string{"close-early"}},
		{[]string{"user", "reviewed", "3", "--accept-unresolved"}, []string{"--reviewed", "3", "--accept-unresolved"}, []string{"reviewed"}},
		{[]string{"user", "handoff", "a.md", "b.md", "--json"}, []string{"--handoff", "a.md", "b.md", "--json"}, []string{"handoff"}},
		{[]string{"user", "mirror", "lib", "app", "--json"}, []string{"--mirror", "lib", "app", "--json"}, []string{"mirror"}},
		{[]string{"USER", "HandOff", "a.md"}, []string{"--handoff", "a.md"}, []string{"handoff"}},
		// 入口でない位置と、互換の読みでも入口の語にならない ASCII でない語。
		{[]string{"--docs", "--keyword", "user"}, []string{"--docs", "--keyword", "user"}, nil},
		{[]string{"--", "user", "agree"}, []string{"--", "user", "agree"}, nil},
		{[]string{"--title", "user", "agree"}, []string{"--title", "user", "agree"}, nil},
		{[]string{"ticket", "user"}, []string{"ticket", "user"}, nil},
		{[]string{"--agree", "--preview"}, []string{"--agree", "--preview"}, nil},
		{[]string{"ユーザ", "agree"}, []string{"ユーザ", "agree"}, nil},
	}
	for _, c := range cases {
		got, verb, err := Rewrite(c.argv)
		var words []string
		if verb != nil {
			words = verb.words
		}
		if err != nil || !slices.Equal(got, c.want) || !slices.Equal(words, c.words) {
			t.Errorf("%q: %q %v %v", c.argv, got, words, err)
		}
	}
	// `casefold` は `ſ` を `s` に、`ﬀ` を `ff` にする。ASCII の綴りだけを入口の語として読み、互換の読みで
	// 入口の語になる綴りと、使い方の誤りは断る。
	for _, argv := range [][]string{
		{"uſer", "handoff"}, {"ｕser", "agree"}, {"user", "handoﬀ"}, {"user", "agreе"},
		{"--root", "R", "user", "аgree"}, {"user"}, {"user", "bogus"}, {"user", "reviewed"}, {"user", "reviewed", "--x"},
	} {
		var entry *EntryError
		if _, _, err := Rewrite(argv); !errors.As(err, &entry) {
			t.Errorf("%q: 断らない（%v）", argv, err)
		}
	}
}

func TestTakeEntry(t *testing.T) {
	cases := []struct {
		argv, rest []string
		mark       string
	}{
		{[]string{"--entry", "sh", "user", "agree"}, []string{"user", "agree"}, "sh"},
		{[]string{"--entry=board", "user", "mirror"}, []string{"user", "mirror"}, "board"},
		{[]string{"--root", "R", "--entry", "sh", "--cwd", "C", "user", "agree", "i1"},
			[]string{"--root", "R", "--cwd", "C", "user", "agree", "i1"}, "sh"},
		{[]string{"user", "agree"}, []string{"user", "agree"}, ""},
		{[]string{"--agree", "--preview"}, []string{"--agree", "--preview"}, ""},
		// ほかの位置の印は外さない（実行ファイルが知らないオプションとして断る）。
		{[]string{"user", "agree", "--entry", "sh"}, []string{"user", "agree", "--entry", "sh"}, ""},
		// decide の sh が打つ事前検査の並び（`--check` は `user reviewed N` の後ろ）。
		{[]string{"--entry", "sh", "user", "reviewed", "1", "--check", "--accept-unresolved"},
			[]string{"user", "reviewed", "1", "--check", "--accept-unresolved"}, "sh"},
	}
	for _, c := range cases {
		rest, mark, err := TakeEntry(c.argv)
		if err != nil || !slices.Equal(rest, c.rest) || mark != c.mark {
			t.Errorf("%q: %q %q %v", c.argv, rest, mark, err)
		}
	}
	for _, argv := range [][]string{
		{"--entry", "sh", "--check", "user", "reviewed", "1", "--accept-unresolved"},
		{"--entry", "terminal", "user", "agree"}, {"--entry"}, {"--entry", "sh"}, {"--entry", "sh", "agree"},
		{"--entry", "sh", "--entry", "board", "user", "agree"},
	} {
		var entry *EntryError
		if _, _, err := TakeEntry(argv); !errors.As(err, &entry) {
			t.Errorf("%q: 断らない（%v）", argv, err)
		}
	}
}

func TestRefusedLooksAtWhatWasRead(t *testing.T) {
	given := func(options ...string) func(string) bool {
		return func(o string) bool { return slices.Contains(options, o) }
	}
	cases := []struct {
		options []string
		want    string
	}{
		{nil, ""},
		{[]string{"--agree", "--preview"}, ""},
		{[]string{"--agree"}, "--agree はユーザ専用です。ユーザが `ccnavi-user-agree.sh` で打ちます。見るだけなら --agree --preview を使います。"},
		// `--yes` は最後に見る。対応する操作のオプションの文を先に出す。
		{[]string{"--yes", "--close-early"}, "--close-early はユーザ専用です。ユーザが `ccnavi-user-close-early.sh` で打ちます。"},
		{[]string{"--yes"}, "--yes はユーザ専用です（ユーザが `ccnavi-user-*.sh` で打ちます）。"},
		{[]string{"--handoff", "--preview"}, "--handoff はユーザ専用です。ユーザが `ccnavi-user-handoff.sh` で打ちます。"},
	}
	for _, c := range cases {
		if got := Refused(given(c.options...)); got != c.want {
			t.Errorf("%v: %q", c.options, got)
		}
	}
}

// 止めている間に通す形と、サブエージェントの禁止。判定の部品の答えを見る。

func TestExempt(t *testing.T) {
	root := filepath.Join(t.TempDir(), "My Projects", "ws")
	review := config.ScriptCommand(root, "ccnavi-review.sh")
	read := func(src string) (string, string) {
		r := shell.Read(src)
		if r.Degraded {
			return src, string(r.Reason)
		}
		return r.Text, ""
	}
	cases := []struct {
		command string
		want    bool
	}{
		// 案内どおりの形（空白を含むルートの引用でも 1 語に読む）。
		{review + " review-request --phase 1 --body-file b.md", true},
		{review + " confirm --phase 1", true},
		{"sh .ccnavi/scripts/ccnavi-ticket.sh finish x\nsh .ccnavi/scripts/ccnavi-git.sh status", true},
		{`sh .ccnavi/scripts/ccnavi-git.sh commit -m "docs: a b"`, true},
		{"cd /w && sh .ccnavi/scripts/ccnavi-review.sh fetch | tail -3", true},
		{"sh .ccnavi/scripts/ccnavi-launcher.sh --docs x", true},
		// 置換の中の cat まで免除しない。連結の片方が違えば止める。
		{`sh .ccnavi/scripts/ccnavi-git.sh commit -m "$(cat f)"`, false},
		{`ls; sh .ccnavi/scripts/ccnavi-git.sh commit -m "docs: a b"`, false},
		{"sh .ccnavi/scripts/ccnavi-launcher.sh --lint", false},
		{"sh .ccnavi/scripts/ccnavi-review.sh fetch | tail -f x", false},
	}
	for _, c := range cases {
		subject, reason := read(c.command)
		if got := Exempt(subject, reason); got != c.want {
			t.Errorf("%q: %v", c.command, got)
		}
	}
	// 付き添いだけでは通さない。付き添いにリダイレクトを付けても通さない。
	for _, subject := range []string{"cd /tmp\x00tail -3", "sh .ccnavi/scripts/ccnavi-review.sh fetch\x00tail > out"} {
		if Exempt(subject, "") {
			t.Errorf("%q を通した", subject)
		}
	}
	if Exempt("sh .ccnavi/scripts/ccnavi-review.sh fetch", "backquote") {
		t.Error("読み切れなかったコマンドを通した")
	}
	if got := len(commands(shell.Read(`sh .ccnavi/scripts/ccnavi-git.sh commit -m "docs: a b"`).Text)); got != 1 {
		t.Errorf("引用の空白でコマンドが %d 本に割れた", got)
	}
}

func TestForbidden(t *testing.T) {
	root := filepath.Join(t.TempDir(), "My Projects", "ws")
	review := config.ScriptCommand(root, "ccnavi-review.sh")
	ticket := config.ScriptCommand(root, "ccnavi-ticket.sh")
	for command, want := range map[string]bool{
		review + " ready":                                        true,
		ticket + " finish i0001-01-01":                           true,
		`echo "$(sh .ccnavi/scripts/ccnavi-ticket.sh finish x)"`: true,
		"SH .ccnavi/scripts/CCNAVI-TICKET.SH start":              true,
		"./.ccnavi/scripts/ccnavi-git.sh push":                   true,
		"sh ccnavi-sync.sh":                                      true,
		"sh ccnavi-start.sh new":                                 true,
		ticket + " status":                                       false,
		review + " decide 3 --preview":                           false,
		"sh ccnavi-git.sh status":                                false,
		"sh ccnavi-start.sh --list-open":                         false,
		"cat .ccnavi/scripts/ccnavi-ticket.sh":                   false,
		"sh .ccnavi/scripts/ccnavi-launcher.sh --docs x":         false,
	} {
		if got := Forbidden(shell.Read(command).Text, ""); got != want {
			t.Errorf("%q: %v", command, got)
		}
	}
	// 中で実行されるコマンドの層にも当てる（`env sh …` は元の形では先頭の sh に当たらない）。
	if !Forbidden("env sh .ccnavi/scripts/ccnavi-ticket.sh start", "sh .ccnavi/scripts/ccnavi-ticket.sh start") {
		t.Error("層を見ていない")
	}
}

// 記録を消す --prune。起動の sh 経由の形も止める（#786）。
func TestPruneForm(t *testing.T) {
	const launcher = "sh .ccnavi/scripts/ccnavi-launcher.sh"
	for command, want := range map[string]string{
		launcher + " --prune":                  optPrune,
		launcher + " --prune --preview":        "",
		"ccnavi --prune":                       optPrune,
		"ccnavi --preview --prune":             "",
		`ccnavi --prune "--preview"`:           optPrune,
		`bash -c "ccnavi --prune" x --preview`: optPrune,
		"git fetch --prune":                    "",
		"grep -n 'ccnavi --prune' x":           "",
	} {
		if got := PruneForm(command, ""); got != want {
			t.Errorf("%q: %q", command, got)
		}
	}
	if got := PruneForm("my-ccnavi --prune", "/w/bin/my-ccnavi"); got != optPrune {
		t.Errorf("実行ファイルの名前: %q", got)
	}
	if got := UserPruneForm("ccnavi --root /r user prune --yes", ""); got != "--root /r user prune" {
		t.Errorf("user prune: %q", got)
	}
}

// エージェントが打てる形は組み込みの止めに当たらない（tests/ticket の plan-order・fill-phases）。
func TestPlanOrderIsNotStopped(t *testing.T) {
	rule, err := TicketApprovalRule("", "/w")
	if err != nil {
		t.Fatal(err)
	}
	for _, command := range []string{
		"ccnavi --plan-order i0001 --json",
		`ccnavi --plan-order i0001 --json --order '[{"from": 1}]'`,
		".ccnavi/bin/ccnavi --plan-order i0001 --order '[]' --expect abc --write",
		"ccnavi --plan-order i0001 --fill-phases",
		".ccnavi/bin/ccnavi --plan-order i0001 --fill-phases",
	} {
		if rule.Compiled.Search(command) {
			t.Errorf("%q に当たった", command)
		}
		if kind, found := HumanPathForm(command); kind != humanNone {
			t.Errorf("%q: %s %q", command, kind, found)
		}
	}
}

// 案内どおりに打った承認の sh に当たり、文がその打ち方を名指す。
func TestRuleNamesTheScriptToType(t *testing.T) {
	root := t.TempDir()
	testkit.CopyMessages(t, root)
	rule, err := TicketApprovalRule("", root)
	if err != nil {
		t.Fatal(err)
	}
	approve := config.ScriptCommand(root, "ccnavi-user-agree.sh")
	if !rule.Compiled.Search(approve) {
		t.Errorf("%q に当たらない", approve)
	}
	said := texts.Spoken(rule)
	if !strings.Contains(said, approve) || strings.Contains(said, "sh .ccnavi/scripts/") {
		t.Errorf("文: %s", said)
	}
}

// 組み込みのルールの文の識別子・値・場合が文面ファイルと合っている。判定の文（HumanMessage など）も引けること。
func TestBuiltinRuleTexts(t *testing.T) {
	repo := testkit.RepoRoot(t)
	catalog := texts.Load(repo)
	root := t.TempDir()
	testkit.CopyMessages(t, root)
	rule, err := TicketApprovalRule(filepath.Join(root, "bin", "ccnavi"), root)
	if err != nil {
		t.Fatal(err)
	}
	if rule.Text.ID == "" || rule.Message != "" {
		t.Fatalf("文の識別子が無いか、文を直接持っている: %+v", rule.Text)
	}
	entry := catalog.Entries[rule.Text.ID]
	if entry == nil {
		t.Fatalf("%s が無い", rule.Text.ID)
	}
	if len(rule.Text.Values) != len(entry.Values) {
		t.Errorf("値 %v, 文面ファイル %v", rule.Text.Values, entry.Values)
	}
	for name, c := range rule.Text.Variants {
		if !slices.Contains(entry.Cases(name), c) {
			t.Errorf("欄 %s に場合 %s が無い", name, c)
		}
	}
	said := texts.Spoken(rule)
	if said == texts.Fallback(rule.Text.ID) || strings.Contains(said, "引けませんでした") {
		t.Errorf("文が引けない: %s", said)
	}
	for _, s := range []string{
		HumanMessage(root, humanGuardOff, "x"), HumanMessage(root, humanUserScript, "x"),
		HumanMessage(root, humanExpanded, "x"), HumanMessage(root, humanBoard, "--via … board"),
		HumanMessage(root, humanBoard, "user … mirror … --yes"), HiddenEntryMessage(root, entryExe, "$X"),
		HiddenEntryMessage(root, config.ReviewWrapper, "$X"), HiddenEntryMessage(root, "other", "$X"),
		HiddenEntryMessage(root, entryExe, stdinShell), HiddenEntryMessage(root, entryExe, "xargs"), PruneMessage(root),
	} {
		if strings.Contains(s, "引けませんでした") || strings.Contains(s, "{") {
			t.Errorf("文が引けない: %s", s)
		}
	}
}
