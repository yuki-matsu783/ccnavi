package judge

import (
	"strings"

	"ccnavi/internal/hook/reasons"
	"ccnavi/internal/hook/shellguard"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase/scope"
)

// workspaceLabel は、ワークスペースから切った（プロジェクトの無い）ツリーやチケットを文で呼ぶ名前。
const workspaceLabel = "(workspace)"

// scanned は承認済みチケットを 1 回の判定で 1 度だけ読むための持ち回り。読んだかを have で分ける
// （読んで 0 件と、まだ読んでいないを分ける）。
type scanned struct {
	tickets []*ticket.Ticket
	have    bool
}

func (s *scanned) read(conf *config.Settings, root string, raw *approval.Raw) {
	if !s.have {
		s.tickets, _ = approval.Scan(conf, root, false, raw)
		s.have = true
	}
}

// projectMismatch は、ワークツリーの元リポジトリと、そこに結び付く承認済みチケットの project が違えば、その理由の文。
// 範囲の宣言ではなく取り違えなので、ルールが allow と言っていても通さない。子は親から継ぐ。
// index はワークツリーの中への書き込みでは必ず読んである。
func projectMismatch(root string, t tree.Tree, full string, index map[string]*ticket.Ticket) string {
	if t.IsMain() {
		return ""
	}
	// 範囲の判定と同じく、大文字小文字を区別せずに引く。ここだけ厳密に引くと取り違えの検査を素通りする。
	tk, ok := tree.Lookup(index, t.Name)
	if !ok {
		return ""
	}
	owner := tk.Project
	if tk.IsChild() {
		if parent := index[tk.Parent]; parent != nil {
			owner = parent.Project
		}
	}
	if owner == t.Project {
		return ""
	}
	return strings.Join([]string{
		hookio.Cited(reasons.CodeTicketProject, tk.Path),
		hookio.SubjectPrefix + full,
		texts.Say(root, reasons.CodeTicketProject, texts.Values{
			"ticket":   tk.ID,
			"title":    tk.Title,
			"owner":    orWorkspace(owner),
			"cut_from": orWorkspace(t.Project),
			"worktree": t.Name,
		}, nil),
	}, "\n")
}

func orWorkspace(project string) string {
	if project == "" {
		return workspaceLabel
	}
	return project
}

// flowLock は、着手中の子のフローへの書き込みなら、その理由の文を返す。当てるのは次の 3 通りのパス。解決したパス（full）、
// 解く前のパス、ディレクトリだけを解決したパス。どれかが置き場に当たれば止める（リンク越しでも止める向きだけ）。
// ハードリンクはパスに置き場が出ないので、名前が 2 つ以上あるときだけ中身（inode）で見る。
func flowLock(conf *config.Settings, root string, in hookio.Input, full string, s *scanned) string {
	value := ""
	if field := rules.SubjectFieldOf(in.ToolName); field != "" {
		value = in.Field(field)
	}
	if in.ToolName == config.ToolNotebookEdit && value == "" {
		value = in.Field("file_path")
	}
	spelled := fsio.SpelledPath(value, in.Cwd)
	parent := fsio.ParentResolved(spelled)
	for _, path := range pyfmt.NonEmpty([]string{full, spelled, parent}, pyfmt.DropRepeats) {
		at, ok := flow.Locate(conf, root, path)
		if !ok {
			continue
		}
		s.read(conf, root, nil)
		if child := flow.LockHit(s.tickets, at); child != nil {
			return flow.LockedMessage(root, conf, child, path)
		}
	}
	target := full
	if target == "" {
		target = spelled
	}
	if target != "" && flow.HardLinked(target) {
		s.read(conf, root, nil)
		if child := flow.InodeHit(conf, root, s.tickets, target); child != nil {
			return flow.LockedMessage(root, conf, child, target)
		}
	}
	return ""
}

// ruleHit は、ルールがヒットしていたときの（記録の名前, タイプ）。チケットがそれより厳しいときに文で名指しする。
type ruleHit struct {
	name     string
	decision rules.Decision
}

// ticketOutcome はチケットが承認された範囲について言うこと。code は記録の文面を呼び手が決められないとき
// （範囲の外ではなく、チケット自体が信頼できない・親のワークツリー）だけ入る。
type ticketOutcome struct {
	decision rules.Decision
	text     string
	code     string
}

// ticketVerdict は、チケットが承認された範囲について何を言うかを返す。キーはファイルの行き先 t（cwd で置き換える前のツリー）で、
// cwd も agent_id も使わない。範囲の中は allow、ask の項は ask、範囲の外と deny の項は deny。子は親の範囲とフェーズ定義の
// 上限で切り詰める（scope.Of）。チケットの置き場（提案と承認済み）には何も言わない。範囲から出る方法が無くなるため。
func ticketVerdict(conf *config.Settings, root, tool, full string, t *tree.Tree, index map[string]*ticket.Ticket, hit *ruleHit) ticketOutcome {
	if !conf.TicketsEnabled() || !isWriteTool(tool) || t == nil || t.IsMain() {
		return ticketOutcome{}
	}
	tk, ok := tree.Lookup(index, t.Name)
	if !ok {
		return ticketOutcome{}
	}
	rel := tree.Relative(*t, full)
	if place.IsUnscoped(rel, conf.Tickets, conf.Approved) {
		return ticketOutcome{}
	}
	head := func(code, problem string) []string {
		lines := []string{
			hookio.Cited(code, tk.Path),
			hookio.SubjectPrefix + full,
			"ticket: " + tk.ID + " (" + tk.Title + "), worktree " + t.Name,
		}
		if problem != "" {
			lines = append(lines, "problem: "+problem)
		}
		return lines
	}
	// 親のワークツリーは作業の場ではない。成果は子で作り、子の finish で合流する。親かどうかは、索引と
	// ワークツリー自身のチケットのどちらかが親と言えば親とする（食い違うときは止める側に倒す）。信頼できない（blocked）
	// チケットは、下の範囲の判定で止める。
	isParent := !tk.IsChild()
	if !isParent {
		role, _ := shellguard.TicketRole(conf, root, t.Name)
		isParent = role == shellguard.RoleParent
	}
	if isParent && tk.Blocked == "" {
		lines := append(head(reasons.CodeTicketParent, ""), texts.Say(root, reasons.CodeTicketParent, texts.Values{
			"ticket": tk.ID, "title": tk.Title, "worktree": t.Name, "path": full, "worktree_path": t.Root,
		}, nil))
		return ticketOutcome{rules.Deny, strings.Join(lines, "\n"), reasons.CodeTicketParent}
	}
	var parent *ticket.Ticket
	if tk.IsChild() {
		parent = index[tk.Parent]
	}
	// 定義は親に固定したコピーから引く（承認のあとに phases.yml を直しても、進行中の子の範囲は変わらない）。
	found := scope.Of(tk, parent, scope.TypeFor(tk, parent), rel)
	if found.Decision == rules.Allow {
		return ticketOutcome{decision: rules.Allow}
	}
	if found.Limit == scope.LimitBlocked {
		// 範囲の外ではなく、チケット自体が信頼できない。範囲を見せても直せないので、引っかかった検査を名指しする。
		lines := append(head(reasons.CodeTicketBlocked, tk.Blocked), texts.Say(root, reasons.CodeTicketBlocked,
			texts.Values{"ticket": tk.ID, "title": tk.Title, "worktree": t.Name}, nil))
		return ticketOutcome{rules.Deny, strings.Join(lines, "\n"), reasons.CodeTicketBlocked}
	}

	lines := []string{
		hookio.SubjectPrefix + full,
		"ticket: " + tk.ID + " (" + tk.Title + "), worktree " + t.Name,
		"scope: " + tk.ScopeLine(),
	}
	decided := rules.Deny
	if found.Decision == rules.Ask {
		decided = rules.Ask
	}
	if hit != nil && strength[decided] > strength[hit.decision] {
		lines = append(lines, "rule: "+hit.name+" ("+string(hit.decision)+") lets this through, "+
			"but the ticket for this worktree narrows it")
	}
	if found.Decision == rules.Deny {
		// 範囲の外ではなく、チケットが deny と書いた項にヒットしたなら、どの項かを名指しする。
		for _, owner := range []*ticket.Ticket{tk, parent} {
			if owner == nil {
				continue
			}
			if entry := owner.EntryFor(rel); entry != nil && entry.Decision == rules.Deny {
				written := entry.Glob
				if written == "" {
					written = entry.Regex
				}
				lines = append(lines, "ticket entry: deny "+written)
				break
			}
		}
	}
	if found.Decision == rules.Ask {
		lines = append([]string{hookio.Cited(reasons.CodeTicketAsk, tk.Path)}, lines...)
		lines = append(lines, "This path is one the ticket marks `ask`: the user looks at each write here. "+
			"Say what you are changing and why.")
		return ticketOutcome{decision: rules.Ask, text: strings.Join(lines, "\n")}
	}
	// 上限ごとに次にすることが違う。子の範囲の外なら提案し直し、親や定義の上限の外なら範囲を広げても通らない。
	which, limit := "area", ""
	if found.Limit == scope.LimitType && found.Type != nil {
		pt := found.Type
		lines = append(lines, "limit: phase type "+pt.Title+" ("+pt.ID+"): "+strings.Join(pt.ScopeGlobs, ", "))
		which, limit = "type", pt.Title+"（"+pt.ID+"）"
	} else if found.Limit == scope.LimitParent && parent != nil {
		lines = append(lines, "limit: parent "+parent.ID+": "+parent.ScopeLine())
		which, limit = "parent", parent.ID
	}
	body := texts.Say(root, reasons.CodeTicketScope,
		texts.Values{"ticket": tk.ID, "title": tk.Title, "worktree": t.Name, "path": full, "limit": limit},
		texts.Variants{"what": which, "why": which, "next": which})
	lines = append([]string{hookio.Cited(reasons.CodeTicketScope, tk.Path)}, lines...)
	return ticketOutcome{decision: rules.Deny, text: strings.Join(append(lines, body), "\n")}
}
