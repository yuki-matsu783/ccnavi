package diagnose

import (
	"bytes"
	"io"
	"maps"
	"slices"
	"sort"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/turn"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/config/risk"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/notice"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/proposal/agree"
)

// boardVersion は `--explain --json` の形の版。読み手（VS Code 拡張）が形の違いに気づけるように。
const boardVersion = 1

// copyStatus は承認済みチケットの置き場で決めたチケットの状態（ボードの `copy.status`）。
type copyStatus string

const (
	statusReview copyStatus = "review"
	statusOpen   copyStatus = "open"
	statusClosed copyStatus = "closed"
	statusNone   copyStatus = "none"
)

// フェーズの状態（ボードの `phases[].state`）。
const (
	phasePlanned = "planned"
	phaseEnded   = "ended"
	phaseActive  = "active"
)

// ExplainJSON は `--explain --json`。`--explain` が言うことのうち、チケットに関わる部分を機械可読で出す。読み手は
// VS Code のボード拡張で、拡張は自分で解釈せず並べるだけにする（答えを 2 か所で出さない）。ネットワークには出ない。
// modeFlag・ticketControlFlag は `--mode`・`--ticket-control` の値（無ければ空）。
func ExplainJSON(stdout, stderr io.Writer, conf *config.Settings, root, modeFlag, ticketControlFlag string) (int, error) {
	return config.ExitOK, writeJSON(stdout, board(conf, root, stderr, modeFlag, ticketControlFlag))
}

// hookSettings は hook がいま使うはずの動作モードとチケット制御を、判定と同じ解決の関数で決める。VS Code 拡張が
// 起こした実行ファイルには settings.json の env が渡らないので、config.ReadHookView が重ね直す。フラグが勝つのも
// 判定と同じ。読めない値の苦情は標準エラーに出す（標準出力は JSON だけ）。
func hookSettings(conf *config.Settings, root string, stderr io.Writer, modeFlag, ticketControlFlag string) (config.Mode, config.Mode) {
	view := config.ReadHookView(root)
	seen := *conf
	seen.Mode = conf.ModeDeclaredInFile
	if seen.Mode == "" {
		seen.Mode = view.Mode
	}
	seen.ModeFromEnvironment = view.Mode
	var complaints bytes.Buffer
	mode := config.ResolveMode(&complaints, modeFlag, &seen)
	control := selfguard.Resolve(&complaints, ticketControlFlag, view.TicketControl, env.TicketControl, selfguard.GateChoices)
	io.WriteString(stderr, complaints.String())
	return mode, control
}

// board はボードの中身。形は docs/11_requirements/json-shapes.md「ボードの JSON」。不備と要対応（markAttention）は、
// 全チケットと親が揃ってから決める（親の有無やフェーズの状態を見るため）。
func board(conf *config.Settings, root string, stderr io.Writer, modeFlag, ticketControlFlag string) *pyjson.Object {
	mode, control := hookSettings(conf, root, stderr, modeFlag, ticketControlFlag)
	// ボードの中身も hook が受け取るはずのチケット制御で組む。settings に出す値と食い違わせない。
	c := *conf
	c.TicketControl = string(control)
	conf = &c
	var problems []string
	trees := tree.AllTrees(root, conf.Projects)
	treeRows := make([]any, len(trees))
	projects := []string{}
	for i, t := range trees {
		treeRows[i] = pyjson.NewObject("name", t.Name, "root", t.Root, "project", t.Project, "kind", string(t.Kind))
		if t.Kind == tree.KindProject {
			projects = append(projects, t.Name)
		}
	}
	turns := []any{}
	for _, t := range turn.Active(conf.State, time.Now()) {
		turns = append(turns, pyjson.NewObject("session", t.Session, "since", t.Since.Unix(), "seen", t.Seen.Unix()))
	}
	payload := pyjson.NewObject(
		"version", boardVersion,
		"root", root,
		"generated_at", fsio.Stamp(),
		"settings", pyjson.NewObject("mode", string(mode), "ticket_control", string(control), "tickets", conf.Tickets,
			"approved", conf.Approved, "projects", conf.Projects),
		"trees", treeRows,
		"projects", projects,
		"layers", layerRecords(conf, root, stderr),
		"sums", sumRecords(conf, root, stderr),
		"problems", nil,
		"pending_approval", []string{},
		"tickets", []any{},
		"parents", []any{},
		// 手元の退避にある閉じたチケット。表示のためだけに載せ、判定には混ぜない。
		"archived", []any{},
		// いまターンの途中にいるセッション。ターン中に設定ファイルを直すと次の呼び出しで戻されうるので、拡張は保存の前に知らせる。
		"turns", turns,
	)
	// 送信の途中で止まった操作（引き継ぎ未完了）。
	if conf.State != "" {
		for _, r := range oplog.Pending(conf.State) {
			problems = append(problems, "引き継ぎ未完了: "+oplog.Summary(r))
		}
	}
	if !conf.TicketsEnabled() {
		problems = append(problems, env.TicketControl+"=disable。チケット制御を使っていない")
		payload.Set("problems", nonNil(problems))
		payload.Set("archived", archivedRecords(root, nil))
		return payload
	}

	everything, scanProblems := ticket.ScanAll(root, conf.Tickets, conf.Projects)
	for _, p := range scanProblems {
		problems = append(problems, p.String())
	}
	// 承認済みの識別子の提案は、承認済みチケットと合わせて正とするツリーを決める。置き場はここで 1 度だけ読み、親ごとの
	// 局面とフェーズまで持ち回る（親ごとに読み直すと、読む回数が親の数とツリーの数の積で増える）。
	raw := approval.ReadRaw(conf, root)
	settled := raw.Everything()
	proposals := ticket.Dedupe(everything, settled)
	// review/ は提案の置き場でもあり承認済みチケットでもあるので、2 つの走査が同じファイルを拾う。パスでまとめる。
	everything = onePerFile(slices.Concat(everything, settled))
	openCopies, notes := approval.Scan(conf, root, false, raw)
	problems = append(problems, notes...)
	closedCopies, notes := approval.Scan(conf, root, true, raw)
	problems = append(problems, notes...)
	reviewCopies, notes := approval.ScanReview(conf, root, raw)
	problems = append(problems, notes...)

	pending, revisions := agree.Waiting(proposals, openCopies, closedCopies, reviewCopies)
	preds := approval.PoolOf(openCopies, reviewCopies, closedCopies, proposals)
	preds.AlignImported(conf, root)
	waiting := map[string]bool{}
	for _, t := range slices.Concat(pending, revisions) {
		waiting[t.ID] = true
	}
	pendingIDs := nonNil(slices.Sorted(maps.Keys(waiting)))
	payload.Set("pending_approval", pendingIDs)

	worktrees := map[string]tree.Tree{}
	for _, t := range trees {
		if t.Kind == tree.KindWorktree {
			worktrees[t.Name] = t
		}
	}
	idx := indexes{
		proposal: approval.ByID(proposals),
		open:     approval.ByID(slices.Concat(openCopies, reviewCopies)),
		closed:   approval.ByID(closedCopies),
	}
	// 同じ識別子があるツリーの全部（seen_in）と、どれが正か決まらないぶん（scattered）。数え方は --lint と同じ関数を通る。
	seen, scattered := map[string][]any{}, map[string][]any{}
	for _, g := range ticket.ByTicket(everything) {
		seen[g.ID] = whereList(g.Tickets)
		scattered[g.ID] = whereList(ticket.Collisions(g.Tickets, true))
	}
	// 承認の時刻。git はツリーごとに 1 回まで。
	times := approval.ApprovedTimes(conf, slices.Concat(byIDValues(slices.Concat(openCopies, reviewCopies)), byIDValues(closedCopies)))
	ids := map[string]bool{}
	for _, m := range []map[string]*ticket.Ticket{idx.proposal, idx.open, idx.closed} {
		for id := range m {
			ids[id] = true
		}
	}
	var rows []*ticketRow
	for _, id := range slices.Sorted(maps.Keys(ids)) {
		rows = append(rows, ticketRecord(conf, root, id, idx, worktrees, seen[id], scattered[id], preds, times))
	}

	// 親ごとの局面とフェーズ。承認済みチケットのある親だけ（承認前の親はフェーズを持たない）。
	parents := slices.Clone(slices.Concat(openCopies, closedCopies))
	sort.SliceStable(parents, func(i, j int) bool { return parents[i].ID < parents[j].ID })
	var families []*parentRow
	for _, p := range parents {
		if !p.IsChild() {
			families = append(families, parentRecord(conf, root, p, idx.closed, raw))
		}
	}
	markAttention(rows, families, waiting)
	tickets := make([]any, len(rows))
	skip := map[[2]string]bool{}
	for i, r := range rows {
		tickets[i] = r.obj
		skip[[2]string{r.project, r.id}] = true
	}
	parentObjs := make([]any, len(families))
	for i, f := range families {
		parentObjs[i] = f.obj
	}
	payload.Set("problems", nonNil(problems))
	payload.Set("tickets", tickets)
	payload.Set("parents", parentObjs)
	payload.Set("archived", archivedRecords(root, skip))
	return payload
}

func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

// byIDValues は approval.ByID の値を、Python の dict と同じ順（識別子の初出の順、値は後のもの）で並べる。
func byIDValues(list []*ticket.Ticket) []*ticket.Ticket {
	index := approval.ByID(list)
	var out []*ticket.Ticket
	done := map[string]bool{}
	for _, t := range list {
		if !done[t.ID] {
			done[t.ID] = true
			out = append(out, index[t.ID])
		}
	}
	return out
}

// objOrNil は読めた JSON のオブジェクトか null。型付きの nil を JSON に渡すと {} になるため、ここで分ける。
func objOrNil(o *pyjson.Object, ok bool) any {
	if !ok || o == nil {
		return nil
	}
	return o
}

// archivedRecords は手元の退避にある閉じたチケット（表示用）。skip の（プロジェクト, 識別子）は tickets の側に出すので出さない。
func archivedRecords(root string, skip map[[2]string]bool) []any {
	out := []any{}
	for _, t := range archive.ClosedTickets(root) {
		if skip[[2]string{t.Project, t.ID}] {
			continue
		}
		out = append(out, pyjson.NewObject("ticket", t.ID, "parent", t.Parent, "phase", phaseOf(t), "title", t.Title,
			"project", t.Project, "path", t.Path, "started_at", t.StartedAt, "completed_at", t.CompletedAt,
			// finish が親のブランチへ合流した子の先頭（表示だけ）
			"merged_sha", t.MergedSHA, "cancelled_at", t.CancelledAt, "cancel_reason", t.CancelReason))
	}
	return out
}

// phaseOf は子のフェーズ番号。親は null。
func phaseOf(t *ticket.Ticket) any {
	if !t.IsChild() {
		return nil
	}
	return t.Phase
}

func ruleRecords(rs *rules.RuleSet) *pyjson.Object {
	o := pyjson.NewObject()
	for _, section := range rules.Sections {
		list := []any{}
		for _, r := range *rs.Section(section) {
			list = append(list, ruleForm(r).put(pyjson.NewObject("id", r.ID, "section", string(r.Decision),
				"source", r.Source, "match", r.Match)).With("message", r.Message))
		}
		o.Set(string(section), list)
	}
	return o
}

func phaseTypeRecord(layerName string, pt *phases.PhaseType) *pyjson.Object {
	return pyjson.NewObject("id", pt.ID, "source", layerName, "kind", string(pt.Kind), "title", pt.Title,
		"review", pt.Review, "scope", scopeWords(pt))
}

func factorRecord(layerName string, f *risk.Factor) *pyjson.Object {
	return pyjson.NewObject("id", f.ID, "source", layerName, "kind", string(f.Kind), "value", factorValue(f),
		"points", f.Points, "message", f.Message)
}

// layerRecords はレイヤーごとの宣言（共通 → 自身 → プロジェクトの名前順）。rules は重複を捨てたあとの、そのレイヤー
// から実際に判定へ入ったぶん。phases と risk はそのレイヤーのファイルに書いてあるぶんで、合成の結果は sums と親の
// フェーズの側に出る。
func layerRecords(conf *config.Settings, root string, stderr io.Writer) []any {
	out := []any{}
	for _, view := range ruleload.Survey(stderr, conf, root) {
		phasesPath := layerConfig(conf, root, view.Name, config.KindPhases)
		riskPath := layerConfig(conf, root, view.Name, config.KindRisk)
		types, phasesUnreadable := layerPhaseTypes(phasesPath)
		factors, riskUnreadable := layerRisk(conf, view.Name, riskPath)
		typeRows := []any{}
		for _, pt := range types {
			typeRows = append(typeRows, phaseTypeRecord(view.Name, pt))
		}
		factorRows := []any{}
		for _, f := range factors {
			factorRows = append(factorRows, factorRecord(view.Name, f))
		}
		rulesObj := pyjson.NewObject("path", view.Path, "unreadable", view.Unreadable)
		for k, v := range ruleRecords(view.RuleSet).All() {
			rulesObj.Set(k.(string), v)
		}
		out = append(out, pyjson.NewObject(
			"name", view.Name,
			"rules", rulesObj,
			"phases", typeRows,
			"risk", pyjson.NewObject("path", riskPath, "unreadable", riskUnreadable, "factors", factorRows),
			"phases_file", pyjson.NewObject("path", phasesPath, "unreadable", phasesUnreadable),
		))
	}
	return out
}

// sumRecords はワークスペースとプロジェクトごとの「共通 + 1 レイヤー」の和。拡張は layers を足し直さず、ここをそのまま
// 見せる。判定の合成と同じ関数（ruleload.SumView・risk.LayerDefinition・phases.LayerTypes）で組む。
func sumRecords(conf *config.Settings, root string, stderr io.Writer) []any {
	out := []any{}
	for _, l := range ruleload.Layers(conf, root) {
		project := ""
		if l.Name != config.LayerSelf {
			project = l.Name
		}
		view := ruleload.SumView(stderr, conf, root, l)
		definition, notes := risk.LayerDefinition(conf, root, project)
		phasesPath := layerConfig(conf, root, l.Name, config.KindPhases)
		_, phasesUnreadable := layerPhaseSet(phasesPath)
		types, _ := phases.LayerTypes(conf, root, project)
		rulesObj := pyjson.NewObject("path", l.Path, "unreadable", view.Unreadable, "missing", view.Missing)
		for k, v := range ruleRecords(view.RuleSet).All() {
			rulesObj.Set(k.(string), v)
		}
		factorRows := []any{}
		for _, f := range definition.Factors {
			factorRows = append(factorRows, factorRecord(orName(f.Source, l.Name), f))
		}
		problemRows := []string{}
		for _, n := range notes {
			problemRows = append(problemRows, n.String())
		}
		typeRows := []any{}
		for pt := range types.All() {
			typeRows = append(typeRows, phaseTypeRecord(orName(pt.Source, l.Name), pt))
		}
		out = append(out, pyjson.NewObject(
			"name", l.Name,
			"layers", []string{config.LayerCommon, l.Name},
			"rules", rulesObj,
			"risk", pyjson.NewObject("levels", risk.EffectiveLevels(definition.Levels).Object(), "factors", factorRows,
				"fallback", definition.Fallback, "problems", problemRows),
			"phases", pyjson.NewObject("path", phasesPath, "unreadable", phasesUnreadable, "types", typeRows),
		))
	}
	return out
}

func orName(source, name string) string {
	if source != "" {
		return source
	}
	return name
}

// where はチケット 1 つの場所。どのツリーの、どの置き場の、どのファイルか。
func whereList(list []*ticket.Ticket) []any {
	out := []any{}
	for _, t := range list {
		out = append(out, pyjson.NewObject("tree", t.Tree, "state", string(t.State), "path", t.Path))
	}
	return out
}

// onePerFile は同じツリーで同じファイルを 2 度読んだぶんをまとめる（先を残す）。キーにツリーを入れるのは、まとめるのを
// 1 つの走査の重なりに限るため。2 つのツリーが同じ実体を指す形は判定の側もまとめないので、ここだけまとめると、
// ボードに何も出ていないのに操作が止まる。
func onePerFile(found []*ticket.Ticket) []*ticket.Ticket {
	var kept []*ticket.Ticket
	seen := map[[2]string]bool{}
	for _, t := range found {
		key := [2]string{t.Tree, pathcase.Fold(ospath.Abspath(t.Path))}
		if seen[key] {
			continue
		}
		seen[key] = true
		kept = append(kept, t)
	}
	return kept
}

type indexes struct{ proposal, open, closed map[string]*ticket.Ticket }

// ticketRow はチケット 1 件の JSON と、不備と要対応を決めるのに読む欄。
type ticketRow struct {
	obj                 *pyjson.Object
	id, parent, project string
	phase               int
	hasProposal         bool
	status              copyStatus
	blocked             string
	worktree            bool
	risk                *pyjson.Object
	scattered           bool
}

// ticketRecord はチケット 1 件。状態は承認済みチケットの置き場で決め、項目は提案があれば提案を、無ければ承認済みの
// コピーを使う。
func ticketRecord(conf *config.Settings, root, id string, idx indexes, worktrees map[string]tree.Tree,
	seenIn, scattered []any, preds approval.Pool, times map[string]approval.ApprovedTime) *ticketRow {
	proposal := idx.proposal[id]
	openCopy, isOpen := idx.open[id]
	closedCopy, isClosed := idx.closed[id]
	copied := openCopy
	if copied == nil {
		copied = closedCopy
	}
	source := proposal
	if source == nil {
		source = copied
	}
	status := statusNone
	switch {
	case isOpen && openCopy.State == place.Review:
		status = statusReview
	case isOpen:
		status = statusOpen
	case isClosed:
		status = statusClosed
	}
	found, hasTree := tree.Lookup(worktrees, id)
	parentTree := hasTree && !source.IsChild()

	branch, remote := "", any(nil)
	if parentTree {
		branch = standing.HeadBranch(found.Root, id)
		remote = notice.RemoteInfo(found.Root)
	}
	unmet := []any{}
	if status != statusClosed {
		for _, p := range approval.UnmetPredecessors(source, preds) {
			unmet = append(unmet, pyjson.NewObject("ticket", p.Ticket, "state", string(p.State), "label", p.Label()))
		}
	}
	var proposalObj any
	if proposal != nil {
		// 親の提案が全体計画（plan:）を持つか。承認前の親は parents に出ないので、ボードが計画の順序を直すタブの入口を
		// 出し分けるのに使う。子は偽。
		proposalObj = pyjson.NewObject("state", string(proposal.State), "tree", proposal.Tree, "tree_root", proposal.TreeRoot,
			"path", proposal.Path, "has_plan", !proposal.IsChild() && proposal.HasPlan())
	}
	blocked := ""
	if isOpen {
		blocked = openCopy.Blocked
	}
	copyObj := pyjson.NewObject("status", string(status))
	if copied != nil {
		at := times[copied.Path]
		copyObj.Set("approved_at", at.At)
		copyObj.Set("approved_from", string(at.Source))
		copyObj.Set("path", copied.Path)
	}
	var worktreeObj *pyjson.Object
	if hasTree {
		worktreeObj = pyjson.NewObject("exists", true, "path", found.Root, "project", found.Project)
	} else {
		worktreeObj = pyjson.NewObject("exists", false, "path", tree.WorktreePath(root, id))
	}
	flowSource := copied
	if flowSource == nil {
		flowSource = source
	}
	var riskRecord, judgeRecord any
	var riskObj *pyjson.Object
	if source.Parent != "" {
		home := approval.HomeDirRead(conf, root, id, source.Parent, source.Project)
		r, ok := marks.ReadRecord(home, source.Parent, id, marks.Risk)
		riskRecord = objOrNil(r, ok)
		if ok {
			riskObj = r
		}
		judgeRecord = objOrNil(marks.ReadRecord(home, source.Parent, id, marks.Judge))
	}
	obj := pyjson.NewObject(
		"ticket", id,
		"parent", source.Parent,
		"phase", phaseOf(source),
		"title", source.Title,
		"project", source.Project,
		"issue", optionalNumber(source.Issue),
		// 親を載せる MR の番号と親のブランチ名。リモートとの差は親のワークツリーがあるときだけ（子は null）。
		"mr", optionalNumber(source.MR),
		"branch", branch,
		"remote", remote,
		"predecessors", nonNil(source.Predecessors),
		// 満たしていない先行。承認と着手はこれが空でなければ止まる。閉じたチケットは空。
		"predecessors_unmet", unmet,
		"human_review", pyjson.NewObject("required", needValue(source.ReviewRequired), "reason", source.ReviewReason),
		"proposal", proposalObj,
		// 完了した親から下書きを作れるか（`ccnavi --templates --draft <親>` が通る形）。
		"template_draft", status == statusClosed && !source.IsChild() && source.HasPlan() && source.State != place.Cancelled,
		// 読めるが信頼できない理由。判定はこのチケットのワークツリーへの書き込みを全部止めるので、素の open に見せない。
		"blocked", blocked,
		"copy", copyObj,
		"worktree", worktreeObj,
		"started_at", source.StartedAt,
		"completed_at", source.CompletedAt,
		"base_sha", source.BaseSHA,
		"merged_sha", source.MergedSHA,
		"cancelled_at", source.CancelledAt,
		"cancel_reason", source.CancelReason,
		// 作業中か（doing/ にあり、着手していて、終わっても取り消されてもいない）。拡張は保存を止めるかをこれで決める。
		"in_progress", isOpen && openCopy.State == place.Doing && openCopy.InProgress(),
		"seen_in", nonNilAny(seenIn),
		"scattered", nonNilAny(scattered),
		"flow", flowObject(flow.FlowInfo(conf, root, flowSource)),
		"risk", riskRecord,
		"judge", judgeRecord,
	)
	return &ticketRow{obj: obj, id: id, parent: source.Parent, project: source.Project, phase: source.Phase,
		hasProposal: proposal != nil, status: status, blocked: blocked, worktree: hasTree, risk: riskObj,
		scattered: len(scattered) > 0}
}

func nonNilAny(s []any) []any {
	if s == nil {
		return []any{}
	}
	return s
}

// optionalNumber は 0（無し）を null にする。
func optionalNumber(n int) any {
	if n == 0 {
		return nil
	}
	return n
}

// needValue はレビューの要否の宣言の JSON（真・偽・null）。
func needValue(n ticket.Need) any {
	switch n {
	case ticket.Needed:
		return true
	case ticket.Unneeded:
		return false
	}
	return nil
}

// flowObject はボードのフローの欄。draft はエージェントの下書き（効力は無い）。locked は判定がそのフローへの書き込みを
// 止めているか、lock_kind はその種類（止めていなければ null）。
func flowObject(info *flow.Info) any {
	if info == nil {
		return nil
	}
	var kind any
	if info.Locked() {
		kind = string(info.LockKind)
	}
	return pyjson.NewObject("path", info.Path, "rel", info.Rel, "tree", info.Tree, "exists", info.Exists,
		"linked", info.Linked, "locked", info.Locked(), "lock_kind", kind,
		"draft", pyjson.NewObject("path", info.Draft.Path, "rel", info.Draft.Rel, "exists", info.Draft.Exists,
			"linked", info.Draft.Linked))
}

// parentRow は親 1 件の JSON と、要対応を決めるのに読むフェーズの欄。
type parentRow struct {
	obj    *pyjson.Object
	id     string
	phases []phaseRow
}

type phaseRow struct {
	number                               int
	gateClosed, reviewWaiting, escalates bool
}

func (p phaseRow) needsUser() bool { return p.gateClosed || p.reviewWaiting || p.escalates }

// phaseRecord はフェーズ 1 つ。判定と同じ Phase から組む。manual は手で done/ に動かされた子の確認待ちなど（無ければ空）。
func phaseRecord(ph *phase.Phase, manual string) (*pyjson.Object, phaseRow) {
	state := phaseActive
	switch {
	case len(ph.Tickets) == 0:
		state = phasePlanned
	case ph.Ended():
		state = phaseEnded
	}
	typeName := ""
	if ph.Item != nil {
		typeName = ph.Item.Type
	}
	ids := []string{}
	states := pyjson.NewObject()
	for _, t := range ph.Tickets {
		ids = append(ids, t.ID)
		states.Set(t.ID, string(ph.States[t.ID]))
	}
	markObj := pyjson.NewObject()
	for _, kind := range marks.Kinds {
		if data, ok := ph.Marks[kind]; ok {
			markObj.Set(string(kind), data)
		}
	}
	var reviewAt any
	if at, ok := ph.ReviewAt(); ok {
		reviewAt = at
	}
	covers := []any{}
	for _, n := range ph.Covers() {
		covers = append(covers, n)
	}
	obj := pyjson.NewObject(
		"number", ph.Number,
		"type", typeName,
		"title", ph.Title(),
		"label", ph.Label(),
		"state", state,
		"tickets", ids,
		"states", states,
		"marks", markObj,
		"review_required", ph.ReviewRequired(),
		"gate_closed", ph.GateClosed(),
		"review_waiting", ph.ReviewWaiting(),
		"deferred", ph.Deferred(),
		"review_at", reviewAt,
		"covers", covers,
		"risk", objOrNil(ph.Risk(), true),
		"risk_escalates", ph.RiskEscalates(),
		"risk_line", ph.RiskLine(),
		"manual", manual,
	)
	return obj, phaseRow{number: ph.Number, gateClosed: ph.GateClosed(), reviewWaiting: ph.ReviewWaiting(),
		escalates: ph.RiskEscalates()}
}

// parentRecord は親 1 件。局面、計画、親のマーカー、フェーズの並び。閉じた親は局面を持たない（「クローズ可」と言っても
// 意味が無い）。
func parentRecord(conf *config.Settings, root string, parent *ticket.Ticket, closedIndex map[string]*ticket.Ticket,
	raw *approval.Raw) *parentRow {
	home := approval.HomeDirRead(conf, root, parent.ID, "", parent.Project)
	_, closed := closedIndex[parent.ID]
	stage := ""
	if !closed {
		stage = phase.Stage(conf, root, parent, raw)
	}
	plan := []any{}
	for i := range parent.Plan {
		plan = append(plan, parent.Plan[i].Raw())
	}
	var feedback any
	if parent.FeedbackPlanned {
		items := []any{}
		for i := range parent.Feedback {
			items = append(items, parent.Feedback[i].Raw())
		}
		feedback = items
	}
	list := phase.Phases(conf, root, parent.ID, raw)
	phaseObjs := []any{}
	var rows []phaseRow
	for _, ph := range list {
		line := ""
		if ph.ReviewRequired() {
			line = phase.AssessManual(conf, root, parent, ph, list, phase.ManualOptions{}).Line()
		}
		obj, row := phaseRecord(ph, line)
		phaseObjs = append(phaseObjs, obj)
		rows = append(rows, row)
	}
	obj := pyjson.NewObject(
		"ticket", parent.ID,
		"closed", closed,
		"stage", stage,
		"plan", plan,
		"feedback", feedback,
		"close_early", objOrNil(marks.ReadParent(home, parent.ID, marks.CloseEarly)),
		"ready", objOrNil(marks.ReadParent(home, parent.ID, marks.Ready)),
		"closed_record", objOrNil(marks.ReadParent(home, parent.ID, marks.Closed)),
		"phases", phaseObjs,
	)
	return &parentRow{obj: obj, id: parent.ID, phases: rows}
}
