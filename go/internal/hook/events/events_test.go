package events

import (
	"bytes"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/tickets/testkit"
)

func TestOverReport(t *testing.T) {
	cases := []struct {
		name   string
		in     audit.Record
		detail string
		rules  []string
	}{
		{"報告の code と rules を detail へ移す",
			audit.Record{Decision: audit.Deny, Code: "POST_VIOLATION", Rules: []string{"guard-a", "guard-b"}, Detail: "uncounted 1"},
			"uncounted 1; report POST_VIOLATION guard-a,guard-b", nil},
		{"rules が無ければ code だけ", audit.Record{Decision: audit.Deny, Code: "POST_VIOLATION"}, "report POST_VIOLATION", nil},
		{"報告が無ければ何もしない", audit.Record{Decision: audit.Allow, Detail: "uncounted 1", Rules: []string{"r"}}, "uncounted 1", []string{"r"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := c.in
			overReport(&r)
			if r.Detail != c.detail || strings.Join(r.Rules, ",") != strings.Join(c.rules, ",") {
				t.Errorf("detail %q rules %v、want %q %v", r.Detail, r.Rules, c.detail, c.rules)
			}
		})
	}
}

func reviewConf(t *testing.T) *config.Settings {
	t.Helper()
	state := testkit.TempRoot(t)
	return &config.Settings{State: state, StopReviewAgent: true, StopReview: string(config.Enable), Tickets: "wip/proposals"}
}

func bash(event hookio.Event, command, id string) hookio.Input {
	return hookio.Input{Event: event, SessionID: "s1", ToolName: config.ToolBash, ToolUseID: id,
		ToolInput: pyjson.NewObject("command", command)}
}

func names(t *testing.T, dir, prefix string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), prefix) {
			out = append(out, e.Name())
		}
	}
	return out
}

// approval-request の呼び出しだけを、識別子ごとに登録する（言及しただけの引数・サブエージェント・未登録は数えない）。
func TestTrackProposalsRegistersCalls(t *testing.T) {
	cases := []struct {
		name    string
		in      hookio.Input
		agent   bool // 判定役を登録しているか
		pending int
	}{
		{"1 本", bash(hookio.PreToolUse, "sh .ccnavi/scripts/ccnavi-ticket.sh approval-request p1", "t1"), true, 1},
		{"1 つの Bash に 2 本", bash(hookio.PreToolUse,
			"sh .ccnavi/scripts/ccnavi-ticket.sh approval-request a1 && sh ccnavi-ticket.sh approval-request b2", "t1"), true, 2},
		{"echo の引数", bash(hookio.PreToolUse, "echo ccnavi-ticket.sh approval-request zzz", "t1"), true, 0},
		{"grep の引数", bash(hookio.PreToolUse, "grep -r 'ccnavi-ticket.sh approval-request' .", "t1"), true, 0},
		{"ほかのサブコマンド", bash(hookio.PreToolUse, "sh ccnavi-ticket.sh status", "t1"), true, 0},
		{"サブエージェント", func() hookio.Input {
			in := bash(hookio.PreToolUse, "sh ccnavi-ticket.sh approval-request p1", "t1")
			in.AgentID = "a1"
			return in
		}(), true, 0},
		{"判定役を登録していない", bash(hookio.PreToolUse, "sh ccnavi-ticket.sh approval-request p1", "t1"), false, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			conf := reviewConf(t)
			conf.StopReviewAgent = c.agent
			trackProposals(conf, c.in)
			if got := names(t, conf.State, stopreview.PrePrefix); len(got) != c.pending {
				t.Errorf("登録 %v、want %d 件", got, c.pending)
			}
			if !c.agent && len(names(t, conf.State, "")) != 0 {
				t.Error("未登録のワークスペースに何か書いた")
			}
		})
	}
}

// 実行前に登録した呼び出しに、実行後に結果ファイルを結ぶ。提案を todo/ へ書いたことも残す。
func TestTrackProposalsBindsResultAndWrites(t *testing.T) {
	conf := reviewConf(t)
	command := "sh .ccnavi/scripts/ccnavi-ticket.sh approval-request p1"
	trackProposals(conf, bash(hookio.PreToolUse, command, "tu1"))
	now := strconv.FormatInt(time.Now().Unix(), 10)
	testkit.Put(t, filepath.Join(conf.State, stopreview.ResultDir, now+"-tu1.kv"),
		"id=p1\nroute=sent\nrc=0\nstarted="+now+"\nended="+now+"\ncommit=c0ffee0123456789\nfile=abc wip/proposals/todo/p1.md\n")
	trackProposals(conf, bash(hookio.PostToolUse, command, "tu1"))
	ops := stopreview.TurnOps(conf.State, "s1", time.Unix(0, 0))
	if len(ops) != 1 || ops[0].Ident != "p1" || ops[0].Route != stopreview.RouteSent || ops[0].ToolUseID != "tu1" {
		t.Fatalf("操作実績 %+v", ops)
	}
	write := hookio.Input{Event: hookio.PostToolUse, SessionID: "s1", ToolName: config.ToolWrite, ToolUseID: "tu2",
		ToolInput: pyjson.NewObject("file_path", "/w/wip/proposals/todo/p2.md")}
	trackProposals(conf, write)
	failed := write
	failed.Event, failed.ToolInput = hookio.PostToolUseFailure, pyjson.NewObject("file_path", "/w/wip/proposals/todo/p3.md")
	trackProposals(conf, failed)
	got := stopreview.TurnWrites(conf.State, "s1", time.Unix(0, 0))
	if len(got) != 1 || got[0].Ident != "p2" {
		t.Errorf("書いた記録 %+v（失敗した書き込みは残さない）", got)
	}
}

// 続きの Stop とみなすのは、判定役を登録していて、stop_hook_active で、差し戻した試行があるときだけ。
func TestAIContinuationRequiresRegistrationAndSentBack(t *testing.T) {
	conf := reviewConf(t)
	now := strconv.FormatInt(time.Now().Unix(), 10)
	sentBack := `{"attempt_id": "x1", "key": "s1:a1", "created": ` + now + `, "state": "sent_back", "sent_at": ` + now + `}`
	in := hookio.Input{Event: hookio.Stop, SessionID: "s1"}
	if aiContinuation(conf, in).ok {
		t.Error("stop_hook_active が偽でも続きにした")
	}
	in.StopHookActive = true
	if aiContinuation(conf, in).ok {
		t.Error("差し戻した試行が無いのに続きにした")
	}
	path := filepath.Join(conf.State, stopreview.AttemptPrefix+"x1.json")
	testkit.Put(t, path, sentBack)
	agent := in
	agent.AgentID = "a1"
	if aiContinuation(conf, agent).ok {
		t.Error("サブエージェントの Stop を続きにした")
	}
	unregistered := *conf
	unregistered.StopReviewAgent = false
	if aiContinuation(&unregistered, in).ok {
		t.Error("判定役を登録していないのに続きにした")
	}
	if got := aiContinuation(conf, in); !got.ok || got.attemptID != "x1" {
		t.Errorf("続き %+v", got)
	}
	if aiContinuation(conf, in).ok {
		t.Error("消費した試行をもう一度続きにした")
	}
}

// 判定役（agent 型 hook）の呼び出しは、PreToolUse 以外のイベントでほかの hook の副作用を動かさない。
func TestJudgeEventsSkipEveryOtherHook(t *testing.T) {
	for _, event := range []hookio.Event{hookio.PostToolUse, hookio.PostToolUseFailure, hookio.SubagentStart,
		hookio.SubagentStop, hookio.Stop, hookio.UserPromptSubmit, hookio.SessionStart} {
		t.Run(string(event), func(t *testing.T) {
			conf := reviewConf(t)
			var stdout, stderr bytes.Buffer
			record := &audit.Record{}
			in := bash(event, "ls", "t1")
			in.AgentID = "hook-agent-6f1c2d3e"
			code, err := Decide(&stdout, &stderr, config.Enable, conf, conf.State, in, record, time.Time{})
			if err != nil || code != config.ExitOK || stdout.Len() != 0 {
				t.Fatalf("code %d err %v stdout %q", code, err, stdout.String())
			}
			if record.Reason != "stop-review-judge" || len(names(t, conf.State, "")) != 0 {
				t.Errorf("reason %q state %v", record.Reason, names(t, conf.State, ""))
			}
		})
	}
}

// 無効モードの Stop も、判定役を待たせないよう検査省略の計画を書く。ほかは記録だけ。
func TestDisabledModeOnlyPlansTheStop(t *testing.T) {
	conf := reviewConf(t)
	root := testkit.TempRoot(t)
	transcript := testkit.Put(t, filepath.Join(root, "t.jsonl"), `{"type":"assistant","uuid":"a1"}`+"\n")
	var stdout, stderr bytes.Buffer
	record := &audit.Record{}
	in := hookio.Input{Event: hookio.Stop, SessionID: "s1", TranscriptPath: transcript, Cwd: root}
	code, err := Decide(&stdout, &stderr, config.Disable, conf, root, in, record, time.Time{})
	if err != nil || code != config.ExitOK || stdout.Len() != 0 {
		t.Fatalf("code %d err %v stdout %q", code, err, stdout.String())
	}
	if record.Decision != audit.Skip || record.Reason != audit.ReasonModeDisabled {
		t.Errorf("record %+v", record)
	}
	plan, ok := stopreview.Store{State: conf.State}.ReadPlan("s1:a1")
	if !ok || plan.IsCandidate || plan.Final != stopreview.FinalSkipped {
		t.Errorf("計画 %+v %v", plan, ok)
	}
}
