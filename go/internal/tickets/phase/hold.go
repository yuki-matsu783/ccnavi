package phase

import (
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/useronly"
)

// 止めたときの文の理由コード。
const (
	CodeReview   = "DENY_PHASE_REVIEW"
	CodeSubagent = "DENY_SUBAGENT_TICKET_OP"
)

// ParallelRequestNote はレビュー準備中・レビュー待ちの間の、新しい依頼についての案内。案内だけで、止めるのは
// その親の cwd からのサブエージェントの起動とシェル実行だけ。止めたときの文と request の出力が共有する。
const ParallelRequestNote = "自分が準備・依頼したレビューの待ち中は、別の親・別の目的の新しい依頼を受けない。" +
	"受けるときは、ワークスペースルートで起動した別のセッションで受けるようユーザに伝える。" +
	"他の会話が待っている親は対象外。" +
	"同じ親のレビュー対応・相談・止める指示・取り消し・次のフェーズの計画・" +
	"動いている子の完了処理は、このセッションで続けてよい。"

const (
	blockedAgent = "サブエージェントの起動"
	blockedShell = "このシェル実行"
)

// HoldReason は止めたときに返す文。段階の名前（レビュー準備中・レビュー待ち）を見出しに置き、段階ごとに次に何を
// すればよいかを言う（依頼済みならユーザを待つ、未依頼なら子の合流を確かめて依頼する）。
func HoldReason(p *Phase, tool, root string) string {
	agent := slices.Contains(config.AgentTools(), tool)
	blocked := blockedShell
	if agent {
		blocked = blockedAgent
	}
	why := "child"
	if p.RiskEscalates() {
		why = "risk"
	} else if p.Planned() {
		why = "plain"
	}
	number := strconv.Itoa(p.Number)
	var said string
	if p.ReviewWaiting() {
		said = texts.Say(root, "DENY_PHASE_REVIEW__WAITING", texts.Values{
			"parent": p.parent, "phase": number, "label": p.Label(), "review_label": p.ReviewLabel(),
			"blocked": blocked, "risk_line": p.RiskLine(),
		}, texts.Variants{"why": why})
	} else {
		said = texts.Say(root, "DENY_PHASE_REVIEW__REQUEST", texts.Values{
			"parent": p.parent, "phase": number, "label": p.Label(), "review_label": p.ReviewLabel(),
			"blocked": blocked, "risk_line": p.RiskLine(), "turn_defined": useronly.TurnDefined,
		}, texts.Variants{"why": why})
	}
	lines := []string{
		hookio.Tagged(CodeReview, "parent: "+p.parent+", phase: "+number+", "+p.ReviewLabel()),
		said,
		ParallelRequestNote,
	}
	if !agent {
		lines = append(lines, useronly.ExemptNote)
	}
	return strings.Join(lines, "\n")
}
