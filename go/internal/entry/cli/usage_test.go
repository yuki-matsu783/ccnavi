package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
)

// 一覧が testdata/overview.txt と一致する。幅は符号位置で数える。
func TestOverviewMatchesGolden(t *testing.T) {
	want, err := os.ReadFile(filepath.Join("testdata", "overview.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if got := overview(); got != string(want) {
		t.Errorf("overview が testdata と違う:\n%s", got)
	}
	if n := len(strings.Split(strings.TrimSuffix(overview(), "\n"), "\n")); n >= 40 {
		t.Errorf("一覧が %d 行（40 行未満にする）", n)
	}
	full := fullUsage()
	for _, r := range topicRows {
		if !strings.Contains(full, topic(r.name)) || topic(r.name) == "" {
			t.Errorf("全文に %s の節が無い", r.name)
		}
	}
}

// 引数解析が受けるフラグとサブコマンドは、ヘルプの全文に書いてある。解析にだけ足して書き忘れると、ここで落ちる。
func TestEveryFlagAndSubcommandIsInTheHelp(t *testing.T) {
	full := fullUsage()
	// `--help` はヘルプそのもの、`--reverse` は `--docs` の節に `-r` で書いてある。
	unlisted := []string{"--help", "--reverse"}
	n := 0
	for _, a := range newParser().Actions() {
		for _, flag := range a.OptionStrings {
			if !strings.HasPrefix(flag, "--") || slices.Contains(unlisted, flag) {
				continue
			}
			n++
			if !strings.Contains(full, flag) {
				t.Errorf("ヘルプに書いていないフラグ: %s", flag)
			}
		}
	}
	if n < 50 {
		t.Fatalf("フラグを拾えていない（%d）", n)
	}
	pair := regexp.MustCompile(`(?:head\(|\[\]string\{)"([a-z0-9-]+)", "([a-z-]+)"[)}]`)
	found := 0
	for _, name := range []string{"route.go", "ops.go"} {
		src, err := os.ReadFile(name)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range pair.FindAllStringSubmatch(string(src), -1) {
			found++
			if !strings.Contains(full, "ccnavi "+m[1]+" "+m[2]) {
				t.Errorf("ヘルプに書いていないサブコマンド: %s %s", m[1], m[2])
			}
		}
	}
	if found == 0 {
		t.Fatal("サブコマンドを拾えていない")
	}
	for _, m := range regexp.MustCompile(`worktree ([a-z-]+) <`).FindAllStringSubmatch(worktreeUsage, -1) {
		if !strings.Contains(full, "ccnavi worktree "+m[1]+" ") {
			t.Errorf("ヘルプに書いていない worktree のサブコマンド: %s", m[1])
		}
	}
}

// 話題の節には、その話題のものだけを書く。
func TestATopicHoldsOnlyItsOwnCommands(t *testing.T) {
	own := map[string][]string{
		"docs":      {"--text", "--sort", "--format", "--no-refresh"},
		"templates": {"--draft", "ticket new ID --title"},
		"agree":     {"--plan-order", "--fill-phases", "--digest"},
		"ticket":    {"ticket record-risk", "ticket learn", "ticket next-id"},
		"scripts":   {"ccnavi write-and-push family", "ccnavi worktree tidy", "--record-writes"},
		"logs":      {"--prune", "--suggest"},
	}
	for name, flags := range own {
		text := topic(name)
		for _, f := range flags {
			if !strings.Contains(text, f) {
				t.Errorf("%s に %s が無い", name, f)
			}
		}
		for other, others := range own {
			for _, f := range others {
				if other != name && strings.Contains(text, f) {
					t.Errorf("%s に %s の %s がある", name, other, f)
				}
			}
		}
	}
}

// `--help` の話題はオプションか語で選ぶ。オプションの優先は helpTopics の順（上が勝つ）。`--test` は 2 語を取るが、
// `--help` と一緒なら test の節に届く。
func TestHelpPicksTheTopic(t *testing.T) {
	for _, c := range []struct {
		args  []string
		topic string
	}{
		{[]string{"--help"}, ""},
		{[]string{"--docs", "--help"}, "docs"},
		{[]string{"--help", "--prune"}, "logs"},
		{[]string{"--agree", "--help"}, "agree"},
		{[]string{"ticket", "--help"}, "ticket"},
		{[]string{"--help", "docs"}, "docs"},
		{[]string{"worktree", "--help"}, "scripts"},
		{[]string{"write-and-push", "--help"}, "scripts"},
		{[]string{"--version", "--help"}, "version"},
		{[]string{"--help", "--via", "board"}, "scripts"},
		{[]string{"--help", "--close-early"}, "scripts"},
		{[]string{"-h", "--suggest"}, "logs"},
		{[]string{"--help", "--templates", "--docs"}, "docs"},
		{[]string{"--test", "--help"}, "test"},
		{[]string{"--test", "Bash", "--help"}, "test"},
		{[]string{"--test", "Bash", "ls", "--help"}, "test"},
		{[]string{"--help", "--test"}, "test"},
	} {
		var out, errOut bytes.Buffer
		code := Run(strings.NewReader(""), &out, &errOut, c.args)
		want := overview()
		if c.topic != "" {
			want = topic(c.topic)
		}
		if code != 1 || errOut.String() != want {
			t.Errorf("%v: code %d、%s の節でない:\n%s", c.args, code, c.topic, errOut.String())
		}
	}
	var out, errOut bytes.Buffer
	Run(strings.NewReader(""), &out, &errOut, []string{"--help", "nothing"})
	if got := errOut.String(); !strings.HasPrefix(got, "ccnavi: 話題 'nothing' はありません") || !strings.HasSuffix(got, overview()) {
		t.Errorf("知らない語: %s", got)
	}
	for _, h := range helpTopics {
		if topicWord(h.name) != h.name {
			t.Errorf("話題 %s の節が無い", h.name)
		}
	}
}
