package childnote

import (
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/sync/mirror"
)

// ruleLines は担当の案内に必ず足す行。サブエージェントの Bash は cd がコマンドをまたいで続かず、担当外の
// ワークツリーと、push・チケットの状態の操作は触らせない（メインが行う）。
var ruleLines = []string{
	"  Bash の cd はコマンドをまたいで続かない。絶対パスか `cd <パス> && …` の形で打つ。",
	"  担当外のワークツリーに触れない。",
	"  push とチケットの状態を動かす操作はしない（メインが行う）。",
}

// RuleLines は担当の案内に必ず足す行（写し）。
func RuleLines() []string { return slices.Clone(ruleLines) }

// Block は子 1 本ぶんの案内。Lines は全部、Brief はそのうちフローの行、Notice はフローの変更の知らせ（無ければ空）。
type Block struct {
	Lines  []string
	Brief  []string
	Notice string
}

// ChildBlock は子 1 本について渡す行（ワークツリーの場所と有無、未完了の先行、フェーズの定義の案内、範囲、フロー）。
// フローの手順は budget 文字まで。full が偽ならフローはファイルの 1 行だけ（どの子の担当かが決まらないとき）。
// フェーズの定義は親に固定したコピー（phases:）から引き、phases.yml は読まない。
func ChildBlock(conf *config.Settings, root string, t *ticket.Ticket, index map[string]*ticket.Ticket,
	preds approval.Pool, budget int, full bool) Block {
	where := tree.WorktreePath(root, t.ID)
	state := "ワークツリー無し（範囲は有効にならない）"
	if ospath.IsDir(where) {
		state = "ワークツリーあり"
	}
	var waiting []string
	for _, p := range approval.UnmetPredecessors(t, preds) {
		waiting = append(waiting, p.Ticket+"（"+p.Label()+"）")
	}
	label := strconv.Itoa(t.Phase)
	hint := ""
	var item *ticket.PlanItem
	parent := index[t.Parent]
	if parent != nil {
		item = parent.ItemAt(t.Phase)
	}
	if item != nil {
		if pt := phases.TypesOf(parent).Get(item.Type); pt != nil {
			label += ": " + pt.Title
			if pt.Agent != "" {
				hint = " 定義の案内: エージェント " + pt.Agent
			}
			if skill := mirror.Installed(root, pt.Skill); skill != "" {
				hint += " 最初に Skill ツールで " + skill + " を呼ぶ"
			}
		}
	}
	head := "  " + t.ID + "（親 " + t.Parent + "、フェーズ " + label + "）: " + where + " [" + state + "]"
	if len(waiting) > 0 {
		head += " 先行が未完了: " + strings.Join(waiting, ", ")
	}
	block := Block{Lines: []string{head + hint}}
	for _, section := range rules.Sections {
		if paths := t.Paths(section); len(paths) > 0 {
			block.Lines = append(block.Lines, "    "+string(section)+": "+strings.Join(paths, ", "))
		}
	}
	block.Brief = flow.Briefing(conf, root, t, budget, full)
	block.Lines = append(block.Lines, block.Brief...)
	block.Notice = flow.ChangedNotice(conf, root, t)
	return block
}

// StageLines は子の親ごとに、親の局面を 1 行ずつ返す（局面の文は phase.Stage のまま）。計画の無い親には何も出さない。
func StageLines(conf *config.Settings, root string, index map[string]*ticket.Ticket, children []*ticket.Ticket) []string {
	sorted := slices.SortedFunc(slices.Values(children), func(a, b *ticket.Ticket) int { return strings.Compare(a.ID, b.ID) })
	var parents []*ticket.Ticket
	for _, child := range sorted {
		if parent := index[child.Parent]; parent != nil && !slices.Contains(parents, parent) {
			parents = append(parents, parent)
		}
	}
	if len(parents) == 0 {
		return nil
	}
	// 承認済みチケットの置き場は 1 度だけ読む。
	raw := approval.ReadRaw(conf, root)
	var lines []string
	for _, parent := range parents {
		if text := phase.Stage(conf, root, parent, raw); text != "" {
			lines = append(lines, "  親 "+parent.ID+" の局面: "+text)
		}
	}
	return lines
}
