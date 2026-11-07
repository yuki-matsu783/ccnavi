package judge

import (
	"io"
	"slices"
	"strings"

	"ccnavi/internal/catalog/docs"
	"ccnavi/internal/hook/askcontext"
	"ccnavi/internal/hook/reasons"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/builtin"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/redact"
	"ccnavi/internal/records/tally"
	"ccnavi/internal/tickets/model/guard"
	"ccnavi/internal/tickets/model/useronly"
)

// dryRunHoldsNote は dry-run でも効く止め（ゲートと補助）の拒否文に添える 1 行。
const dryRunHoldsNote = hookio.Head + "CCNAVI_MODE is dry-run, but this stop still applies: ticket gates and the " +
	"protections that keep them from being bypassed are enforced in every mode except disable."

// shellGuardRuleIDs はシェルから書き込む形を止める組み込みの保護。生の文字列では `>` の個数に対して 2 乗で遅くなるので、
// `>` が上限を超えた生の文字列には当てない（代わりに止める）。`.git` の中を守る土台の 1 本は長さに比例して終わるので入れない。
var shellGuardRuleIDs = []string{
	selfguard.ShellRuleID,
	builtin.ConfigViaBashRuleID,
	guard.StateShellRuleID,
	guard.ApprovedShellRuleID,
	guard.OpsShellRuleID,
}

// gateRuleIDs は dry-run でも止めるゲートのルール。チケット制御が有効なときだけ足される。
var gateRuleIDs = []string{
	guard.StateRuleID,
	guard.StateShellRuleID,
	guard.ApprovedRuleID,
	guard.ApprovedShellRuleID,
	guard.OpsRuleID,
	guard.OpsShellRuleID,
	useronly.TicketApprovalRuleID,
}

// entryWord は書き直しを求める形のうち、生の git や ccnavi の入口を隠しうる語（パスの一部の `/ccnavi/` は数えない）。
var entryWord = pyre.MustCompile(`(?<![\w/.-])(?:git|ccnavi)(?:\.exe)?(?![\w-])|ccnavi-[\w-]+\.sh`, pyre.IgnoreCase)

// judgement は 1 回の判定で持ち回る呼び出しの文脈。
type judgement struct {
	stdout, stderr io.Writer
	mode           config.Mode
	conf           *config.Settings
	root           string
	in             hookio.Input
	record         *audit.Record
	// askContext は確認の前の発言を調べる関数（止める文か空）。
	askContext func() string
}

// refusal は refuse の付け足し。context はルールの additionalContext、updated は起動の入力の更新、
// checkAsk は確認を返す前に直前の発言を調べるか。
type refusal struct {
	kind     config.HoldKind
	context  string
	updated  *pyjson.Object
	checkAsk bool
}

// refuse は拒否か確認を返す。dry-run で一般の保護なら、返していたはずだと言うだけにする。
// 確認を返す前に直前の発言を調べ（checkAsk）、止める文が返れば拒否に替える（確認の本文と kind は残す）。
// 実際に止めた回（enable の deny）は同じ拒否の繰り返しを数える。拒否は dry-run でも失敗の連続に数える。
// 起動の入力の更新は、確認と dry-run の報告に添える。強制する拒否には添えない。
func (j *judgement) refuse(verdict rules.Decision, parts []string, o refusal) (int, error) {
	record := j.record
	if verdict == rules.Ask && o.checkAsk {
		if stop := j.askContext(); stop != "" {
			parts = slices.Concat([]string{askContextRefusal(record, redact.Redact(record.Subject), stop)}, parts)
			verdict = rules.Deny
		}
	}
	// 空行で区切るのは、1 件ずつが閉じた文であることを見た目でも保つため。
	reason := strings.Join(parts, pyfmt.ParagraphBreak)
	decision := audit.Ask
	if verdict == rules.Deny {
		decision = audit.Deny
	}
	streak := ""
	if verdict == rules.Deny {
		streak = FailStreakHint(j.conf, j.root, record.Session, record.Agent)
	}
	// dry-run は通すのが基本だが、ゲートなど dry-run でも止める種類（holds）は強制する。止めた回には、dry-run でも止めたと文に添える。
	holds := config.HoldsInDryRun(o.kind)
	if j.mode == config.DryRun && holds {
		reason += pyfmt.ParagraphBreak + dryRunHoldsNote
	}
	if j.mode == config.DryRun && !holds {
		record.Decision, record.Enforced = decision, false
		would := "asked the user about"
		if verdict == rules.Deny {
			would = "stopped"
		}
		text := config.DryRunWould(would+" this call:\n") + reason
		if streak != "" {
			text += pyfmt.ParagraphBreak + streak
		}
		if o.context != "" {
			text += pyfmt.ParagraphBreak + o.context
		}
		return config.ExitOK, writePass(j.stdout, text, o.updated)
	}

	// ここから先は実際に止める・聞く回。記録の Enforced を真にし、deny の繰り返しと失敗の連続を数えて文に足す。
	record.Decision, record.Enforced = decision, true
	if verdict == rules.Deny {
		if n := tally.CountDeny(j.conf.State, record); n >= tally.DenyThreshold(j.conf.DenyRepeat) {
			reason += pyfmt.ParagraphBreak + tally.DenyNote(tally.RuleOf(record), n)
		}
		if streak != "" {
			reason += pyfmt.ParagraphBreak + streak
		}
	}
	answer := hookio.Ask
	if verdict == rules.Deny {
		answer = hookio.Deny
	}
	// 入力の更新は、確認のときだけ添える。拒否に添えても実行されない。
	r := hookio.PreToolUseResponse{Decision: answer, Reason: reason, Context: o.context}
	if verdict != rules.Deny {
		r.UpdatedInput = o.updated
	}
	return config.ExitOK, hookio.WritePreToolUse(j.stdout, r)
}

// builtinRefusal は組み込みの止め 1 件で拒否する。記録の code と rules を替え、文の頭に通知を並べる。
func (j *judgement) builtinRefusal(notices []string, code, ruleID, text string, kind config.HoldKind) (int, error) {
	j.record.Code, j.record.Rules = code, []string{ruleID}
	return j.refuse(rules.Deny, append(slices.Clip(notices), reasons.BuiltinRefusal(code, ruleID, redact.Redact(j.record.Subject), text)),
		refusal{kind: kind})
}

// writePass は通す・渡す回の応答。判定（permissionDecision）は付けない（付けると allow で権限の確認を飛ばす）。
func writePass(stdout io.Writer, text string, updated *pyjson.Object) error {
	if updated == nil {
		return hookio.WriteContext(stdout, hookio.PreToolUse, text, "")
	}
	return hookio.WritePreToolUse(stdout, hookio.PreToolUseResponse{Context: text, UpdatedInput: updated})
}

// finalKind はルールとチケットの判定を拒否にした回の分類。ids は、土台を落とす前にヒットしたルールの id。
// `>` の上限で上げた拒否は、ゲートの置き場を守る保護を当てていない代わりになるので、ゲートの穴をふさぐためゲートとして扱う。
func finalKind(conf *config.Settings, ids map[string]bool, byTicket, capped bool) config.HoldKind {
	tickets := conf.TicketsEnabled()
	if capped || byTicket {
		if tickets {
			return config.Gate
		}
		return config.General
	}
	if tickets && slices.ContainsFunc(gateRuleIDs, func(id string) bool { return ids[id] }) {
		return config.Gate
	}
	if ids[guard.RawGitRuleID] {
		if tickets {
			return config.Aux
		}
		return config.General
	}
	return config.General
}

// rewriteKind は書き直しを求める拒否の分類。生の git や ccnavi の入口を隠しうる形（実行時に決まるコマンド名、
// サブエージェントの呼び出し、語としての git・ccnavi・保護済みの sh の名前）は補助にする。
func rewriteKind(conf *config.Settings, in hookio.Input, subject string, inner []layer, rewrites []shell.Rewrite) config.HoldKind {
	if !conf.TicketsEnabled() {
		return config.General
	}
	if in.AgentID != "" {
		return config.Aux
	}
	texts := []string{subject}
	for _, l := range inner {
		texts = append(texts, l.text)
	}
	for _, r := range rewrites {
		if r.Form == shell.FormCommandName {
			return config.Aux
		}
		texts = append(texts, r.Text)
	}
	if slices.ContainsFunc(texts, entryWord.Search) {
		return config.Aux
	}
	return config.General
}

// FailStreakHint は失敗か拒否を連続に 1 回数え、案内を出す回ならその文を返す。出さない回と数えられない回は空。
// ツール自体の失敗（PostToolUseFailure）と拒否は、同じ連続として数える。
func FailStreakHint(conf *config.Settings, root, session, agent string) string {
	kind, got := tally.Fail(conf.State, session, agent, tally.StreakThreshold(conf.FailStreakCount))
	if kind == tally.NoNotice {
		return ""
	}
	return docs.FailStreakNote(conf, root, got, kind == tally.First)
}

// askContextCheck は確認の前の発言を調べる関数。非対話のセッション（答える人がいない）とサブエージェントの呼び出し
// （会話記録の置き場が別）は調べない。
func askContextCheck(root string, in hookio.Input) func() string {
	if hookio.Unattended() || in.AgentID != "" {
		return func() string { return "" }
	}
	return func() string { return askcontext.StopText(root, in.TranscriptPath, in.ToolUseID) }
}

// askContextRefusal は直前の発言の検査で止めた文。記録の code を替え、rules には元のルールを残して足す。
func askContextRefusal(record *audit.Record, subject, text string) string {
	record.Code, record.Rules = askcontext.Code, slices.Concat(record.Rules, []string{askcontext.RuleID})
	return reasons.BuiltinRefusal(askcontext.Code, askcontext.RuleID, subject, text)
}

// askSubject は AskUserQuestion の文に出す主題（最初の質問の文）。
func askSubject(in hookio.Input) string {
	if in.ToolInput != nil {
		if questions, ok := in.ToolInput.Value("questions").([]any); ok && len(questions) > 0 {
			if first, ok := questions[0].(*pyjson.Object); ok {
				if text, ok := first.Value("question").(string); ok && text != "" {
					return redact.Redact(text)
				}
			}
		}
	}
	return in.ToolName
}
