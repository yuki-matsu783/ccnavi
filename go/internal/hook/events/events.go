package events

import (
	"io"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/hook/judge"
	"ccnavi/internal/hook/stopjudge"
	"ccnavi/internal/hook/subagent"
	"ccnavi/internal/hook/turngate"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/records/turn"
	"ccnavi/internal/tickets/lifecycle/branch"
)

// Decide はイベントごとの処理に振り分け、hook の終了コードを返す。error は、下の部品が想定外に失敗したとき、または
// 応答を書けなかったときに返る（入口が内部エラーの応答にする）。deadline は判定の期限（起動時刻 + config.HookDeadline）。
// 呼ぶ前に record.Subject を rules.SubjectOf で埋めておく。
func Decide(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record, deadline time.Time) (int, error) {
	if mode == config.Disable {
		record.Decision, record.Reason = audit.Skip, audit.ReasonModeDisabled
		if in.Event == hookio.Stop {
			// 判定役を登録していても待たせない（検査省略の計画を書く）。
			planStopReview(stderr, conf, root, in, mode, false, continuation{}, nil)
		}
		return config.ExitOK, nil
	}
	if stopjudge.IsJudge(conf, in) {
		// 判定役の呼び出しは、ほかの hook の副作用を動かさない。
		if in.Event == hookio.PreToolUse {
			stopjudge.Before(stdout, mode, conf, in, record)
		} else {
			record.Decision, record.Reason = audit.Skip, stopjudge.ReasonJudge
		}
		return config.ExitOK, nil
	}
	if in.Event == hookio.PreToolUse && stopjudge.Before(stdout, mode, conf, in, record) {
		return config.ExitOK, nil
	}
	trackProposals(conf, in)
	switch in.Event {
	case hookio.PreToolUse, hookio.PostToolUse, hookio.PostToolUseFailure:
		// サブエージェントの呼び出しも同じターンの中。
		turn.Touch(conf.State, in.SessionID, time.Now())
	}
	switch in.Event {
	case hookio.PreToolUse:
		code, err := judge.Before(stdout, stderr, mode, conf, root, in, record, deadline)
		if err != nil {
			return code, err
		}
		if record.Decision == audit.Deny && record.Enforced {
			// 止めた呼び出しは走らない。実行前に残したゲートの記録を消す。
			turngate.ForgetCall(conf.State, in.SessionID, in.ToolUseID)
		}
		return code, nil
	case hookio.PostToolUse:
		return after(stdout, stderr, mode, conf, root, in, record)
	case hookio.PostToolUseFailure:
		return afterFailure(stdout, stderr, mode, conf, root, in, record)
	case hookio.SessionStart:
		return atStart(stdout, stderr, mode, conf, root, in, record, deadline)
	case hookio.UserPromptSubmit:
		return atPrompt(stdout, stderr, mode, conf, root, in, record)
	case hookio.Stop:
		return atStop(stdout, stderr, mode, conf, root, in, record, deadline)
	case hookio.SubagentStart:
		return subagent.AtStart(stdout, stderr, conf, root, in, record)
	case hookio.SubagentStop:
		return subagent.AtStop(stdout, stderr, mode, conf, root, in, record)
	}
	// 想定していない登録が作業を止めてはいけない。
	record.Decision, record.Reason = audit.Skip, audit.ReasonEventNotChecked
	return config.ExitOK, nil
}

// trackProposals は approval-request の操作実績とターンの開始を state に残す（Stop の AI 判定の材料）。agent hook を
// 登録していなければ何もしない。サブエージェントの呼び出しは数えない（approval-request はサブエージェントから呼べない）。
func trackProposals(conf *config.Settings, in hookio.Input) {
	if !conf.StopReviewAgent || conf.State == "" || in.SessionID == "" || in.AgentID != "" {
		return
	}
	now := time.Now()
	switch in.Event {
	case hookio.UserPromptSubmit:
		if in.FromBackgroundTask() {
			return
		}
		stopreview.StartTurn(conf.State, in.SessionID, now)
		var labels []string
		for _, r := range branch.PromptRefs(in.Prompt) {
			labels = append(labels, r.Label())
		}
		stopreview.NoteRefs(conf.State, in.SessionID, labels, now)
	case hookio.PreToolUse:
		command, ok := in.ToolInput.Value("command").(string)
		if in.ToolName != config.ToolBash || !ok {
			return
		}
		for _, p := range shell.PlacedCommands(command, false) {
			if ident := stopreview.ProposeIdent(p.Words); ident != "" {
				stopreview.Register(conf.State, in.SessionID, in.ToolUseID, ident, now)
			}
		}
	case hookio.PostToolUse, hookio.PostToolUseFailure:
		stopreview.Bind(conf.State, in.ToolUseID, now)
		path, ok := in.ToolInput.Value("file_path").(string)
		if in.Event != hookio.PostToolUse || !slices.Contains(config.WrittenFileTools, in.ToolName) || !ok {
			return
		}
		if ident := stopreview.WrittenIdent(path, conf.Tickets); ident != "" {
			stopreview.NoteWritten(conf.State, in.SessionID, ident, path, now)
		}
	}
}

// joined は空でない文を sep でつなぐ。
func joined(sep string, parts ...string) string {
	var kept []string
	for _, p := range parts {
		if p != "" {
			kept = append(kept, p)
		}
	}
	return strings.Join(kept, sep)
}

// paragraphs は空でない文を空行でつなぐ（応答の文の区切り）。
func paragraphs(parts ...string) string { return joined("\n\n", parts...) }
