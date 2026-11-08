package lint

import (
	"fmt"
	"path/filepath"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/lifecycle/stop"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/proposal/agree"
	"ccnavi/internal/tickets/proposal/candidates"
)

// チケットの検査。承認済みチケット・承認・提案・古い提案と、親子の運用に要る hook の登録・スクリプト・リモートを
// 読み書きする手段を見る。判定に使われるのは承認済みチケットの側だけなので、問うのは「使われている範囲は何か」と
// 「ワークツリーと提案がそれと一致しているか」。

// workspaceRootName は文に出す、ワークスペースルートのツリーの名前。
const workspaceRootName = "(ワークスペースルート)"

// ticketScripts は親子の運用に要る sh。レビュー済みのマーカーが置かれるまでシェル実行を止める間も、例外として通る。
var ticketScripts = []string{"ccnavi-ticket.sh", config.ReviewWrapper, "ccnavi-git.sh"}

func ticketProblem(severity rules.Severity, detail string) rules.Problem {
	return rules.Problem{Severity: severity, Rule: whereTicket, Detail: detail}
}

func treeName(t *ticket.Ticket) string {
	if t.Tree == "" {
		return workspaceRootName
	}
	return t.Tree
}

func stateOr(s, fallback place.State) place.State {
	if s == "" {
		return fallback
	}
	return s
}

// ticketProblems はチケットと承認済みチケットとワークツリーが合っているかを見る。一致していない状態は誤りではないが、
// 書いたユーザは書いたとおりに使われていると思っている。その思い違いを名指しする。
func ticketProblems(conf *config.Settings, root string) []rules.Problem {
	if !conf.TicketsEnabled() {
		return []rules.Problem{ticketProblem(rules.Warn, env.TicketControl+"=disable。チケットの範囲・フェーズの HITL ポイント・"+
			"サブエージェントの制限は有効にならない")}
	}
	if root == "" {
		root, _ = ospath.Getwd()
	}
	// 1. チケットを使っていないプロジェクトは、ほとんどの検査を飛ばす。
	if !anyCopies(conf, root) && !treeHasTickets(root, conf.Tickets) {
		// チケットによる制御は任意。使っていないプロジェクトに苦情を返すと、その 1 行が常態になって他の報告ごと読まれなく
		// なる。送信の途中で止まった操作は、チケットが無くても言う。
		return operationProblems(conf, root, nil, nil)
	}
	// 2. 承認済み・閉じた・審査中のチケットを読む。読めないファイルは error にする。
	problems := approvedGuarded(conf)
	raw := approval.ReadRaw(conf, root)
	copies, notes := approval.Scan(conf, root, false, raw)
	closed, closedNotes := approval.Scan(conf, root, true, raw)
	review, reviewNotes := approval.ScanReview(conf, root, raw)
	// 閉じた承認済みチケットの苦情も拾う。判定は閉じたものを読まないが、読めないファイルが残っていると他の機械へそのまま届く。
	for _, note := range slices.Concat(notes, closedNotes, reviewNotes) {
		problems = append(problems, ticketProblem(rules.Error, note))
	}
	// done は閉じた・審査中の識別子。todo/ に同じ識別子が残っていないかを見るために使う。
	index := approval.ByID(copies)
	done := map[string]bool{}
	for _, t := range slices.Concat(closed, review) {
		done[t.ID] = true
	}

	// 3. 提案を読み、チケットの状態ごとの検査を当てる。
	read := approval.ReadProposals(conf, root, raw)
	proposals := read.Kept
	problems = append(problems, read.Problems...)
	problems = append(problems, staleProblems(conf, root, read.Stale, index, done)...)
	problems = append(problems, copyProblems(conf, root, copies, index, closed, raw)...)
	// 閉じた・レビュー待ちの親にも issue と MR のセットを求める（doing の親は copyProblems が見る）。
	for _, t := range slices.Concat(review, closed) {
		for _, p := range approval.MRProblems(t) {
			problems = append(problems, ticketProblem(rules.Error, t.ID+": "+p.Detail))
		}
	}

	// ツリーの名前から、そのツリーがどのリポジトリのものかを引く表（ワークツリーは元リポジトリのほうに付く）。
	repoOf := map[string]string{}
	for _, t := range tree.AllTrees(root, conf.Projects) {
		name := t.Name
		if name == "" {
			name = workspaceRootName
		}
		repoOf[name] = t.Project
	}
	// 先行の判定は承認・着手と同じ関数に任せる。親子のチケットの先行は、Chrome 拡張の判定と答えがそろうよう、親のブランチ上のチケットで読み直す。
	preds := approval.PoolOf(copies, review, closed, proposals)
	preds.AlignImported(conf, root)
	problems = append(problems, proposalProblems(root, proposals, copies, index, closed, done, repoOf, preds)...)

	// 4. ブランチ名と置き場の検査。統合先の名前はリポジトリごとに 1 回だけ引く（git を呼ぶため）。
	integrations := map[string]string{}
	integrationOf := func(project string) string {
		// 統合先はリポジトリごと。チケットの project のリポジトリの origin/HEAD で引く。
		if name, ok := integrations[project]; ok {
			return name
		}
		owner := tree.ProjectRoot(conf.Projects, project)
		if owner == "" {
			owner = root
		}
		integrations[project] = integrate.IntegrationName(owner)
		return integrations[project]
	}
	settled := slices.Concat(copies, closed, review)
	problems = append(problems, placeProblems(root, proposals, settled, repoOf)...)
	problems = append(problems, branchNameProblems(conf, root, proposals, copies, closed, review, integrationOf, adoptedParents(root, proposals))...)
	problems = append(problems, prefixSettingProblems(conf)...)
	for _, text := range candidates.ExistingBranchWarnings(conf, root, proposals, settled) {
		problems = append(problems, ticketProblem(rules.Warn, text+branchRule))
	}
	problems = append(problems, approvalProblems(conf, root, proposals, copies, closed, review)...)

	// 5. ワークツリーの側の検査。
	worktrees := tree.Worktrees(root, conf.Projects)
	known := map[string]bool{}
	for _, t := range slices.Concat(proposals, copies, closed, review) {
		known[t.ID] = true
	}
	problems = append(problems, worktreeProblems(conf, root, worktrees, index, copies, known)...)
	problems = append(problems, unregisteredWorktrees(root, worktrees)...)
	if slices.ContainsFunc(copies, func(t *ticket.Ticket) bool { return !t.IsChild() }) {
		problems = append(problems, reviewToken(root)...)
	}
	problems = append(problems, ticketHooks(root)...)
	return append(problems, operationProblems(conf, root, copies, review)...)
}

// copyProblems は作業中の承認済みチケットを検査する。置き場を動かして承認する進め方では承認の検査を通らないので、
// ここで全部言う。blocked は判定が止める理由なので error。計画の形は Validate が付けた深刻度のまま。フェーズの順序は
// 「その順で始めた」という記録でいま止める根拠にならないので warn。定義のコピーの食い違い・再開で残った欄・残った
// reviewed のマーカー・HEAD の祖先でない基準点も、判定は止めないので warn。
func copyProblems(conf *config.Settings, root string, copies []*ticket.Ticket, index map[string]*ticket.Ticket,
	closed []*ticket.Ticket, raw *approval.Raw) []rules.Problem {
	var out []rules.Problem
	pool := map[string]*ticket.Ticket{}
	for id, t := range index {
		pool[id] = t
	}
	for _, t := range closed {
		if _, ok := pool[t.ID]; !ok {
			pool[t.ID] = t
		}
	}
	// 定義はチケットごとにレイヤーが違いうるので、プロジェクトごとに 1 回だけ読む。
	types := map[string]*phases.Types{}
	typesOf := func(project string) *phases.Types {
		if _, ok := types[project]; !ok {
			types[project] = phases.LoadTypes(conf, root, project)
		}
		return types[project]
	}
	for _, t := range copies {
		say := func(severity rules.Severity, detail string) {
			out = append(out, ticketProblem(severity, t.ID+": "+detail))
		}
		if left := approval.ResumedFields(t); len(left) > 0 {
			names := make([]string, len(left))
			for i, name := range left {
				names[i] = "`" + name + "`"
			}
			say(rules.Warn, "作業中（doing/）なのに "+strings.Join(names, ", ")+" に値が残っている。"+
				"done/ から手で戻した再開なら、ユーザがその欄を空にしてください"+
				"（残っていると、フローの着手中の扱いなど、着手中として数えない箇所がある）")
		}
		if left := phase.ResumedReview(conf, root, t, raw); left != "" {
			say(rules.Warn, left)
		}
		if t.StartedAt != "" {
			// 判定は git を読まないので判定には入れない。start も止めない（通れば基準点を HEAD で書き直す）。
			if off := stop.BaseOffHead(conf, root, t); off != "" {
				say(rules.Warn, off)
			}
		}
		for _, p := range approval.MRProblems(t) {
			say(rules.Error, p.Detail)
		}
		if t.Blocked != "" {
			say(rules.Error, t.Blocked)
			continue
		}
		missing := ""
		if t.IsChild() && pool[t.Parent] == nil {
			var everything []*ticket.Ticket
			if raw != nil {
				everything = raw.Everything()
			}
			missing = candidates.ParentElsewhereText(conf, root, t, everything)
		}
		complaints, overflow := candidates.Validate(t, pool, typesOf(candidates.ProjectOf(t, pool)), true, missing)
		for _, p := range slices.Concat(complaints, overflow) {
			say(p.Severity, p.Detail)
		}
		if parent := pool[t.Parent]; t.IsChild() && parent != nil {
			// 承認のときは error。承認済みのものに適用するのは「その順で始めた」という記録なので warn。
			for _, p := range phase.OrderProblems(conf, root, t, parent, nil, raw) {
				say(rules.Warn, p.Detail)
			}
		}
	}
	return out
}

// operationProblems は送信の途中で止まった操作、受け入れ台帳の壊れ、経路の欄を持たない子、保護の外れた組み合わせ。
func operationProblems(conf *config.Settings, root string, copies, review []*ticket.Ticket) []rules.Problem {
	var out []rules.Problem
	if conf.State != "" {
		for _, record := range oplog.Pending(conf.State) {
			severity := rules.Warn
			how := "decide は 'ccnavi-user-decide.sh --resume <操作ID>' で続きを送る。" +
				"review-request・comment・ready・close-early は同じコマンドを打ち直す"
			if record.Broken != "" {
				severity = rules.Error
				how = "記録が壊れている。ユーザが logs/state/ops/ の該当ファイルを確かめて消す"
			}
			out = append(out, ticketProblem(severity, "引き継ぎ未完了: "+oplog.Summary(record)+"。"+how))
		}
	}
	for _, parent := range copies {
		if parent.IsChild() {
			continue
		}
		home := approval.HomeDirRead(conf, root, parent.ID, "", parent.Project)
		if _, bad := marks.ReadLedger(home, parent.ID); bad != "" {
			out = append(out, ticketProblem(rules.Warn, parent.ID+": "+bad))
		}
	}
	for _, child := range review {
		if child.IsChild() && child.FinishedTo == "" {
			out = append(out, ticketProblem(rules.Info, child.ID+": review/ の子に `finished_to` が無い（`finish` を通らずに"+
				"閉じた子）。手で done/ に動かしても確定できないので、confirm か decide を使う"))
		}
	}
	if conf.GuardTicketApproval == string(config.Disable) && conf.GuardCoreFiles == string(config.Disable) {
		out = append(out, ticketProblem(rules.Warn, env.GuardTicketApproval+" と "+env.GuardCoreFiles+
			" が両方 disable。承認の直接実行も設定ファイルの書き換えも止まらない"))
	}
	return out
}

// adoptedParents は親のワークツリーが在り、HEAD のブランチ名を変換したものが識別子と一致する親の識別子。ブランチは
// 既に決まっているので、名前の形式の警告は言わない。
func adoptedParents(root string, proposals []*ticket.Ticket) map[string]bool {
	out := map[string]bool{}
	for _, t := range proposals {
		if !t.IsChild() && standing.HeadBranch(tree.WorktreePath(root, t.ID), t.ID) != "" {
			out[t.ID] = true
		}
	}
	return out
}

// anyCopies はどこかのツリーに承認済みチケットの置き場があるか。
func anyCopies(conf *config.Settings, root string) bool {
	for _, t := range approval.Trees(conf, root) {
		if ospath.IsDir(conf.ApprovedDir(t.Root)) {
			return true
		}
	}
	return false
}

// treeHasTickets はワークスペースルートかワークツリーのどこかに提案の置き場があるか。
func treeHasTickets(root, ticketsRel string) bool {
	for _, t := range append([]tree.Tree{tree.MainTree(root)}, tree.Worktrees(root, "")...) {
		if ospath.IsDir(ospath.Join(t.Root, filepath.FromSlash(ticketsRel))) {
			return true
		}
	}
	return false
}

// approvedGuarded は承認済みチケットの置き場が ccnavi ディレクトリの下にあるか。外に向けると組み込みの保護
// （builtin:guard-project-home）が及ばず、エージェントが承認済みチケットを書けて承認の意味が無くなる。外に向くのは
// 診断のフラグ（--approved・--project-home）で動かしたときだけ。
func approvedGuarded(conf *config.Settings) []rules.Problem {
	norm := func(value, fallback string) string {
		if value == "" {
			value = fallback
		}
		// 大文字小文字の違う綴り（`.CCNAVI/approved`）も同じ置き場として比べる。
		return strings.Trim(strings.ReplaceAll(pathcase.Fold(value), `\`, "/"), "/")
	}
	home := norm(conf.ProjectHome, config.DefaultProjectHome)
	approved := norm(conf.Approved, config.DefaultApproved)
	if home != "" && (approved == home || strings.HasPrefix(approved, home+"/")) {
		return nil
	}
	return []rules.Problem{ticketProblem(rules.Error, "承認済みチケットの置き場（"+conf.Approved+"）が ccnavi ディレクトリ（"+
		conf.ProjectHome+"）の外にある。組み込みの保護が及ばないので、エージェントが承認済みチケットを書き換えられ、承認が意味を持たない")}
}

// approvalProblems は承認で失敗するものを承認の前に名指しする。承認と同じ関数（candidates.Candidates）を通し、
// 同じ事実を数える経路を 2 本にしない。「まだ承認できない」（前のフェーズが閉じていない子）だけは warn にする。
// --lint の終了コードは設定画面の保存と CI に使われるので、編集と関わりのない提案 1 本で止めない（承認は error のまま）。
func approvalProblems(conf *config.Settings, root string, proposals, copies, closed, review []*ticket.Ticket) []rules.Problem {
	pending, revisions := agree.Waiting(proposals, copies, closed, review)
	if len(pending) == 0 && len(revisions) == 0 {
		return nil
	}
	batch, rejected, _ := candidates.Candidates(conf, root, pending, revisions, copies)
	var out []rules.Problem
	for _, cand := range batch {
		for _, p := range slices.Concat(cand.Complaints, cand.Overflow) {
			out = append(out, approvalSaid(cand.Ticket.ID, p))
		}
	}
	for _, r := range rejected {
		for _, p := range r.Problems {
			out = append(out, approvalSaid(r.Ticket.ID, p))
		}
	}
	return out
}

func approvalSaid(id string, p rules.Problem) rules.Problem {
	severity := p.Severity
	if p.Kind == rules.KindNotYet {
		severity = rules.Warn
	}
	return ticketProblem(severity, id+": "+p.Detail)
}

// spot はチケットの在りか（リポジトリ・ツリー・状態）。
type spot struct{ repo, at, state string }

// proposalProblems は提案の側。承認待ち、先行が閉じていない着手済み、同じ識別子の重複。同じ識別子が別のツリーに在る
// のは普通（承認済みチケットは git で共有し、ワークツリーはそれぞれ別のコミットを指す）なので、error にするのは
// 1 つのツリーの中で 2 つの状態に在る形（ticket.Collisions）と、リポジトリをまたいだ形（違うチケットどうしの衝突）だけ。
func proposalProblems(root string, proposals, copies []*ticket.Ticket, index map[string]*ticket.Ticket, closed []*ticket.Ticket,
	done map[string]bool, repoOf map[string]string, preds approval.Pool) []rules.Problem {
	var out []rules.Problem
	placeOf := func(t *ticket.Ticket, state place.State) spot {
		at := treeName(t)
		repo, ok := repoOf[at]
		if !ok {
			repo = at
		}
		return spot{repo, at, string(state)}
	}
	// 同じ識別子が、どのリポジトリ・ツリー・状態に在るかを集める（order は初めて出た順）。
	var order []string
	seen := map[string][]spot{}
	held := map[string][]*ticket.Ticket{}
	hold := func(t *ticket.Ticket, state place.State) {
		if _, ok := seen[t.ID]; !ok {
			order = append(order, t.ID)
		}
		seen[t.ID] = append(seen[t.ID], placeOf(t, state))
		held[t.ID] = append(held[t.ID], t)
	}
	// state が空のチケットは、承認済みなら doing、閉じたなら done として数える。
	for _, t := range copies {
		hold(t, stateOr(t.State, place.Doing))
	}
	for _, t := range closed {
		hold(t, stateOr(t.State, place.Done))
	}
	for _, t := range proposals {
		hold(t, t.State)
		if t.State != place.Todo {
			continue
		}
		// todo/ の提案が、承認済みの同じ識別子と重なる形を分ける。親の改版（計画が違う）だけは正しい状態。
		if current, ok := index[t.ID]; ok {
			if !t.IsChild() && t.HasPlan() && approval.PlanDiffers(t, current) {
				continue // 親の改版。承認待ちに入る
			}
			out = append(out, ticketProblem(rules.Warn, fmt.Sprintf("%s は承認済み（%s の %s/）なのに todo/ にも在る（%s）。"+
				"改版（計画・コピー・範囲）でなければ todo/ の側を削除してください", t.ID, treeName(current),
				stateOr(current.State, place.Doing), relToRoot(root, t.Path))))
			continue
		}
		if done[t.ID] {
			out = append(out, ticketProblem(rules.Warn, closedTodoText(t.ID, relToRoot(root, t.Path))))
			continue
		}
		out = append(out, ticketProblem(rules.Warn, fmt.Sprintf("%s は承認待ち（%s の todo/）。"+
			"'ccnavi-user-agree.sh' を通すまで範囲は有効にならない", t.ID, treeName(t))))
	}
	for _, t := range indexValues(copies, index) {
		if t.StartedAt == "" {
			continue
		}
		// 先行の数え方は承認と着手と同じ。ここに出るのは着手のあとに先行が動いたか、止める前の版で着手したもの。
		if unmet := approval.UnmetPredecessors(t, preds); len(unmet) > 0 {
			names := make([]string, len(unmet))
			for i, p := range unmet {
				names[i] = p.Ticket + "（" + p.Label() + "）"
			}
			out = append(out, ticketProblem(rules.Warn, t.ID+" は先行 "+strings.Join(names, ", ")+" を満たしていないのに着手している"))
		}
	}
	// 識別子ごとに、重複を調べる。リポジトリをまたぐ重複を先に言い、同じリポジトリの中の重複はその後で調べる。
	for _, id := range order {
		places := seen[id]
		repos := map[string]bool{}
		for _, p := range places {
			repos[p.repo] = true
		}
		if len(repos) > 1 {
			sorted := slices.Clone(places)
			// 文面を機械に依らず同じにするため、リポジトリ・ツリー・状態の順に並べる。
			slices.SortFunc(sorted, func(a, b spot) int {
				return strings.Compare(a.repo+"\x00"+a.at+"\x00"+a.state, b.repo+"\x00"+b.at+"\x00"+b.state)
			})
			where := make([]string, len(sorted))
			for i, p := range sorted {
				repo := p.repo
				if repo == "" {
					repo = "(ワークスペース)"
				}
				// プロジェクトの元ツリーならツリーの名前を 2 度書かない。
				if p.at == p.repo {
					where[i] = repo + ":" + p.state
				} else {
					where[i] = repo + "/" + p.at + ":" + p.state
				}
			}
			out = append(out, ticketProblem(rules.Error, id+" が複数のリポジトリにある: "+strings.Join(where, ", ")+
				"。識別子はリポジトリごとに一意にしてください"))
			continue
		}
		// 正とするツリーでまとめて 2 つ以上残る形と、その中で 2 つの置き場に在る形。判断は状態の操作とボードと同じ関数。
		caught := ticket.Collisions(held[id], false)
		if len(caught) == 0 {
			continue
		}
		caught = slices.Clone(caught)
		slices.SortStableFunc(caught, func(a, b *ticket.Ticket) int {
			if c := strings.Compare(a.Tree, b.Tree); c != 0 {
				return c
			}
			return strings.Compare(string(a.State), string(b.State))
		})
		where := make([]string, len(caught))
		for i, t := range caught {
			where[i] = treeName(t) + ":" + string(t.State)
		}
		out = append(out, ticketProblem(rules.Error, id+" が複数の場所にある: "+strings.Join(where, ", ")))
	}
	return out
}

// indexValues は index（ByID(copies)）の値を、識別子が copies に初めて出た順に並べる（ByID が作る順）。
func indexValues(copies []*ticket.Ticket, index map[string]*ticket.Ticket) []*ticket.Ticket {
	var out []*ticket.Ticket
	listed := map[string]bool{}
	for _, t := range copies {
		if !listed[t.ID] {
			listed[t.ID] = true
			out = append(out, index[t.ID])
		}
	}
	return out
}

// relToRoot は名指しに使う、ワークスペースルートからの相対パス（"/" 区切り）。
func relToRoot(root, path string) string {
	if root == "" || path == "" {
		return path
	}
	return ospath.Slashed(ospath.RelOr(path, root))
}

// staleProblems は正とするツリーの外に残った、承認済みの識別子の todo/ の提案。言うのは計画の違う親の版（書く場所を
// 案内する。文は --agree と同じ）と、閉じたかレビュー待ちの識別子の todo/ だけ。計画の同じ古い提案は、承認の前に
// 切ったワークツリーに残る普通の形なので言わない。
func staleProblems(conf *config.Settings, root string, stale []approval.Stale, index map[string]*ticket.Ticket, done map[string]bool) []rules.Problem {
	var out []rules.Problem
	for _, s := range stale {
		current := index[s.Ticket.ID]
		var text string
		switch {
		case approval.RevisionElsewhere(s.Ticket, current):
			text = approval.RevisionElsewhereText(conf, root, s.Ticket, s.Where, nil)
		case current == nil && done[s.Ticket.ID]:
			text = closedTodoText(s.Ticket.ID, relToRoot(root, s.Ticket.Path))
		default:
			continue
		}
		out = append(out, ticketProblem(rules.Warn, text))
	}
	return out
}

func closedTodoText(id, rel string) string {
	return id + " は閉じたかレビュー待ちなのに todo/ にも在る（" + rel + "）。todo/ の側は承認の対象にならない。" +
		"再開するには、ユーザが承認済みチケットを戻す"
}

// reviewToken は sh がリモートを読み書きできる形か（gh / glab か、curl とホストに合うトークン）。
func reviewToken(root string) []rules.Problem {
	url, _ := git.Text(root, []string{"remote", "get-url", "origin"}, git.Options{})
	if url == "" {
		return []rules.Problem{ticketProblem(rules.Warn, "origin がありません。レビューの依頼と確認は動かない")}
	}
	if problem := host.TransportProblem(url); problem != "" {
		return []rules.Problem{ticketProblem(rules.Warn, problem+"。レビューの依頼と確認は動かない")}
	}
	return nil
}

// ticketHooks は親子の運用に要る hook の登録とスクリプトが揃っているか。
func ticketHooks(root string) []rules.Problem {
	var out []rules.Problem
	for _, h := range []struct {
		event hookio.Event
		what  string
	}{
		{hookio.SubagentStart, "サブエージェントに開いている子の一覧を渡せない"},
		{hookio.SubagentStop, "範囲外の変更を残したサブエージェントを差し戻せない"},
	} {
		if yes, known := registered(root, h.event); known && !yes {
			out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereProject, Detail: fmt.Sprintf(
				"%s の %s に ccnavi が登録されていない。%s", projectSettingsRel, h.event, h.what)})
		}
	}
	for _, name := range ticketScripts {
		if !ospath.IsFile(ospath.Join(root, config.DefaultProjectHome, config.ScriptsDir, name)) {
			out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereProject, Detail: config.DefaultProjectHome + "/" +
				config.ScriptsDir + "/" + name + " がありません。レビュー済みのマーカーが置かれるまでシェル実行を止めている間、" +
				"例外として通るはずのこの sh の操作もできなくなる"})
		}
	}
	return out
}
