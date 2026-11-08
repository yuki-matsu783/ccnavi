package subagent

import (
	"fmt"
	"slices"
	"strings"

	"ccnavi/internal/hook/outcome"
	"ccnavi/internal/hook/post"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
)

// 親に言う文。
const (
	keyAgentID     = "agentId"
	ignoredBounce  = hookio.Head + post.CodeOutOfScope + ": サブエージェント %s は範囲外の変更を差し戻されたまま終わっています。合流する前に、その子のワークツリーの範囲外の変更を確かめてください。"
	uncheckedError = hookio.Head + "%s: サブエージェント %s の終わりにccnavi が内部エラーで止まり、子チケットの範囲外の変更を確かめていません。合流する前に、その子のワークツリーを確かめてください。"
	uncheckedRead  = hookio.Head + "%s: サブエージェント %s の終わりに子チケット %s のワークツリーを読めず、範囲外の変更を確かめていません。合流する前に、そのワークツリーを確かめてください。"
)

// launchedAgent は Agent（Task）の PostToolUse なら起動したサブエージェントの agentId。違えば空。
func launchedAgent(in hookio.Input) string {
	if !slices.Contains(config.AgentTools(), in.ToolName) {
		return ""
	}
	if v := in.ToolResponse.Value(keyAgentID); pyfmt.Truthy(v) {
		return pyfmt.Str(v)
	}
	return ""
}

// IgnoredBounce は、親の Agent の PostToolUse で、終わったサブエージェントが差し戻されたまま終わっていたならその旨。
// 差し戻しの印は消す。言うのは終わり方が Outside か、記録が無いときだけ（直った・確かめられなかったは言わない。後者は
// UncheckedScope が言う）。差し戻しは 1 回きりなので、2 度目の終了は何も言われずに通っている。
func IgnoredBounce(state string, in hookio.Input) string {
	agent := launchedAgent(in)
	if agent == "" || !bounced(state, in.SessionID, agent) {
		return ""
	}
	fsio.Remove(markPath(state, in.SessionID, agent, bouncedSuffix))
	if found := outcome.Of(state, in.SessionID, agent); found != nil &&
		slices.Contains([]outcome.Kind{outcome.OK, outcome.Fixed, outcome.Unchecked}, found.Kind) {
		return ""
	}
	return fmt.Sprintf(ignoredBounce, agent)
}

// UncheckedScope は、終わったサブエージェントの子の範囲を SubagentStop で確かめられなかったならその旨。印は読んだら
// 消し、同じことを Stop で促さない。読むのは親の Agent の PostToolUse（その agentId の印）と、親（agent_id の無い
// 呼び出し）のどのツールの PostToolUse と UserPromptSubmit（このセッションに残った印の全部）。バックグラウンドの
// サブエージェントは Agent の PostToolUse が SubagentStop より先に来るので、そこだけで読むと知らせが落ちる。
// サブエージェントの中では読まない（その子が親でない印を消すと、親に届かなくなる）。
func UncheckedScope(state string, in hookio.Input) string {
	if state == "" {
		return ""
	}
	var marks []mark
	if agent := launchedAgent(in); agent != "" {
		marks = append(marks, mark{agent, markPath(state, in.SessionID, agent, uncheckedSuffix)})
	}
	if in.AgentID == "" {
		left, _ := sessionMarks(state, in.SessionID, uncheckedSuffix)
		marks = append(marks, left...)
	}
	var said []string
	seen := map[string]bool{}
	for _, m := range marks {
		if seen[m.file] {
			continue
		}
		seen[m.file] = true
		tickets, ok := readMark(m.file, in.SessionID)
		if !ok {
			continue
		}
		fsio.Remove(m.file)
		outcome.MarkNudged(state, in.SessionID, m.who)
		if len(tickets) == 0 {
			// SubagentStop が内部エラーで終わった回。どの子を見るはずだったかも分からない。
			said = append(said, fmt.Sprintf(uncheckedError, string(post.ReasonWorktreeUnreadable), m.who))
			continue
		}
		said = append(said, fmt.Sprintf(uncheckedRead, string(post.ReasonWorktreeUnreadable), m.who, strings.Join(tickets, ", ")))
	}
	return strings.Join(said, pyfmt.ParagraphBreak)
}

// Notices は親に言う終わり方の知らせ（担当の子が分からなかった体）と、書き終えたあとに outcome.Confirm へ渡す結果。
// 読むのは親（agent_id の無い呼び出し）だけ。入れ子の体の中で読むと、親に届かない。
func Notices(state string, in hookio.Input) (string, []*outcome.Outcome) {
	if state == "" || in.AgentID != "" {
		return "", nil
	}
	return outcome.Notices(state, in.SessionID)
}
