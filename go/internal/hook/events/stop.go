package events

import (
	"fmt"
	"io"
	"strings"
	"time"
	"unicode"

	"ccnavi/internal/hook/outcome"
	"ccnavi/internal/hook/subagent"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/policy/ctxfile"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/records/tally"
	"ccnavi/internal/records/turn"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/lifecycle/stop"
)

const (
	// codeRuleNudge は `match: Stop` のルールで止めた回の理由コード。記録の code と、止めた文の頭に出る。
	codeRuleNudge = "NUDGE_STOP_RULE"
	// codeSubagentNudge は終わったサブエージェントの結果で止めた回の理由コード（文面ファイルの識別子も同じ）。
	codeSubagentNudge = "NUDGE_SUBAGENT_OUTCOME"
	// subagentNudgeHead は終わったサブエージェントの結果の促しの頭の行。
	subagentNudgeHead = "[ccnavi] SUBAGENT_OUTCOME"
)

// atStop は Stop。このターンで変わった保護領域をユーザへ systemMessage で 1 度に報告する（dry-run でも。見えたことを
// 言うだけで、モードの約束を何も破らない）。報告のためには止めない。
//
// 止めるのは促しだけで、上から順に最初に当たった 1 つ（finish・依頼までの道・フィードバック計画・サブエージェントの
// 結果・直接作業の integrate・`match: Stop` のルール）。Stop でモデルに文を届ける経路は止めて reason に書くことしか
// 無いので、dry-run でも止める。止めた回もターンの続きなので、ターン中の印は残す。
func atStop(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record, deadline time.Time) (int, error) {
	// 次の発言での受け入れが読む。
	turn.RecordBusy(conf.State, in.SessionID, in.Busy)
	raw := readRaw(conf, root)
	c, err := checker(stderr, conf, root, record, nil, raw)
	if err != nil {
		return config.ExitOK, err
	}
	report := c.AtStop(in, record)
	// 拒否の文面はモデルにしか届かないので、言い換えで回っているかもしれないことをユーザにも 1 度言う。
	report = paragraphs(report, tally.DenyReport(conf.State, in.SessionID, tally.DenyThreshold(conf.DenyRepeat)))
	// AI 判定の差し戻しの次の Stop は、既存の促しを stop_hook_active が偽のときと同じに 1 回だけ動かす
	// （差し戻しのせいで既存の促しが黙らないように）。
	cont := aiContinuation(conf, in)
	asked := in
	if cont.ok {
		asked.StopHookActive = false
	}
	nudge := finishNudge(stderr, conf, root, asked, record, raw)
	if nudge == "" {
		nudge = requestNudge(stderr, conf, root, asked, record, raw)
	}
	if nudge == "" {
		nudge = feedbackNudge(stderr, conf, root, asked, record, raw)
	}
	if nudge == "" {
		nudge = subagentNudge(conf, root, in, record)
	}
	if nudge == "" {
		// 前提を満たせず促せない回は、止めずに理由だけを報告へ足す。
		var notice string
		nudge, notice = integrateNudge(stderr, conf, root, asked, record, deadline)
		report = paragraphs(report, notice)
	}
	if nudge == "" {
		// 上で止める回はルールの促しを数えない（数えを進めて届かない回を作らない）。
		nudge = stopRulesNudge(stderr, conf, root, asked, record)
	}
	// 計画は促しの連鎖の後で確定する（回数を進める副作用で判断が食い違わないように）。
	planStopReview(stderr, conf, root, in, mode, nudge != "", cont, raw)
	if nudge != "" {
		return config.ExitOK, hookio.WriteStopBlock(stdout, nudge, report)
	}
	// ターンの終わり。ここから次の発言までは、ユーザの編集が戻されない。
	turn.End(conf.State, in.SessionID)
	if report != "" {
		return config.ExitOK, hookio.WriteSystemMessage(stdout, report)
	}
	return config.ExitOK, nil
}

// nudgeable は Stop の促しを考えてよい回か。stop_hook_active（Stop の hook が続けさせた連鎖の 2 回目以降）・
// サブエージェント・state の置き場が無い（覚えられないまま止めると毎回止まる）回は促さない。実行中のサブエージェントが
// 居る間（記録を読めないときも）は委任して待つ回なので促さず、「1 回だけ」の記録も進めない。
func nudgeable(conf *config.Settings, in hookio.Input) bool {
	return !in.StopHookActive && in.AgentID == "" && conf.State != "" && !subagent.Running(conf.State, in.SessionID)
}

// remembered は促した状態を記録する。書けなければ、覚えられないまま止めないよう理由を標準エラーに書いて偽。
func remembered(stderr io.Writer, conf *config.Settings, in hookio.Input, n stop.Nudge, what string) bool {
	if failed := stop.RememberNudge(conf.State, in.SessionID, n); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: %sを促した記録を残せないので、促さない: %s\n", what, failed)
		return false
	}
	return true
}

// nudged は促しで止めた回を記録に書く。
func nudged(record *audit.Record, code, tree string) {
	record.Decision, record.Code = audit.Nudge, code
	record.Enforced = true
	record.Tree = tree
}

// finishNudge は cwd のツリーのチケットが、作業を終えたように見えるのに finish されていないときの促し。同じセッションで
// 同じチケットを同じ HEAD のまま促したことがあれば促さない。チケット制御が disable なら何もしない。
func finishNudge(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record,
	raw *approval.Raw) string {
	if !conf.TicketsEnabled() || !nudgeable(conf, in) {
		return ""
	}
	found := stop.UnfinishedAtStop(conf, root, in.Cwd, raw)
	if found == nil || stop.NudgedBefore(conf.State, in.SessionID, found) || !remembered(stderr, conf, in, found, "finish ") {
		return ""
	}
	overReport(record)
	nudged(record, stop.CodeFinishNudge, found.Ticket.ID)
	return stop.FinishNudge(root, found)
}

// requestNudge はレビューの依頼までの道の途中で終えようとしたときの、次の一手の促し。最後の発言は読まず、状態だけで
// 決める。同じ状態（親・フェーズ・残っている子・HEAD）では 1 回だけ。
func requestNudge(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record,
	raw *approval.Raw) string {
	if !conf.TicketsEnabled() || !nudgeable(conf, in) {
		return ""
	}
	found := stop.PendingAtStop(conf, root, in.Cwd, raw)
	if found == nil || stop.NudgedBefore(conf.State, in.SessionID, found) || !remembered(stderr, conf, in, found, "依頼までの道") {
		return ""
	}
	nudged(record, stop.CodeRequestNudge, found.Parent.ID)
	return stop.RequestNudge(root, found)
}

// feedbackNudge はフィードバック計画待ちのまま終えようとしたときの促し。同じ状態（親・HEAD・改版を提案済みか）では
// 1 回だけ。
func feedbackNudge(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record,
	raw *approval.Raw) string {
	if !conf.TicketsEnabled() || !nudgeable(conf, in) {
		return ""
	}
	found := stop.WaitingAtStop(conf, root, in.Cwd, raw)
	if found == nil || stop.NudgedBefore(conf.State, in.SessionID, found) || !remembered(stderr, conf, in, found, "フィードバック計画") {
		return ""
	}
	nudged(record, stop.CodeFeedbackNudge, found.Parent.ID)
	return stop.FeedbackNudge(root, found)
}

// subagentNudge は終わったサブエージェントが範囲外の変更を残した・範囲を確かめられなかったときの促し（同じ結果では
// 1 回だけ）。結果の 1 行ずつはコードが組み、前後の文は文面ファイルが持つ（状態の行の次の 1 行が何をするか）。
func subagentNudge(conf *config.Settings, root string, in hookio.Input, record *audit.Record) string {
	if !conf.TicketsEnabled() || !nudgeable(conf, in) {
		return ""
	}
	lines := outcome.StopLines(conf.State, in.SessionID)
	if len(lines) == 0 {
		return ""
	}
	record.Decision, record.Code = audit.Nudge, codeSubagentNudge
	record.Enforced = true
	said := strings.Split(texts.Say(root, codeSubagentNudge, nil, nil), "\n")
	out := append([]string{subagentNudgeHead, said[0]}, lines...)
	return strings.Join(append(out, said[1:]...), "\n")
}

// integrateNudge は直接作業が integrate まで進んでいないときの、打ち方の促しと、促せない理由（統合先のツリーが綺麗で
// ないなど。止めずに報告へ足す）。チケット制御の有無は見ない（直接作業はチケットに結び付かない）。期限を過ぎてから
// git を起こさない。
func integrateNudge(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record,
	deadline time.Time) (string, string) {
	if !nudgeable(conf, in) {
		return "", ""
	}
	found := integrate.DirectAtStop(conf, root, in.Cwd, in.SessionID, deadline)
	if found == nil || stop.NudgedBefore(conf.State, in.SessionID, found) || !remembered(stderr, conf, in, found, "integrate ") {
		return "", ""
	}
	if found.Blocked != "" {
		return "", integrate.BlockedNotice(found)
	}
	overReport(record)
	nudged(record, integrate.CodeIntegrateNudge, found.Name)
	return integrate.IntegrateNudge(root, found), ""
}

// stopRulesNudge は `match: Stop` の allow のルールが、このターンの終わりで止めて渡す文。何を言うか・何回に 1 度かは
// 設定が持ち、ここは当てて数えるだけ。ルールは共通レイヤーとワークスペース自身のレイヤーからだけ引き、本文は
// ワークスペースルートの版だけを読む（プロジェクトやワークツリーの版はエージェントが書き換えられる）。数えを
// 覚えられない回は言わない（覚えられないまま止めると毎回止まる）。文の頭には振り返りの前置きを必ず付ける。
func stopRulesNudge(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record) string {
	if in.StopHookActive || in.AgentID != "" || conf.State == "" {
		return ""
	}
	group := ruleload.StopRules(conf, root)
	if len(group) == 0 {
		return ""
	}
	o := ctxfile.Options{Bases: []string{root}, QuietWhenUnsure: true, CountsPath: ctxfile.StopPath(conf.State, in.SessionID)}
	var parts, delivered []string
	for _, rule := range group {
		if text, _ := ctxfile.Deliver(stderr, conf.State, in, []*rules.Rule{rule}, o); text != "" {
			parts = append(parts, text)
			delivered = append(delivered, rules.RecordName(rule))
		}
	}
	if len(parts) == 0 {
		return ""
	}
	overReport(record)
	record.Decision, record.Code = audit.Nudge, codeRuleNudge
	record.Enforced = true
	record.Rules = delivered
	preface := texts.Say(root, codeRuleNudge, nil, nil)
	return codeRuleNudge + ": " + preface + "\n\n" + strings.Join(parts, "\n\n")
}

// overReport は、促しで止める回の記録から同じ Stop の報告を detail へ移す。1 回の Stop の記録は 1 行で、decision と
// code は促しが勝つ。上書きするだけだと保護領域の変更を報告したことが消え、rules を残したままだとどれで止めたかが
// 読めなくなるので、報告の code と rules を `report <code> <id,…>` の形で detail に足し、rules を空にする。
func overReport(record *audit.Record) {
	if record.Decision != audit.Deny || record.Code == "" {
		return
	}
	note := strings.TrimRightFunc("report "+record.Code+" "+strings.Join(record.Rules, ","), unicode.IsSpace)
	record.Detail = joined("; ", record.Detail, note)
	record.Rules = nil
}

// continuation は AI 判定の差し戻しで続いた Stop で消費した試行（ok が偽なら続きではない）。
type continuation struct {
	attemptID string
	ok        bool
}

// aiContinuation は、この Stop が AI 判定の差し戻しで続いた回なら、その試行を消費して返す。
// 消費はイベントの記録に書かない（Python は記録の置き場を結ぶ前に消費していた）。
func aiContinuation(conf *config.Settings, in hookio.Input) continuation {
	if !in.StopHookActive || !conf.StopReviewAgent || conf.State == "" || in.AgentID != "" || in.SessionID == "" {
		return continuation{}
	}
	id, ok := stopreview.Store{State: conf.State}.ConsumeContinuation(in.SessionID, time.Now())
	return continuation{id, ok}
}
