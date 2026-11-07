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
	"ccnavi/internal/infra/pyfmt"
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
	subagentNudgeHead = hookio.Head + "SUBAGENT_OUTCOME"
)

// atStop は Stop。このターンで変わった保護領域を、ユーザへ systemMessage で 1 度だけ報告する（dry-run でも。見えたことを
// 言うだけで、モードの約束を何も破らない）。報告のためには止めない。
//
// 止めるのは促しだけで、上から順に最初に当たった 1 つ。finish・依頼までの道・フィードバック計画（ticketNudges）・
// サブエージェントの結果・直接作業の integrate（integrateStep）・`match: Stop` のルールの順に当てる。Stop でモデルに
// 文を届ける経路は、止めて reason に書くことしかないので、dry-run でも止める。止めた回もターンの続きなので、ターン中の印は残す。
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
	report = pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, report, tally.DenyReport(conf.State, in.SessionID, tally.DenyThreshold(conf.DenyRepeat)))
	// AI 判定の差し戻しの次の Stop は、既存の促しを stop_hook_active が偽のときと同じに 1 回だけ動かす
	// （差し戻しのせいで既存の促しが黙らないように）。
	cont := aiContinuation(conf, in)
	asked := in
	if cont.ok {
		asked.StopHookActive = false
	}
	nudge := ""
	for _, step := range ticketNudges {
		if nudge, _ = stateNudge(stderr, conf, root, asked, record, raw, deadline, step); nudge != "" {
			break
		}
	}
	if nudge == "" {
		nudge = subagentNudge(conf, root, in, record)
	}
	if nudge == "" {
		// 前提を満たせず促せない回は、止めずに理由だけを報告へ足す。
		var notice string
		nudge, notice = stateNudge(stderr, conf, root, asked, record, raw, deadline, integrateStep)
		report = pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, report, notice)
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

// nudgeable は Stop の促しを考えてよい回かを返す。促さないのは、stop_hook_active（Stop の hook が続けさせた連鎖の 2 回目以降）、
// サブエージェント、state の置き場が無い回（覚えられないまま止めると毎回止まる）。実行中のサブエージェントが居る間（記録を
// 読めないときも）は、委任して待つ回なので促さず、「1 回だけ」の記録も進めない。
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

// stopScene は状態で決める促しが見る、この Stop の持ち物。
type stopScene struct {
	conf     *config.Settings
	root     string
	in       hookio.Input
	raw      *approval.Raw
	deadline time.Time
}

// nudgeHit は状態で決める促しの当たり。say は止める文を組む（blocked なら止めずに報告へ足す理由を組む）。
type nudgeHit struct {
	found stop.Nudge
	code  string
	// tree は記録の tree に残す名前（チケットの ID かワークツリーの名前）。
	tree string
	// over は同じ Stop の報告を記録の detail へ移すか（overReport）。
	over    bool
	blocked bool
	say     func() string
}

// nudgeStep は状態で決める促し 1 つ。what は促した記録を残せないときの苦情で、何を促したかを言う語。tickets が真なら、
// チケット制御が disable の回は促さない。find は当たらなければ偽。
type nudgeStep struct {
	what    string
	tickets bool
	find    func(s stopScene) (nudgeHit, bool)
}

// ticketNudges はチケットの状態で決める促しを、当てる順に並べたもの。
//   - finish: cwd のツリーのチケットが作業を終えたように見えるのに finish されていない
//   - 依頼までの道: レビューの依頼までの道の途中で終えようとした（最後の発言は読まず、状態だけで決める）
//   - フィードバック計画: フィードバック計画待ちのまま終えようとした
var ticketNudges = []nudgeStep{
	{what: "finish ", tickets: true, find: func(s stopScene) (nudgeHit, bool) {
		u := stop.UnfinishedAtStop(s.conf, s.root, s.in.Cwd, s.raw)
		if u == nil {
			return nudgeHit{}, false
		}
		return nudgeHit{found: u, code: stop.CodeFinishNudge, tree: u.Ticket.ID, over: true,
			say: func() string { return stop.FinishNudge(s.root, u) }}, true
	}},
	{what: "依頼までの道", tickets: true, find: func(s stopScene) (nudgeHit, bool) {
		p := stop.PendingAtStop(s.conf, s.root, s.in.Cwd, s.raw)
		if p == nil {
			return nudgeHit{}, false
		}
		return nudgeHit{found: p, code: stop.CodeRequestNudge, tree: p.Parent.ID,
			say: func() string { return stop.RequestNudge(s.root, p) }}, true
	}},
	{what: "フィードバック計画", tickets: true, find: func(s stopScene) (nudgeHit, bool) {
		w := stop.WaitingAtStop(s.conf, s.root, s.in.Cwd, s.raw)
		if w == nil {
			return nudgeHit{}, false
		}
		return nudgeHit{found: w, code: stop.CodeFeedbackNudge, tree: w.Parent.ID,
			say: func() string { return stop.FeedbackNudge(s.root, w) }}, true
	}},
}

// integrateStep は、直接作業が integrate まで進んでいないときの、打ち方の促し。促せない理由（統合先のツリーが綺麗で
// ないなど）は、止めずに報告へ足す。チケット制御の有無は見ない（直接作業はチケットに結び付かない）。期限を過ぎてから
// git を起こさない。
var integrateStep = nudgeStep{what: "integrate ", find: func(s stopScene) (nudgeHit, bool) {
	d := integrate.DirectAtStop(s.conf, s.root, s.in.Cwd, s.in.SessionID, s.deadline)
	if d == nil {
		return nudgeHit{}, false
	}
	if d.Blocked != "" {
		return nudgeHit{found: d, blocked: true, say: func() string { return integrate.BlockedNotice(d) }}, true
	}
	return nudgeHit{found: d, code: integrate.CodeIntegrateNudge, tree: d.Name, over: true,
		say: func() string { return integrate.IntegrateNudge(s.root, d) }}, true
}}

// stateNudge は状態で決める促し 1 つを当て、止める文と、止めずに報告へ足す理由を返す。同じセッションで同じ状態を
// 促したことがあれば促さない。促した状態は、止める前に覚える（覚えられなければ促さない）。
func stateNudge(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record,
	raw *approval.Raw, deadline time.Time, step nudgeStep) (string, string) {
	if (step.tickets && !conf.TicketsEnabled()) || !nudgeable(conf, in) {
		return "", ""
	}
	hit, ok := step.find(stopScene{conf: conf, root: root, in: in, raw: raw, deadline: deadline})
	if !ok || stop.NudgedBefore(conf.State, in.SessionID, hit.found) || !remembered(stderr, conf, in, hit.found, step.what) {
		return "", ""
	}
	if hit.blocked {
		return "", hit.say()
	}
	if hit.over {
		overReport(record)
	}
	nudged(record, hit.code, hit.tree)
	return hit.say(), ""
}

// subagentNudge は、終わったサブエージェントが範囲外の変更を残した、または範囲を確かめられなかったときの促し（同じ結果では
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

// stopRulesNudge は、`match: Stop` の allow のルールが、このターンの終わりで止めて渡す文を返す。何を言うか・何回に 1 度かは
// 設定が持ち、ここは当てて数えるだけ。ルールは共通レイヤーとワークスペース自身のレイヤーからだけ引き、本文は
// ワークスペースルートの版だけを読む（プロジェクトやワークツリーの版はエージェントが書き換えられるため）。数えを
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
	return codeRuleNudge + ": " + preface + pyfmt.ParagraphBreak + strings.Join(parts, pyfmt.ParagraphBreak)
}

// overReport は、促しで止める回の記録から同じ Stop の報告を detail へ移す。1 回の Stop の記録は 1 行で、decision と
// code は促しが勝つ。上書きするだけだと保護領域の変更を報告したことが消え、rules を残したままだとどれで止めたかが
// 読めなくなる。そこで、報告の code と rules を `report <code> <id,…>` の形で detail に足し、rules を空にする。
func overReport(record *audit.Record) {
	if record.Decision != audit.Deny || record.Code == "" {
		return
	}
	note := strings.TrimRightFunc("report "+record.Code+" "+strings.Join(record.Rules, ","), unicode.IsSpace)
	record.Detail = pyfmt.JoinNonEmpty("; ", record.Detail, note)
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
