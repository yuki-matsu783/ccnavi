package plan

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

const (
	// phasesFile はユーザが最小のフェーズ定義を書く場所（ワークスペースかプロジェクトのルートからの相対）。
	phasesFile = ".ccnavi/config/project/phases.yml"

	textChildless = "NEXT_OPS_START_CHILDLESS"
)

// MissingPlan は計画の無い親なら理由を書いて真。子と、計画のある親は偽。
func MissingPlan(stderr io.Writer, found *ticket.Ticket) bool {
	if found.IsChild() || found.HasPlan() {
		return false
	}
	where := phasesFile
	if found.Project != "" {
		where = "projects/" + found.Project + "/" + phasesFile
	}
	fmt.Fprintf(stderr, "ccnavi: 親 %s に plan が無いので着手できない（子や子の提案があっても同じ）。"+
		"%s に最小のフェーズ定義（例: work を 1 つ）を書き、親に plan: を追加します。"+
		"phases.yml はユーザが持つ設定なので、書くのはユーザ。"+
		"承認済みの親に plan を追加するには、plan を追加した提案を改版として出し、"+
		"ユーザに承認し直してもらう。"+
		"書き方は `ccnavi-launcher.sh --docs --keyword 提案書式` と `--keyword phases` で引く。\n", found.ID, where)
	return true
}

type missing struct {
	number int
	label  string
}

// childlessPhases は子（承認済みと todo/ の提案の両方）の無いフェーズ。計画の無い親は空。
func childlessPhases(conf *config.Settings, root string, found *ticket.Ticket) []missing {
	if found.IsChild() || !found.HasPlan() {
		return nil
	}
	drafted := map[int]bool{}
	for _, t := range approval.ReadProposals(conf, root, nil).Kept {
		if n, ok := t.ChildPhase(); ok && t.Parent == found.ID && t.State == place.Todo {
			drafted[n] = true
		}
	}
	var out []missing
	for _, p := range phase.Phases(conf, root, found.ID, nil) {
		if len(p.Tickets) == 0 && !drafted[p.Number] {
			out = append(out, missing{p.Number, p.Label()})
		}
	}
	return out
}

// ChildlessNotice は start の成功時に足す案内の行。子の無いフェーズが無ければ空。
func ChildlessNotice(conf *config.Settings, root string, found *ticket.Ticket) []string {
	gaps := childlessPhases(conf, root, found)
	if len(gaps) == 0 {
		return nil
	}
	labels := make([]string, len(gaps))
	for i, g := range gaps {
		labels[i] = g.label
	}
	example := ids.ChildID(found.ID, gaps[0].number, 1)
	return []string{
		"子のない（子の提案もありません）フェーズがある: " + strings.Join(labels, ", "),
		texts.Say(root, textChildless, texts.Values{"example": example, "parent": found.ID}, nil),
	}
}
