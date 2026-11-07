package subagent

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/hook/outcome"
	"ccnavi/internal/hook/post"
	"ccnavi/internal/hook/reasons"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase/scope"
)

// 終わりの文。
const (
	scopeHead = "[ccnavi] %s: 子チケットの範囲の外に変更が残っています（" +
		"%d 件）。範囲の中へ戻すか、必要なら親に伝えて次のチケットにしてください。"
	scopeLine      = "  %s: %s%s  範囲は %s"
	emptyArea      = "(空)"
	unreadableLine = "ccnavi: %s のワークツリーを読めない: %s\n"
	uncheckedHead  = "unchecked: "
	limitBlocked   = "（チケットを信頼できない: %s）"
	limitType      = "（定義 %s の上限の外）"
	limitParent    = "（親 %s の範囲の外）"
	outcomeFail    = "ccnavi: サブエージェントの終了結果を保存できませんでした: %s\n"
	uncheckedFail  = "ccnavi: 範囲を確かめられなかった印を保存できませんでした: %s\n"
	bounceFail     = "ccnavi: 差し戻しの回数を保存できませんでした: %s\n"
)

// finding は担当の子 1 本の範囲の外のパス 1 つ。
type finding struct {
	child *ticket.Ticket
	scope.Finding
}

func (f finding) label() string { return f.child.ID + ":" + f.Path }

// AtStop は SubagentStop。実行中の印を外し、範囲の外の変更の差し戻しを判定する。差し戻した（ExitBlock）ときは
// サブエージェントが続けるので印を残す。error は応答を書けなかったとき（印は外す）。
func AtStop(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record) (int, error) {
	code, err := judgeStop(stdout, stderr, mode, conf, root, in, record)
	if code != config.ExitBlock && conf.State != "" && in.AgentID != "" {
		fsio.Remove(markPath(conf.State, in.SessionID, in.AgentID, runningSuffix))
	}
	return code, err
}

// judgeStop は範囲の外の変更が残っていれば 1 回だけ差し戻す。見るのは cwd が子のワークツリーならその子、親のワークツリー
// ならその親の開いている子の全部で、範囲は実行前チェックと同じく親の範囲と定義の上限で切り詰める（scope.Findings）。
// 担当が分からないときは差し戻さず、親に知らせる。差し戻すのは enable の初めての回だけ。
func judgeStop(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record) (int, error) {
	record.Decision, record.Enforced = audit.Allow, true
	if !conf.TicketsEnabled() {
		return config.ExitOK, nil
	}
	here, found := cwdTree(conf, root, in)
	copies, _ := approval.Scan(conf, root, false, nil)
	index := approval.ByID(copies)
	var bound *ticket.Ticket
	var targets []*ticket.Ticket
	if found && !here.IsMain() {
		if t, ok := tree.Lookup(index, here.Name); ok {
			bound, targets = t, childrenOf(t, copies)
		}
	}
	// 親のツリーから起動された体はどの子の担当か分からないので、全部の子を調べても差し戻さない。
	attributed := bound != nil && bound.IsChild()
	var findings []finding
	var unchecked []string
	for _, child := range targets {
		outside, err := scope.Findings(conf, root, child, index[child.Parent])
		if err != nil {
			fmt.Fprintf(stderr, unreadableLine, child.ID, err)
			unchecked = append(unchecked, child.ID)
			continue
		}
		for _, f := range outside {
			findings = append(findings, finding{child, f})
		}
	}
	who := in.AgentID
	if who == "" && found {
		who = here.Name
	}
	if len(unchecked) > 0 {
		// 読めないのは子の作業のせいとは限らず、差し戻しても直せないことがある。この出力は親に届かないので、親の
		// PostToolUse で言うための印を置く。範囲の外が他に無ければ、判定に至らなかった回として残す。
		record.Detail = uncheckedHead + strings.Join(unchecked, ", ")
		if len(findings) == 0 {
			record.Decision, record.Reason, record.Enforced = audit.Skip, post.ReasonWorktreeUnreadable, false
		}
		if conf.State != "" {
			if failed := fsio.WriteText(markPath(conf.State, in.SessionID, who, uncheckedSuffix),
				markText(in.SessionID, unchecked), fsio.NativeNewline, fsio.Plain); failed != "" {
				fmt.Fprintf(stderr, uncheckedFail, failed)
			}
		}
	}
	// 着手のあとにフローが書き換わっていれば知らせる（行き先を追えないシェルの書き込みは止められない）。
	var changed []string
	for _, c := range targets {
		if n := flow.ChangedNotice(conf, root, c); n != "" {
			changed = append(changed, n)
		}
	}
	note := strings.Join(changed, "\n")
	child := ""
	if attributed {
		child = bound.ID
	}
	if len(findings) == 0 {
		kind := outcome.OK
		if len(unchecked) > 0 && attributed {
			kind = outcome.Unchecked
		} else if bounced(conf.State, in.SessionID, who) {
			kind = outcome.Fixed
		}
		recordOutcome(stderr, conf, in, who, outcome.Entry{Kind: kind, Child: child})
		return context(stdout, note, note)
	}

	paths := make([]string, len(findings))
	for i, f := range findings {
		paths[i] = f.label()
	}
	record.Decision, record.Paths = audit.Deny, paths
	record.Rules, record.Code = []string{reasons.TicketRule}, post.CodeTicketScope
	lines := []string{fmt.Sprintf(scopeHead, post.CodeTicketScope, len(findings))}
	for _, f := range findings[:min(len(findings), post.ReportLimit)] {
		area := strings.Join(append(f.child.Paths(rules.Allow), f.child.Paths(rules.Ask)...), ", ")
		if area == "" {
			area = emptyArea
		}
		lines = append(lines, fmt.Sprintf(scopeLine, f.child.ID, f.Path, limitNote(f), area))
	}
	text := strings.Join(append(lines, changed...), "\n")
	already := bounced(conf.State, in.SessionID, who)
	if !attributed {
		// 別の子の変更を、関係ない体に直させない。
		record.Enforced = false
		recordOutcome(stderr, conf, in, who, outcome.Entry{Kind: outcome.Unattributed, Paths: paths})
		return context(stdout, note, note)
	}
	if mode == config.Enable && !already {
		if conf.State != "" {
			if failed := fsio.WriteText(markPath(conf.State, in.SessionID, who, bouncedSuffix), fsio.Stamp(),
				fsio.NativeNewline, fsio.Plain); failed != "" {
				fmt.Fprintf(stderr, bounceFail, failed)
			}
		}
		record.Enforced = true
		// 続けさせる回なので結果はまだ確定しない（次の終わりで直ったかが決まる）。
		recordOutcome(stderr, conf, in, who,
			outcome.Entry{Kind: outcome.Outside, Child: child, Bounces: 1, Paths: paths, Provisional: true})
		fmt.Fprintln(stderr, text)
		return config.ExitBlock, nil
	}
	record.Enforced = false
	bounces := 0
	if already {
		bounces = 1
	}
	recordOutcome(stderr, conf, in, who, outcome.Entry{Kind: outcome.Outside, Child: child, Bounces: bounces, Paths: paths})
	return context(stdout, text, note)
}

// context は SubagentStop で文を渡す。text が空なら何も書かない。
func context(stdout io.Writer, text, system string) (int, error) {
	if text == "" {
		return config.ExitOK, nil
	}
	if err := hookio.WriteContext(stdout, hookio.SubagentStop, text, system); err != nil {
		return config.ExitOK, fmt.Errorf("SubagentStop の応答: %w", err)
	}
	return config.ExitOK, nil
}

// limitNote は子の範囲の中なのに外とされたパスにつける、止めた上限の名指し。つけないと、承認で見た範囲の中を
// 書いたのに差し戻された理由が読めず、戻し先が分からない。
func limitNote(f finding) string {
	switch {
	case f.Limit == scope.LimitBlocked:
		return fmt.Sprintf(limitBlocked, f.child.Blocked)
	case f.Limit == scope.LimitType && f.Type != nil:
		return fmt.Sprintf(limitType, f.Type.Title)
	case f.Limit == scope.LimitParent:
		return fmt.Sprintf(limitParent, f.child.Parent)
	}
	return ""
}

// recordOutcome は終わり方を残す。残せなくても止めない（言うだけ）。
func recordOutcome(stderr io.Writer, conf *config.Settings, in hookio.Input, who string, e outcome.Entry) {
	if conf.State == "" || who == "" {
		return
	}
	if failed := outcome.Write(conf.State, in.SessionID, who, e); failed != "" {
		fmt.Fprintf(stderr, outcomeFail, failed)
	}
}

// AfterError は SubagentStop の処理が内部エラーで終わったとき、範囲を確かめられなかった印を置く。内部エラーの知らせ
// （systemMessage）は親に届かないので、読めない子があった回と同じ印で親の PostToolUse か UserPromptSubmit に言わせる。
// どの子を見るはずだったかは分からないので中身は空。先に書いた印と確定した結果は上書きしない。
func AfterError(stderr io.Writer, conf *config.Settings, root string, in hookio.Input) {
	if conf.State == "" || !conf.TicketsEnabled() {
		return
	}
	who := in.AgentID
	if who == "" {
		if here, found := cwdTree(conf, root, in); found {
			who = here.Name
		}
	}
	file := markPath(conf.State, in.SessionID, who, uncheckedSuffix)
	if fsio.Exists(file) {
		return
	}
	if failed := fsio.WriteText(file, markText(in.SessionID, nil), fsio.NativeNewline, fsio.Plain); failed != "" {
		fmt.Fprintf(stderr, uncheckedFail, failed)
	}
	if existing := outcome.Of(conf.State, in.SessionID, who); existing == nil || !existing.Final {
		recordOutcome(stderr, conf, in, who, outcome.Entry{Kind: outcome.Unchecked})
	}
}
