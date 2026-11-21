package core

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/scenario/host"
)

const sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

func load(text string, agent ...string) (*Scenario, error) {
	a := AgentClaude
	if len(agent) > 0 {
		a = agent[0]
	}
	return Load("scn.yml", text, a)
}

func TestLoadRejectsMalformedScenarios(t *testing.T) {
	t.Parallel()
	const dockerHead = "id: T\ntest_repo: true\nsetup:\n  host: docker-gitlab\n"
	cases := []struct{ name, yml, want string }{
		{"id が無い", "steps: []\n", "id がありません"},
		{"人間の step に expect_after", "id: T\nsteps:\n  - human: true\n    instruction: x\n    expect_after:\n      decisions: []\n", "人間の操作の step には expect_after"},
		{"expect_after の知らない欄", "id: T\nsteps:\n  - prompt: a\n    expect_after:\n      files_unchanged: [x]\n", "files_unchanged"},
		{"setup の知らない欄", "id: T\nsteps: []\nsetup:\n  reeset: {}\n", "setup に書けるのは"},
		{"setup.git の対象外へ出る", "id: T\nsteps: []\nsetup:\n  reset: {base: " + sha + "}\n  git: [[git, -C, /, status]]\n", "-C"},
		{"setup.git だけで reset が無い", "id: T\nsteps: []\nsetup:\n  git: [[git, status]]\n", "setup.reset"},
		{"追記だけで reset が無い", "id: T\nsteps: []\nsetup:\n  files:\n    x.md: {content: y, append: true}\n", "setup.reset"},
		{"files の値が数", "id: T\nsteps: []\nsetup:\n  files: {x: 1}\n", "文字列か"},
		{"files の content が無い", "id: T\nsteps: []\nsetup:\n  files:\n    x: {append: true}\n  reset: {base: " + sha + "}\n", "content"},
		{"files の append が真偽でない", "id: T\nsteps: []\nsetup:\n  files:\n    x: {content: y, append: 'yes'}\n  reset: {base: " + sha + "}\n", "append"},
		{"files の知らない欄", "id: T\nsteps: []\nsetup:\n  files:\n    x: {content: y, mode: a}\n  reset: {base: " + sha + "}\n", "content と append だけ"},
		{"reset.base が短縮 SHA", "id: T\nsteps: []\nsetup:\n  reset: {base: abc1234}\n", "40 桁"},
		{"reset.base がブランチ名", "id: T\nsteps: []\nsetup:\n  reset: {base: main}\n", "40 桁"},
		{"reset に余計な欄", "id: T\nsteps: []\nsetup:\n  reset: {base: " + sha + ", force: true}\n", "base だけ"},
		{"git が文字列", "id: T\nsteps: []\nsetup:\n  git: git status\n", "setup.git は"},
		{"host の値が不明", "id: T\nsteps: []\nsetup:\n  host: nowhere\n", "setup.host は"},
		{"docker-gitlab と issue の併用", "id: T\nsteps: []\nsetup:\n  host: docker-gitlab\n  issue: {repo: r, title: t}\n", "setup.issue"},
		{"docker-gitlab と host_state の併用", "id: T\nsteps: []\nsetup:\n  host: docker-gitlab\n  host_state: {}\n", "host_state"},
		// human_api
		{"human_api が human でない step", dockerHead + "steps:\n  - prompt: a\n    human_api: [{op: approve}]\n", "human: true の step にだけ"},
		{"human_api が docker-gitlab 以外", "id: T\nsetup:\n  host: fake-gitlab\nsteps:\n  - human: true\n    human_api: [{op: approve}]\n", "docker-gitlab"},
		{"human_api が空", dockerHead + "steps:\n  - human: true\n    human_api: []\n", "1 件以上"},
		{"human_api の op が不明", dockerHead + "steps:\n  - human: true\n    human_api: [{op: comment, body: x}]\n", "op は"},
		{"thread に body が無い", dockerHead + "steps:\n  - human: true\n    human_api: [{op: thread}]\n", "body"},
		{"resolve が名前の無い thread を指す", dockerHead + "steps:\n  - human: true\n    human_api: [{op: resolve, thread: t9}]\n", "as で名前を付けたものだけ"},
		{"resolve が thread より前", dockerHead + "steps:\n  - human: true\n    human_api:\n      - {op: resolve, thread: t1}\n      - {op: thread, body: x, as: t1}\n", "as で名前を付けたものだけ"},
		// human_run（schema.py の _check_human_run の 4 つの検査と、残りの形）
		{"human_run が human でない step", "id: T\ntest_repo: true\nsteps:\n  - prompt: a\n    human_run: {script: agree}\n", "human: true の step にだけ"},
		{"human_run と human_api の併記", dockerHead + "steps:\n  - human: true\n    instruction: i\n    human_api: [{op: approve}]\n    human_run: {script: agree}\n", "human_api と同じ step には書けません"},
		{"human_run が test_repo でないシナリオ", "id: T\nsteps:\n  - human: true\n    instruction: i\n    human_run: {script: agree}\n", "test_repo: true のシナリオにだけ"},
		{"human_run の script が表に無い", "id: T\ntest_repo: true\nsteps:\n  - human: true\n    instruction: i\n    human_run: {script: other}\n", "script は agree のどれか"},
		{"human_run に余計な欄", "id: T\ntest_repo: true\nsteps:\n  - human: true\n    instruction: i\n    human_run: {script: agree, argv: [x]}\n", "script と answers だけ"},
		{"human_run の answers が文字列でない", "id: T\ntest_repo: true\nsteps:\n  - human: true\n    instruction: i\n    human_run: {script: agree, answers: [1]}\n", "answers は文字列の list"},
		{"human_run に instruction が無い", "id: T\ntest_repo: true\nsteps:\n  - human: true\n    human_run: {script: agree}\n", "instruction"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if _, err := load(tc.yml); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("err = %v, want 「%s」を含む", err, tc.want)
			}
		})
	}
}

func TestScriptedAgentNeedsAScriptInEveryPromptStep(t *testing.T) {
	t.Parallel()
	text := "id: T\nsteps:\n  - prompt: a\n"
	if _, err := load(text, AgentScript); err == nil || !strings.Contains(err.Error(), "script が要ります") {
		t.Fatalf("err = %v", err)
	}
	if _, err := load(text); err != nil {
		t.Fatalf("claude で回すなら script は要らない: %v", err)
	}
}

func TestLoadReadsEveryShapeOfSetup(t *testing.T) {
	t.Parallel()
	s, err := load(`id: UC-1
title: t
test_repo: true
steps:
  - prompt: a
  - human: true
    instruction: 手で
    check: [c1, c2]
    human_run: {script: agree, answers: [y]}
expect:
  decisions:
    - {tool: Edit, decision: deny, rules_include: [r], min_count: 2, hinted: false}
  state:
    - {run: "echo hi", expect_contains: hi}
setup:
  files:
    x.md: {content: y, append: true}
    z: s
  git: [[git, switch, -c, feat]]
  reset: {base: ` + strings.ToUpper(sha) + `}
  host: bare
`)
	if err != nil {
		t.Fatal(err)
	}
	if s.Model != "haiku" || s.BudgetUSD != 1.0 || s.PermissionMode != "auto" || s.PromptSteps != 1 {
		t.Errorf("既定値 = %+v", s)
	}
	if got := s.Setup.Files; len(got) != 2 || !got[0].Append || got[0].Rel != "x.md" || got[1].Append || got[1].Body != "s" {
		t.Errorf("files = %+v", got)
	}
	if s.Setup.ResetBase != sha {
		t.Errorf("base は小文字にする: %s", s.Setup.ResetBase)
	}
	if s.HostKind() != host.Bare || !s.UsesTarget() {
		t.Errorf("host = %s", s.HostKind())
	}
	if c := s.Expect.Decisions[0]; c.MinCount != 2 || c.Hinted == nil || *c.Hinted || c.RulesInclude[0] != "r" {
		t.Errorf("cond = %+v", c)
	}
	if st := s.Steps[1]; st.HumanRun == nil || st.HumanRun.Script != "agree" || len(st.Check) != 2 {
		t.Errorf("step 2 = %+v", st)
	}
	if got := s.Steps[1].HumanRun.Text(); !strings.Contains(got, "ccnavi-user-agree.sh") {
		t.Errorf("Text = %s", got)
	}
}

func TestUsesTargetAndStubUnready(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, yml  string
		target     bool
		stubUnread string
	}{
		{"何も触らない", "id: T\nsteps:\n  - {prompt: a, script: [{tool: Bash}]}\n", false, ""},
		{"real は対象を触らない", "id: T\nsetup: {host: real}\nsteps:\n  - {prompt: a, script: [{tool: Bash}]}\n", false, "setup が作るファイル以外を使う: host"},
		{"bare は対象を触る", "id: T\nsetup: {host: bare}\nsteps:\n  - {prompt: a, script: [{tool: Bash}]}\n", true, "setup が作るファイル以外を使う: host"},
		{"人間の step がある", "id: T\nsteps:\n  - {human: true, instruction: i}\n", false, "人間の操作の step がある"},
		{"script の無い step", "id: T\nsteps:\n  - {prompt: a, script: [{tool: Bash}]}\n  - {prompt: b}\n", false, "step 2 に script が無い"},
		{"追記がある", "id: T\nsetup:\n  files: {x: {content: y, append: true}}\n  reset: {base: " + sha + "}\nsteps:\n  - {prompt: a, script: [{tool: Bash}]}\n", true, "setup が作るファイル以外を使う: reset"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			s, err := load(tc.yml)
			if err != nil {
				t.Fatal(err)
			}
			if s.UsesTarget() != tc.target || s.StubUnready() != tc.stubUnread {
				t.Errorf("UsesTarget = %v・StubUnready = %q（want %v・%q）", s.UsesTarget(), s.StubUnready(), tc.target, tc.stubUnread)
			}
		})
	}
}

func TestCheckGitArgv(t *testing.T) {
	t.Parallel()
	bad := [][]string{
		{"cd", "x"}, {"sh", "-c", "git status"}, {"git", "-C", "..", "status"}, {"git", "-C../x", "status"},
		{"git", "--git-dir=/x", "status"}, {"git", "--git-dir", "/x", "status"}, {"git", "--work-tree=/x", "status"},
		{"git", "--work-tree", "/x", "status"}, {"git", "-c", "alias.x=!sh", "x"}, {"git", "--exec-path=/x", "status"},
		{}, {"git", ""},
	}
	for _, argv := range bad {
		if err := CheckGitArgv(argv); err == nil {
			t.Errorf("%q を通した", argv)
		}
	}
	for _, argv := range [][]string{{"git", "switch", "-c", "feat"}, {"git", "commit", "-m", "x"}} {
		if err := CheckGitArgv(argv); err != nil {
			t.Errorf("%q: %v", argv, err)
		}
	}
}

func TestShippedScenariosAreWellFormed(t *testing.T) {
	t.Parallel()
	paths, _ := filepath.Glob(filepath.Join(base.Root(), "tests", "scenarios", "UC-*.yml"))
	if len(paths) == 0 {
		t.Fatal("シナリオが無い")
	}
	for _, path := range paths {
		t.Run(filepath.Base(path), func(t *testing.T) {
			t.Parallel()
			s, err := LoadFile(path, AgentClaude)
			if err != nil {
				t.Fatal(err)
			}
			if !strings.HasPrefix(filepath.Base(path), s.ID) || (s.Status != "draft" && s.Status != "verified") || len(s.Steps) == 0 {
				t.Errorf("id・status・steps が合わない: %+v", s)
			}
			e := s.Expect
			if len(e.Decisions)+len(e.DecisionsNone)+len(e.FilesUnchanged)+len(e.State)+len(e.TranscriptForbidden)+len(e.Judge) == 0 {
				t.Error("期待が 1 つも無い")
			}
			raw := s.Raw.(map[string]any)["expect"].(map[string]any)
			for k := range raw {
				switch k {
				case "decisions", "decisions_none", "files_unchanged", "state", "transcript_forbidden", "judge":
				default:
					t.Errorf("知らない期待の欄 %s", k)
				}
			}
			for i, st := range s.Steps {
				if !st.Human && st.Prompt == "" || st.Human && st.Instruction == "" {
					t.Errorf("step %d に prompt も instruction も無い", i+1)
				}
			}
		})
	}
}

func TestUC09CreatesAnIssuePerRun(t *testing.T) {
	t.Parallel()
	s, err := LoadFile(filepath.Join(base.Root(), "tests", "scenarios", "UC-09-quick-ticket.yml"), AgentClaude)
	if err != nil {
		t.Fatal(err)
	}
	if i := s.Setup.Issue; i == nil || i.Repo != "projects/test-repo" || !i.Close {
		t.Fatalf("issue = %+v", i)
	}
	if p := s.Steps[0].Prompt; !strings.Contains(p, "{issue}") || strings.Contains(p, "#34") {
		t.Errorf("prompt は番号を {issue} で受ける: %s", p)
	}
}

func TestStateRoundTripKeepsAnEmptyBaselineAsAList(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	if err := Save(dir, &State{ProposalsBefore: []Proposal{}}); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(dir, "state.json"))
	if !strings.Contains(string(data), `"proposals_before": []`) {
		t.Errorf("承認待ちの基準は空でも [] で書く: %s", data)
	}
	if err := Save(dir, &State{}); err != nil {
		t.Fatal(err)
	}
	st, err := LoadState(dir)
	if err != nil || st.ProposalsBefore != nil {
		t.Errorf("基準が無い状態は nil のまま読む: %v %v", st, err)
	}
}

func TestRefusalIsReasonNotesAndOneNextLine(t *testing.T) {
	t.Parallel()
	got := (&Refusal{What: "理由", Next: "直す", Notes: []string{"a", "b"}}).Error()
	if got != "理由\n案内: a\n案内: b\n次: 直す" {
		t.Errorf("= %q", got)
	}
}
