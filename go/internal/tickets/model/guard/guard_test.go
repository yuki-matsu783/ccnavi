package guard

import (
	"bytes"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/rules"
)

// Python の ticket_guard と突き合わせる。式の文字列が 1 字も違わないことと、組んだルールが同じ呼び出しに
// 同じ答えを返すこと。

type guardRule struct {
	ID       string            `json:"id"`
	Match    string            `json:"match"`
	Pattern  string            `json:"pattern"`
	Text     string            `json:"text"`
	Variants map[string]string `json:"variants"`
	Base     bool              `json:"base"`
}

type guardAnswer struct {
	Rules []guardRule `json:"rules"`
	Hits  [][]string  `json:"hits"`
	State string      `json:"state"`
}

type guardCase struct {
	Root     string `json:"root"`
	Tickets  string `json:"tickets"`
	Approved string `json:"approved"`
}

var guardCases = []guardCase{
	{"/ws", config.DefaultTickets, config.DefaultApproved},
	{"/ws", "props", "a.b/c+d/"},
}

var guardPaths = [][2]string{
	{"Write", "/ws/wip/proposals/review/x.md"},
	{"Edit", "/ws/WIP/proposals/REVIEW/x.md"},
	{"Write", "/ws/wip/web/proposals/review/x.md"},
	{"Write", "/ws/wip/proposals/todo/x.md"},
	{"Write", "/ws/.ccnavi/approved/doing/x.md"},
	{"Write", "/ws/logs/state/ops/x.json"},
	{"Write", "/ws/props/review/x"},
	{"Write", "/ws/a.b/c+d/x"},
}

var guardCommands = []string{
	"git status", "sh .ccnavi/scripts/ccnavi-git.sh status", "/usr/bin/git push", "C:\\Git\\git.exe log",
	"echo to do git x", "if true; then git log; fi", "grep git README.md", "env git status",
	"mv x wip/proposals/review", "cp -t wip/proposals/review a.md", "cp wip/proposals/review/a.md /tmp/",
	"cp -r /tmp/review wip/proposals/", "touch .ccnavi/approved/x", "cat .ccnavi/approved/doing/x.md",
	"echo x > logs/state/ops/a", "python3 x.py logs/state/ops", "rm -rf .ccnavi/approved", "mv a props/review",
}

func TestGuardMatchesPython(t *testing.T) {
	want := golden.Load[[]guardAnswer](t, "guard")
	for i, c := range guardCases {
		var all []*rules.Rule
		raw, err := RawGitRule(c.Root)
		if err != nil {
			t.Fatal(err)
		}
		all = append(all, raw)
		state, err := GuardRules(c.Tickets, c.Root)
		if err != nil {
			t.Fatal(err)
		}
		gate, err := GateRules(c.Approved, c.Root)
		if err != nil {
			t.Fatal(err)
		}
		approved, err := ApprovedWriteRule(c.Approved, c.Root)
		if err != nil {
			t.Fatal(err)
		}
		all = append(append(append(all, state...), gate...), approved)
		got := guardAnswer{State: stateDirRegex(c.Tickets)}
		for _, r := range all {
			v := r.Text.Variants
			if v == nil {
				v = map[string]string{}
			}
			got.Rules = append(got.Rules, guardRule{r.ID, r.Match, r.Compiled.Pattern(), r.Text.ID, v, r.Base})
		}
		subjects := guardPaths
		for _, cmd := range guardCommands {
			subjects = append(subjects, [2]string{config.ToolBash, shell.Read(cmd).Text})
		}
		for _, s := range subjects {
			hits := []string{}
			for _, r := range all {
				if r.Matches(s[0], s[1]) {
					hits = append(hits, r.ID)
				}
			}
			got.Hits = append(got.Hits, hits)
		}
		if !reflect.DeepEqual(got, want[i]) {
			t.Errorf("case %d\n got  %+v\n want %+v", i, got, want[i])
		}
	}
}

func TestProposeNoticeOnlyInsideTheWorkspace(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{Tickets: config.DefaultTickets, State: ""}
	in := hookio.Input{ToolName: config.ToolWrite, ToolInput: pyjson.NewObject()}
	var stderr bytes.Buffer
	if said := ProposeNotice(&stderr, conf, root, in, root+"/wip/proposals/todo/a.md"); !strings.Contains(said, "approval-request") {
		t.Fatalf("文が渡らない: %q", said)
	}
	if said := ProposeNotice(&stderr, conf, root, in, "/elsewhere/wip/proposals/todo/a.md"); said != "" {
		t.Fatalf("ワークスペースの外で渡った: %q", said)
	}
	in.ToolName = config.ToolBash
	if said := ProposeNotice(&stderr, conf, root, in, root+"/wip/proposals/todo/a.md"); said != "" {
		t.Fatalf("書き込みのツールでないのに渡った: %q", said)
	}
}
