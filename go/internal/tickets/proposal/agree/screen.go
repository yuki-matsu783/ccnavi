package agree

import (
	"cmp"
	"fmt"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
)

// 承認の画面（ユーザに見せる本文）と、承認の事実をモデルに伝える文。

func declaredReview(n ticket.Need) string {
	switch n {
	case ticket.Needed:
		return "要"
	case ticket.Unneeded:
		return "不要（子の宣言。計画かフェーズ定義が要ると言えば要る）"
	}
	return "宣言なし（計画のステップとフェーズ定義に従う）"
}

// ApprovedText はチケットが承認されたことをモデルに伝える文（`--agree --yes` の prompt。拡張が Claude Code に
// 渡す）。revisions は親の改版だった識別子、worktrees は子のワークツリーの計画（nil なら案内だけ）、notices は
// 1 件ごとに 1 行の `案内:` として添える補足。
func ApprovedText(tickets []*ticket.Ticket, revisions map[string]bool, root string, worktrees []ticket.WorktreePlan, notices []string) string {
	lines := []string{"[ccnavi] チケットが承認され、承認済みチケットの置き場（doing/）へ動いた。"}
	for _, t := range tickets {
		where := "親"
		switch {
		case revisions[t.ID]:
			where = "親の改版。計画・コピー・範囲のどれかが新しくなった"
		case t.IsChild():
			where = fmt.Sprintf("親 %s、フェーズ %d", t.Parent, t.Phase)
		}
		title := ""
		if t.Title != "" {
			title = ": " + t.Title
		}
		lines = append(lines, "- "+t.ID+title+"（"+where+"）")
	}
	ticketSh := config.ScriptCommand(root, config.TicketWrapper)
	lines = append(lines, "承認はユーザが済ませた。承認を頼み直さない。前の会話が無くても、この文と "+
		"'"+ticketSh+" status' で状況は分かる。承認済みかどうかや置き場は、"+
		"ファイルを自分で探さず status で確かめる。")
	// 子の着手は親の着手を前提にする。勧めるのはこの回に承認された親が居るときだけ（改版と子だけの回で
	// start <親> を勧めると、親は着手済みなので「着手済み」で終わる）。
	hasParent := slices.ContainsFunc(tickets, func(t *ticket.Ticket) bool { return !t.IsChild() && !revisions[t.ID] })
	hasChild := slices.ContainsFunc(tickets, func(t *ticket.Ticket) bool { return t.IsChild() })
	pick := func(withParent, without string) string {
		if hasParent {
			return withParent
		}
		return without
	}
	var next string
	switch {
	case !hasChild:
		next = pick("parent", "plain")
	case worktrees == nil:
		next = pick("parent_child", "child")
	default:
		next = pick("parent_child_wt", "child_wt")
	}
	lines = append(lines, texts.Say(root, "NEXT_AGREE_FOLLOWUP", nil, texts.Variants{"next": next}))
	if worktrees != nil {
		lines = append(lines, childWorktreeLines(tickets, worktrees, config.ScriptCommand(root, config.GitWrapper))...)
	}
	for _, n := range notices {
		lines = append(lines, "案内: "+n)
	}
	// 承認の前に計画編集タブで計画の順序が書き換わると、エージェントが書いた番号と承認された番号が違うことがある
	// （コミットしていない編集と見分けられない）ので、計画を持つ親とその子があればいつも添える。
	if slices.ContainsFunc(tickets, func(t *ticket.Ticket) bool {
		return (!t.IsChild() && t.HasPlan()) || (t.IsChild() && t.Phase != 0)
	}) {
		lines = append(lines, "番号は承認済みチケットで読み直す（承認の前に計画編集タブで計画の順序と番号が"+
			"書き換わっていることがある）。")
	}
	return strings.Join(lines, "\n")
}

// childWorktreeLines は子のワークツリーの状態を 1 子 1 行で言う。作れていない子は、名前と手で打つコマンドを出す。
func childWorktreeLines(tickets []*ticket.Ticket, worktrees []ticket.WorktreePlan, gitSh string) []string {
	byTicket := map[string]ticket.WorktreePlan{}
	for _, w := range worktrees {
		byTicket[w.Ticket] = w
	}
	var lines []string
	for _, t := range tickets {
		w, ok := byTicket[t.ID]
		if !t.IsChild() || !ok {
			continue
		}
		switch w.State {
		case ticket.WorktreeCreate:
			lines = append(lines, "子 "+t.ID+" のワークツリー "+w.Path+" は、親のブランチ "+w.Base+" から"+
				"承認の後に作られる予定（作れなければ案内の警告に理由が出る）。"+
				"無ければ '"+gitSh+" worktree add "+w.Path+" -b "+t.ID+" "+w.Base+"' で作る")
		case ticket.WorktreeExists:
			lines = append(lines, "子 "+t.ID+" のワークツリー "+w.Path+" は既にある")
		default:
			lines = append(lines, "子 "+t.ID+" のワークツリー "+w.Path+" は、まだありません（親 "+t.Parent+
				" のワークツリーがない、"+
				"またはプロジェクトの子）。親のワークツリーを用意し、そのブランチで "+
				"'"+gitSh+" worktree add "+w.Path+" -b "+t.ID+" <親のブランチ>' で作る")
		}
	}
	return lines
}

// originLine はどのプロジェクトの、どのツリーの、どの提案か。提案はツリーからの相対パスで見せる（絶対パスは
// 機械ごとに違い、画面の本文が Chrome と手元で揃わない）。
func originLine(t *ticket.Ticket) string {
	return "■ プロジェクト: " + cmp.Or(t.Project, "ワークスペース") +
		"  ワークツリー: " + cmp.Or(t.Tree, "ワークスペースルート") + "  提案: " + approval.SourcePath(t)
}

// screen は承認を求める画面。最初に「何が新たに書けるようになるか」を置き、子が編集できる範囲（親をどこまで
// 絞ったか）・人間レビューの要否・計画を並べる。定義は候補が持つもの（チケットごとにレイヤーが違いうる）を使う。
func screen(batch []*candidates.Candidate, pool map[string]*ticket.Ticket) string {
	lines := []string{fmt.Sprintf("チケットの承認リクエスト: %d 件", len(batch))}
	for _, cand := range batch {
		t := cand.Ticket
		types := cand.Types
		if cand.IsRevision() {
			lines = append(lines, "", "== "+t.ID+": "+t.Title+"  親の改版")
			lines = append(lines, scopeDiffLines(cand.Current, t)...)
			lines = append(lines, planDiffLines(cand.Current, t, types)...)
			lines = append(lines, copyDiffLines(cand.Current, t)...)
			for _, note := range cand.Notes {
				lines = append(lines, "    "+note)
			}
			lines = append(lines, originLine(t))
			continue
		}
		head := "  親チケット"
		if t.IsChild() {
			head = "  親 " + t.Parent + " / フェーズ " + phaseLabel(t, pool, types)
		}
		lines = append(lines, "", "== "+t.ID+": "+t.Title+head)
		if t.IsChild() {
			// 親は一緒に承認の対象に入っていることが普通。承認済みチケットだけを引くと「無い」になる。
			parent := pool[t.Parent]
			lines = append(lines, "■ この子チケットで編集可能な範囲",
				"    子の範囲は親の範囲の中に収まる。下に並ぶのは親から絞った結果で、"+
					"親の範囲にない場所が新たに編集可能になることはありません")
			if parent != nil {
				lines = append(lines, bullets("親の範囲", slices.Concat(parent.Paths(rules.Allow), parent.Paths(rules.Ask)))...)
			} else {
				lines = append(lines, "    親の範囲: 親がまだ承認されていない")
			}
			if bound := typeOf(t, pool, types); bound != nil && !bound.Inherit {
				lines = append(lines, bullets("定義「"+bound.Title+"」の範囲", bound.ScopeGlobs)...)
			}
		} else {
			lines = append(lines, "■ このチケットで編集可能な範囲",
				"    このチケットでは、以下に示す場所のみ編集できるようになります。"+
					"allow は確認なしで編集できる場所、ask は実行時に確認が必要な場所、"+
					"deny はこのチケットでも編集できない場所",
				// チケットの範囲はルールの allow より強い。「ルールで開けてあるから範囲の外でも書ける」と読み違えやすい。
				"    ルールで許可（allow）されていても、このチケットの範囲外では編集できません。"+
					"ルールで禁止（deny）にした場所は、この範囲の中でも止まります")
		}
		for _, name := range rules.Sections() {
			lines = append(lines, bullets(string(name), t.Paths(name))...)
		}
		if len(cand.Overflow) > 0 {
			// 範囲のすぐ下に置く（判定では止まる）。見出しの「編集対象」と「書き込めない」は、宣言した側と実際の
			// 書き込みが止まる側を指し分けるので、ほかの見出しの「編集」に揃えない。
			lines = append(lines, "■ チケットで編集対象としているが、書き込めない場所",
				"    親の範囲かフェーズ定義の上限を超えているか、計画のどの定義の範囲とも"+
					"重ならない。承認はできますが、これらの場所への書き込みは拒否されます。")
			for _, p := range cand.Overflow {
				lines = append(lines, "    "+p.Detail)
			}
		}
		if t.IsChild() {
			lines = append(lines, "■ 人間レビュー（子の宣言）: "+declaredReview(t.ReviewRequired))
			if t.ReviewReason != "" {
				lines = append(lines, "    理由: "+t.ReviewReason)
			}
			if len(t.Predecessors) > 0 {
				lines = append(lines, "■ 先行: "+strings.Join(t.Predecessors, ", "),
					"    すべての先行チケットが、取り消されずに完了し、"+
						"done/ に移動している必要があります。"+
						"条件を満たすまで、承認・開始はできません。")
			}
		} else if t.HasPlan() {
			lines = append(lines, "■ 全体計画",
				"    承認すると、この順序で進めることに合意したことになります。"+
					"先行フェーズの終了と必要なレビューの完了を確認するまで、このフェーズの子チケットは承認できません。")
			lines = append(lines, planLines(t.Plan, 1, types)...)
			if t.FeedbackPlanned {
				lines = append(lines, "■ フィードバック計画")
				lines = append(lines, orElse(planLines(t.Feedback, len(t.Plan)+1, types), "    対応なし")...)
			}
			lines = append(lines, workflowLines(t, phases.Compute(t))...)
			lines = append(lines, copyLines(t, types, cand.Config)...)
		}
		if !t.IsChild() && t.Issue != 0 {
			lines = append(lines, "■ issue: "+t.IssueLabel(),
				"    この親のマージリクエストの本文に Closes として記載する番号。"+
					"マージされると、このissueもクローズされます。")
		}
		if why := ospath.Strip(t.Rationale); why != "" {
			lines = append(lines, "■ エージェントが記載した理由")
			for _, line := range ospath.Splitlines(why) {
				lines = append(lines, "    "+line)
			}
		}
		lines = append(lines, originLine(t))
		var warnings []rules.Problem
		for _, p := range cand.Complaints {
			if p.Severity == rules.Warn {
				warnings = append(warnings, p)
			}
		}
		if len(warnings) > 0 {
			lines = append(lines, "■ 判定に使われない記述",
				"    提案に記載があっても、判定はこれを読みません。"+
					"承認しても、編集できる場所は変わりません")
			for _, p := range warnings {
				lines = append(lines, "    "+p.Detail)
			}
		}
	}
	return strings.Join(lines, "\n")
}

func orElse(lines []string, fallback string) []string {
	if len(lines) == 0 {
		return []string{fallback}
	}
	return lines
}

func planLines(items []ticket.PlanItem, start int, types *phases.Types) []string {
	var lines []string
	for i, item := range items {
		pt := types.Get(item.Type)
		title := item.Type
		if pt != nil {
			title = pt.Title
		}
		review := ""
		switch {
		case item.Deferred:
			review = "次のフェーズとまとめてレビュー"
		case item.Review == ticket.Needed:
			review = "レビュー必須：計画で指定"
		case item.Review == ticket.Unneeded:
			review = "レビュー不要：計画で指定"
		case pt != nil && pt.Review:
			review = "レビュー必須: マージリクエスト"
		case pt != nil:
			review = "レビュー不要"
		}
		line := fmt.Sprintf("    %d. %s / %s", start+i, title, item.Type)
		if review != "" {
			line += "  " + review
		}
		lines = append(lines, line)
	}
	return lines
}

// workflowLines は計画の先行。全部の番号と、すぐ始まるステップ（線の書き漏れをユーザが見つける場所）。
func workflowLines(t *ticket.Ticket, wf *ticket.Workflow) []string {
	found := phases.Lines(t, wf)
	if len(found) == 0 {
		return nil
	}
	lines := []string{"■ フェーズ間の依存関係（計画の各ステップの after で決まります。" +
		"承認後に phases.yml を変更しても変わりません）"}
	for _, x := range found {
		lines = append(lines, "    "+x)
	}
	return lines
}

// copyLines は親に固定する定義を 3 つに分けて出す（この親が使う定義・使われていない定義・phases.yml にあって
// この親では使わない定義）。中身は phases.yml と同じ（承認が確かめる）なので、読むのはどれを固定しどれを外したか。
func copyLines(t *ticket.Ticket, types, cfg *phases.Types) []string {
	numbers := map[string][]string{}
	for _, s := range t.Numbered() {
		numbers[s.Item.Type] = append(numbers[s.Item.Type], strconv.Itoa(s.Number))
	}
	var used, unused []*phases.PhaseType
	if types != nil {
		for pt := range types.All() {
			if _, ok := numbers[pt.ID]; ok {
				used = append(used, pt)
			} else {
				unused = append(unused, pt)
			}
		}
	}
	var lines []string
	if len(used) > 0 {
		lines = append(lines, "■ この親に固定するフェーズ定義（phases:。承認のあとの判定はこれを読む）",
			"    承認したときの phases.yml と同じ中身を親にコピーして固定します。"+
				"承認後に phases.yml を変更しても、この親チケットの編集可能範囲・"+
				"確認対象・成果物は変わりません。")
		for _, pt := range used {
			scope := strings.Join(pt.ScopeGlobs, ", ")
			if pt.Inherit {
				scope = "inherit（親チケットの編集可能範囲を引き継ぐ）"
			}
			parts := []string{"review: " + strconv.FormatBool(pt.Review), "scope: " + scope}
			if len(pt.Deliverables) > 0 {
				parts = append(parts, "deliverables: "+strings.Join(pt.Deliverables, ", "))
			}
			if pt.Learning == phases.LearningOff {
				parts = append(parts, "learning: off")
			}
			parts = append(parts, "使う番号: "+strings.Join(numbers[pt.ID], ", "))
			lines = append(lines, "    "+pt.Title+" / "+pt.ID+"  "+strings.Join(parts, "、"))
		}
	}
	if len(unused) > 0 {
		lines = append(lines, "■ 使われていない定義（phases: にあるが、どのステップも使わない）")
		for _, pt := range unused {
			lines = append(lines, "    "+pt.Title+" / "+pt.ID)
		}
	}
	var left []string
	if cfg != nil {
		for pt := range cfg.All() {
			if types.Get(pt.ID) == nil {
				left = append(left, pt.Title+" / "+pt.ID)
			}
		}
	}
	if len(left) > 0 {
		lines = append(lines, "■ この親では使わない定義（phases.yml にあるが phases: にない）",
			"    使わない: "+strings.Join(left, "、"))
	}
	return lines
}

// bullets は範囲の項を 1 行 1 項目の箇条書きにする（カンマや空白でつなぐと、承認する人が項目の切れ目を
// 読み違える）。項目が無ければ何も出さない。
func bullets(label string, items []string) []string {
	if len(items) == 0 {
		return nil
	}
	lines := []string{"    " + label + ":"}
	for _, item := range items {
		lines = append(lines, "      - "+item)
	}
	return lines
}

type scopeItem struct {
	regex bool
	expr  string
}

func (s scopeItem) shown() string {
	if s.regex {
		return "regex(" + s.expr + ")"
	}
	return s.expr
}

// scopeItems は差分で比べる項。glob `src/app` と regex `src/app` は当たる範囲が違うので、種別を組に入れて比べる
// （文字列だけだと、署名が違うのに差分が空になり、範囲の広がりが見えなくなる）。
func scopeItems(t *ticket.Ticket, name rules.Decision) []scopeItem {
	var out []scopeItem
	for _, e := range t.Entries {
		if e.Decision != name {
			continue
		}
		item := scopeItem{false, e.Glob}
		if e.Regex != "" {
			item = scopeItem{true, e.Regex}
		}
		if !slices.Contains(out, item) {
			out = append(out, item)
		}
	}
	return out
}

// scopeDiffLines は改版の範囲の差分。古い範囲は承認のあとに上書きされ ccnavi の側に残らないので、新たに書ける
// ようになる範囲と書けなくなる範囲をここで見せる。
func scopeDiffLines(current, revised *ticket.Ticket) []string {
	var lines []string
	for _, name := range rules.Sections() {
		before, after := scopeItems(current, name), scopeItems(revised, name)
		for _, i := range after {
			if !slices.Contains(before, i) {
				lines = append(lines, "    "+string(name)+" 追加: "+i.shown())
			}
		}
		for _, i := range before {
			if !slices.Contains(after, i) {
				lines = append(lines, "    "+string(name)+" 削除: "+i.shown())
			}
		}
	}
	if len(lines) == 0 {
		return nil
	}
	return slices.Concat([]string{
		"■ 範囲の差分（改版で親に固定する範囲）",
		"    allow は確認なしで編集できる場所、ask は実行時に確認が必要な場所、" +
			"deny はこのチケットでも編集できない場所。承認すると、古い範囲は上書きされます",
	}, lines, []string{
		"    作業中の子はいません（いれば改版は通りません）。" +
			"新しい範囲の子は、新しい子として提案します。" +
			"狭めた場合、完了済み・レビュー待ちの子の範囲が親の外に出ることがありますが、" +
			"書き込みは親の範囲で切り詰められます",
	})
}

// copyDiffLines は改版の phases: の差分。改版が実際に書く frontmatter（revisedFront）と承認済みチケットのコピーを
// 比べる。
func copyDiffLines(current, revised *ticket.Ticket) []string {
	before := phases.TypesOf(current)
	after := &phases.Types{}
	if written := revisedFront(current, revised).Get(ticket.PhasesKey); written != nil {
		after, _ = phases.ReadCopy(written)
	}
	var lines []string
	for pt := range after.All() {
		was := before.Get(pt.ID)
		if was == nil {
			lines = append(lines, "    追加: "+pt.Title+" / "+pt.ID)
		} else if !phases.Same(pt, was) {
			lines = append(lines, "    変更: "+pt.Title+" / "+pt.ID+"（"+strings.Join(phases.Differing(was, pt), ", ")+"）")
		}
	}
	for pt := range before.All() {
		if after.Get(pt.ID) == nil {
			lines = append(lines, "    削除: "+pt.Title+" / "+pt.ID)
		}
	}
	if len(lines) == 0 {
		return nil
	}
	return append([]string{"■ phases: の差分（改版で親に固定する定義）"}, lines...)
}

func indent(prefix string, lines []string) []string {
	out := make([]string, len(lines))
	for i, l := range lines {
		out[i] = prefix + l
	}
	return out
}

func planDiffLines(current, revised *ticket.Ticket, types *phases.Types) []string {
	var lines []string
	if !approval.SamePlan(current, revised, phases.Plan) {
		lines = append(lines, "■ 全体計画の変更", "    いま:")
		lines = append(lines, indent("    ", planLines(current.Plan, 1, phases.TypesOf(current)))...)
		lines = append(lines, "    改版:")
		lines = append(lines, indent("    ", planLines(revised.Plan, 1, types))...)
	}
	before := phases.Lines(current, phases.Compute(current))
	after := phases.Lines(revised, phases.Compute(revised))
	if !slices.Equal(before, after) {
		lines = append(lines, "■ フェーズ間の依存関係の変更", "    いま:")
		lines = append(lines, indent("        ", before)...)
		lines = append(lines, "    改版:")
		lines = append(lines, indent("        ", after)...)
	}
	if !approval.SamePlan(current, revised, phases.FeedbackPart) {
		lines = append(lines, "■ フィードバック計画")
		lines = append(lines, orElse(planLines(revised.Feedback, len(revised.Plan)+1, types),
			"    対応なし。見たうえで対応しない、という記録になります")...)
	}
	return lines
}

func phaseLabel(t *ticket.Ticket, pool map[string]*ticket.Ticket, types *phases.Types) string {
	if pt := typeOf(t, pool, types); pt != nil {
		return strconv.Itoa(t.Phase) + ": " + pt.Title
	}
	return strconv.Itoa(t.Phase)
}

func typeOf(t *ticket.Ticket, pool map[string]*ticket.Ticket, types *phases.Types) *phases.PhaseType {
	if !t.IsChild() || types.Len() == 0 {
		return nil
	}
	parent := pool[t.Parent]
	if parent == nil || !parent.HasPlan() {
		return nil
	}
	if item := parent.ItemAt(t.Phase); item != nil {
		return types.Get(item.Type)
	}
	return nil
}
