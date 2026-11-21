package actors

import (
	"errors"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/scenario/prepare"
)

// 端末が付き、答えが届き、終了コードが返る。
func TestTerminalIsAttachedAndAnswersArrive(t *testing.T) {
	t.Parallel()
	script := `if [ -t 0 ]; then echo tty; fi; read a; echo "got=$a"; exit 3`
	rc, text, err := RunOnTerminal([]string{"sh", "-c", script}, "", nil, []string{"y"}, 10*time.Second)
	if errors.Is(err, ErrNoTerminal) {
		t.Skip("script が無い機械・Windows は「手で打つ」で止まる経路に落ちる")
	}
	if err != nil || rc != 3 || !strings.Contains(text, "tty") || !strings.Contains(text, "got=y") || strings.Contains(text, "\r") {
		t.Errorf("rc = %d, err = %v, text = %q", rc, err, text)
	}
}

// 時間切れはグループごと止める（孫の sleep を残さない）。
func TestTerminalTimeoutStopsTheWholeGroup(t *testing.T) {
	t.Parallel()
	start := time.Now()
	_, _, err := RunOnTerminal([]string{"sh", "-c", "sleep 30 & wait"}, "", nil, nil, time.Second)
	if errors.Is(err, ErrNoTerminal) {
		t.Skip("script が無い")
	}
	if !errors.Is(err, ErrTimeout) || time.Since(start) > 15*time.Second {
		t.Errorf("err = %v, 経過 = %v", err, time.Since(start))
	}
}

// ボードの読み取りは、チケットの無いワークスペースでも通る（本物の ccnavi で通す）。
func TestParseBoardOfAWorkspaceWithoutTickets(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	r := run.Ccnavi(t, []string{"--explain", "--json", "--root", root}, run.Opts{Dir: root, NoMessages: true})
	if r.Code != 0 {
		t.Fatalf("code = %d, stderr = %s", r.Code, r.Stderr)
	}
	b, err := ParseBoard([]byte(r.Stdout))
	if err != nil || len(b.pending()) != 0 || len(b.approved()) != 0 {
		t.Errorf("board = %+v, err = %v", b, err)
	}
}

func TestParseBoardReadsStatusAndPending(t *testing.T) {
	t.Parallel()
	b, err := ParseBoard([]byte(`{"pending_approval": ["i2"], "tickets": [
		{"ticket": "i2", "project": "test-repo", "copy": {"status": "open"}},
		{"ticket": "i2", "project": "test-repo", "copy": {"status": "open"}},
		{"ticket": "i1", "project": "", "copy": {"status": "done"}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	if p := b.pending(); len(p) != 1 || p[0] != (prepare.Proposal{"i2", "test-repo"}) {
		t.Errorf("pending = %v（複数のツリーの同じ識別子は畳む）", p)
	}
	if a := b.approved(); !a["i2"] || a["i1"] {
		t.Errorf("approved = %v", a)
	}
}

// driveHumanRun は board の列（打つ前・打ったあと）と端末の結果を差し替えて human_run を 1 回回す。
type humanRunCase struct {
	before         []prepare.Proposal
	pending        []prepare.Proposal
	pendingAfter   *[]prepare.Proposal
	approved       []string
	approvedBefore []string
	exit           int
	execErr        error
}

func boardOf(pending []prepare.Proposal, open []string) Board {
	var b Board
	for _, p := range pending {
		b.Pending = append(b.Pending, p[0])
		b.Tickets = append(b.Tickets, Ticket{ID: p[0], Project: p[1]})
	}
	for _, id := range open {
		b.Tickets = append(b.Tickets, Ticket{ID: id, Project: DisposableProject, Open: true})
	}
	return b
}

func (c humanRunCase) drive(t *testing.T) (int, *prepare.State, [][]string) {
	t.Helper()
	after := c.pending
	if c.pendingAfter != nil {
		after = *c.pendingAfter
	}
	boards := []Board{boardOf(c.pending, c.approvedBefore), boardOf(after, c.approved)}
	var executed [][]string
	tm := Terminal{
		Board: func(string) (Board, error) { b := boards[0]; boards = boards[1:]; return b, nil },
		Exec: func(argv []string, dir string, env, answers []string, timeout time.Duration) (int, string, error) {
			executed = append(executed, argv)
			return c.exit, "", c.execErr
		},
	}
	before := c.before
	if before == nil {
		before = []prepare.Proposal{}
	}
	st := &prepare.State{ProposalsBefore: before}
	rc := tm.Run(st, 0, RunSpec{Script: ".ccnavi/scripts/ccnavi-user-agree.sh", Answers: []string{"y"}}, base.TempDir(t), base.TempDir(t), func() error { return nil }, func(string) {})
	return rc, st, executed
}

func TestHumanRunJudgesByApprovedTicketsNotTheExitCode(t *testing.T) {
	t.Parallel()
	rc, st, ran := humanRunCase{pending: []prepare.Proposal{{"i1", "test-repo"}}, exit: 0}.drive(t)
	if rc != Halted || !strings.Contains(st.Halt.Reasons[0], "新しく承認済みにならなかった: i1") || ran[0][2] != "i1" {
		t.Errorf("rc = %d, halt = %+v, ran = %v", rc, st.Halt, ran)
	}
	if st.HumanRuns[0].Status != "halted" {
		t.Errorf("status = %q", st.HumanRuns[0].Status)
	}
}

func TestHumanRunApprovedContinuesAndIsRecorded(t *testing.T) {
	t.Parallel()
	rc, st, _ := humanRunCase{pending: []prepare.Proposal{{"i1", "test-repo"}}, approved: []string{"i1"}}.drive(t)
	if rc != Done || st.HumanRuns[0].Status != "done" || st.NextStep != 1 || st.Halt != nil {
		t.Errorf("rc = %d, state = %+v", rc, st)
	}
	if len(st.ProposalsBefore) != 1 {
		t.Errorf("次の基準 = %v", st.ProposalsBefore)
	}
}

// 承認済みの親を改版したときは、承認待ちから消えれば成功。承認待ちに残ったままなら数えない。
func TestHumanRunRevisionOfAnApprovedParent(t *testing.T) {
	t.Parallel()
	parent := []prepare.Proposal{{"p1", "test-repo"}}
	gone := []prepare.Proposal{}
	rc, st, _ := humanRunCase{pending: parent, approved: []string{"p1"}, approvedBefore: []string{"p1"}, pendingAfter: &gone}.drive(t)
	if rc != Done || st.HumanRuns[0].Status != "done" {
		t.Errorf("承認待ちから消えた改版: rc = %d, state = %+v", rc, st)
	}
	rc, st, _ = humanRunCase{pending: parent, approved: []string{"p1"}, approvedBefore: []string{"p1"}}.drive(t)
	if rc != Halted || !strings.Contains(st.Halt.Reasons[0], "新しく承認済みにならなかった: p1") {
		t.Errorf("承認待ちのまま: rc = %d, halt = %+v", rc, st.Halt)
	}
}

// 打つ前に止める条件。黙って外すと親だけ落ちて承認が失敗するので、打たない。
func TestHumanRunStopsBeforeRunning(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		c      humanRunCase
		reason string
	}{
		{"test-repo 以外の提案", humanRunCase{pending: []prepare.Proposal{{"i1", "test-repo"}, {"w1", ""}}, approved: []string{"i1"}}, "w1（ワークスペースルート）"},
		{"提案が増えていない", humanRunCase{pending: []prepare.Proposal{{"i1", "test-repo"}}, before: []prepare.Proposal{{"i1", ""}}}, "増えていない"},
		{"プロジェクト名は大文字小文字を区別しない", humanRunCase{pending: []prepare.Proposal{{"i1", "Test-Repo"}}, approved: []string{"i1"}}, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			rc, st, ran := tc.c.drive(t)
			if tc.reason == "" {
				if rc != Done {
					t.Errorf("rc = %d, halt = %+v", rc, st.Halt)
				}
				return
			}
			if rc != Halted || !strings.Contains(st.Halt.Reasons[0], tc.reason) || len(ran) != 0 {
				t.Errorf("rc = %d, halt = %+v, ran = %v", rc, st.Halt, ran)
			}
		})
	}
}

func TestHumanRunNeedsABaselineAndDoesNotRunTwice(t *testing.T) {
	t.Parallel()
	tm := Terminal{Board: func(string) (Board, error) {
		t.Error("基準が無いのにボードを読んだ")
		return Board{}, nil
	}}
	save, out := func() error { return nil }, func(string) {}
	st := &prepare.State{}
	if rc := tm.Run(st, 0, RunSpec{}, "r", "o", save, out); rc != Halted || !strings.Contains(st.Halt.Reasons[0], "基準が state に無い") {
		t.Errorf("rc = %d, halt = %+v", rc, st.Halt)
	}
	st = &prepare.State{ProposalsBefore: []prepare.Proposal{}, HumanRuns: []prepare.HumanRun{{Step: 1, Status: "started"}}}
	if rc := tm.Run(st, 0, RunSpec{}, "r", "o", save, out); rc != Halted || !strings.Contains(st.Halt.Reasons[0], "打ち始めたまま止まった") {
		t.Errorf("rc = %d, halt = %+v", rc, st.Halt)
	}
}

func TestHumanRunWithoutATerminalPausesAndLeavesNoRecord(t *testing.T) {
	t.Parallel()
	rc, st, _ := humanRunCase{pending: []prepare.Proposal{{"i1", "test-repo"}}, execErr: ErrNoTerminal}.drive(t)
	if rc != Pause || len(st.HumanRuns) != 0 || st.Halt != nil {
		t.Errorf("rc = %d, state = %+v", rc, st)
	}
}

func TestHumanRunTimeoutHalts(t *testing.T) {
	t.Parallel()
	rc, st, _ := humanRunCase{pending: []prepare.Proposal{{"i1", "test-repo"}}, execErr: ErrTimeout}.drive(t)
	if rc != Halted || !strings.Contains(st.Halt.Reasons[0], "秒で終わらなかった") || st.HumanRuns[0].Status != "halted" {
		t.Errorf("rc = %d, state = %+v", rc, st)
	}
}
