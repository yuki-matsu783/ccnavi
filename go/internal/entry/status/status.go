package status

import (
	"cmp"
	"fmt"
	"io"
	"slices"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/notice"
	"ccnavi/internal/tickets/phase"
)

const (
	syncScript = "ccnavi-sync.sh"
	pushScript = "ccnavi-user-push-approved.sh"
)

// 読み手が文の形で探すので、文面を変えない。
const (
	msgTooMany  = "ccnavi: ticket status に指定できる親チケットは1件のみです。\n"
	msgNotFound = "ccnavi: 親 %s のチケットが見つからない（%s/ と %s/ の下を全ツリーで探した）\n"
	msgNothing  = "ccnavi: 作業中・レビュー待ち・承認待ちのチケットはありません。\n"
	msgHead     = "ccnavi: チケットの状態。手元の置き場・git だけを読む（リモートからの取得は行いません）。\n" +
		"  push済みかどうかは、最後に取得したリモートの状態に基づくため、最新の状態と異なる場合があります。" +
		"最新にするには先に '%s <親>' を実行してください。\n"
	msgBusyParents = "\n== このPCで作業中の親\n"
	msgFamily      = "\n== 親子 %s\n"
	msgChildTree   = "子のワークツリー: %s  %s\n"
	msgUnfinished  = "\n== 引き継ぎ未完了（ホストへの送信が終わっていない操作）\n"
	msgResume      = "  続きは 'ccnavi ops show <操作ID>' で確かめ、decide は --resume で再開する\n"
)

// entry は 1 つの識別子の今。hits が 2 つ以上なら、どれが正か決まらない。
type entry struct {
	ident string
	hits  []*ticket.Ticket
}

func (e *entry) t() *ticket.Ticket { return e.hits[0] }

// home は親子のチケットの親の識別子。
func (e *entry) home() string { return cmp.Or(e.t().Parent, e.t().ID) }

func closedState(s place.State) bool { return s == place.Done || s == place.Cancelled }

// Run は `ccnavi ticket status [<親>]` の状態を書き、終了コード（0・1）を返す。args は `ticket status` の後の語（親は
// 0 か 1 件。子の識別子なら親子のチケットの親に読む）。親を渡して何も見つからなければ 1。
func Run(stdout, stderr io.Writer, root string, conf *config.Settings, args []string) int {
	if len(args) > 1 {
		fmt.Fprint(stderr, msgTooMany)
		return config.ExitError
	}
	family := ""
	if len(args) == 1 && args[0] != "" {
		family = wap.FamilyOf(args[0])
	}
	return run(stdout, stderr, root, conf, family)
}

// run は状態を出す。family が空なら作業中の親すべて、あればその親だけ。
//
// 処理フロー:
//  1. 置き場の全チケット（作業中・レビュー待ち・閉じた・提案）を読む
//  2. 識別子ごとにまとめ、出す親（family）を決める
//  3. 親ごとに、子と手で動かされた子の状態を出す
//  4. 送り終えていない操作を最後に出す
func run(stdout, stderr io.Writer, root string, conf *config.Settings, family string) int {
	raw := approval.ReadRaw(conf, root)
	doing, notes := approval.Scan(conf, root, false, raw)
	closed, more := approval.Scan(conf, root, true, raw)
	notes = append(notes, more...)
	review, more := approval.ScanReview(conf, root, raw)
	notes = append(notes, more...)
	proposals := approval.ReadProposals(conf, root, raw).Kept

	// 識別子ごとのまとまり（entry）に、同じ識別子のチケットを集める。order は初めて出た順。
	var order []*entry
	entries := map[string]*entry{}
	add := func(t *ticket.Ticket) {
		e, ok := entries[t.ID]
		if !ok {
			e = &entry{ident: t.ID}
			entries[t.ID] = e
			order = append(order, e)
		}
		e.hits = append(e.hits, t)
	}
	for _, list := range [][]*ticket.Ticket{doing, review, closed} {
		for _, t := range list {
			add(t)
		}
	}
	// 承認済みチケットと同じ識別子の todo/ は改版の候補か書き損じで、状態の相手ではない。
	revisions := map[string]bool{}
	var todo []*ticket.Ticket
	for _, t := range proposals {
		if t.State == place.Todo {
			todo = append(todo, t)
			if _, ok := entries[t.ID]; ok {
				revisions[t.ID] = true
			}
		}
	}
	for _, t := range todo {
		if !revisions[t.ID] {
			add(t)
		}
	}

	// 出す親を決める。指定があれば存在を確かめ、無ければ閉じていない状態のチケットがある親を名前順に出す。
	var families []string
	if family != "" {
		if !slices.ContainsFunc(order, func(e *entry) bool { return e.home() == family }) {
			fmt.Fprintf(stderr, msgNotFound, family, conf.Approved, conf.Tickets)
			for _, note := range notes {
				fmt.Fprintf(stderr, "  %s\n", note)
			}
			return config.ExitError
		}
		families = []string{family}
	} else {
		busy := map[string]bool{}
		for _, e := range order {
			if slices.ContainsFunc(e.hits, func(h *ticket.Ticket) bool { return !closedState(h.State) }) {
				busy[e.home()] = true
			}
		}
		for name := range busy {
			families = append(families, name)
		}
		slices.Sort(families)
	}
	if len(families) == 0 {
		fmt.Fprint(stdout, msgNothing)
		unfinishedOps(stdout, conf)
		return config.ExitOK
	}

	pool := approval.PoolOf(doing, review, closed, proposals)
	// 承認と lint と同じ引き方にする。
	pool.AlignImported(conf, root)
	var all []*ticket.Ticket
	for _, e := range order {
		all = append(all, e.hits...)
	}
	f := &familyView{
		root: root, conf: conf, entries: entries, pool: pool, raw: raw,
		times:    approval.ApprovedTimes(conf, slices.Concat(doing, review, closed)),
		files:    fileStates(all),
		fams:     standing.NewFamilies(conf, root),
		ticketSh: config.ScriptCommand(root, config.TicketWrapper),
		pushSh:   config.ScriptCommand(root, pushScript),
	}
	fmt.Fprintf(stdout, msgHead, config.ScriptCommand(root, syncScript))
	rows := notice.Collect(conf, root, doing, review, closed, proposals)
	if family == "" && len(rows) > 0 {
		// 引数なし: このPCで作業中の親を 1 行ずつ、そのあとに親ごとの詳細。
		fmt.Fprint(stdout, msgBusyParents)
		for _, row := range rows {
			fmt.Fprintln(stdout, notice.Line(row))
		}
	}
	// 親ごとに、親、子の順（同じ種類の中ではフェーズ番号、識別子の順）で出す。
	for _, name := range families {
		var members []*entry
		for _, e := range order {
			if e.home() == name {
				members = append(members, e)
			}
		}
		slices.SortStableFunc(members, func(a, b *entry) int {
			return cmp.Or(cmp.Compare(boolKey(a.t().IsChild()), boolKey(b.t().IsChild())),
				cmp.Compare(a.t().Phase, b.t().Phase), cmp.Compare(a.ident, b.ident))
		})
		f.name, f.wapDone = name, false
		fmt.Fprintf(stdout, msgFamily, name)
		if family == "" {
			if i := slices.IndexFunc(rows, func(r notice.Row) bool { return r.Ident == name }); i >= 0 {
				for _, c := range notice.ChildStates(root, rows[i], doing, review) {
					fmt.Fprintf(stdout, msgChildTree, c.Ident, c.State)
				}
			}
		}
		for _, e := range members {
			for _, line := range f.describe(e, revisions[e.ident]) {
				fmt.Fprintln(stdout, line)
			}
		}
		for _, line := range manualLines(root, conf, name, doing, raw) {
			fmt.Fprintln(stdout, line)
		}
	}
	unfinishedOps(stdout, conf)
	return config.ExitOK
}

// boolKey は並べ替えの比較に使う。偽（親）を真（子）より前にする。
func boolKey(b bool) int {
	if b {
		return 1
	}
	return 0
}

// manualLines は、手で done/ に動かされた子の確認待ち・未共有などを、フェーズごとに 1 行で出す。
func manualLines(root string, conf *config.Settings, parentID string, doing []*ticket.Ticket, raw *approval.Raw) []string {
	parent := approval.ByID(doing)[parentID]
	if parent == nil || parent.IsChild() {
		return nil
	}
	phases := phase.Phases(conf, root, parentID, raw)
	var out []string
	for _, p := range phases {
		if !p.ReviewRequired() {
			continue
		}
		if line := phase.AssessManual(conf, root, parent, p, phases, phase.ManualOptions{}).Line(); line != "" {
			out = append(out, fmt.Sprintf("  フェーズ %d: %s", p.Number, line))
		}
	}
	return out
}

// unfinishedOps は送り終えていない操作（引き継ぎ未完了）があれば、1 行ずつ出す。
func unfinishedOps(stdout io.Writer, conf *config.Settings) {
	if conf.State == "" {
		return
	}
	left := oplog.Pending(conf.State)
	if len(left) == 0 {
		return
	}
	fmt.Fprint(stdout, msgUnfinished)
	for _, r := range left {
		fmt.Fprintf(stdout, "  %s\n", oplog.Summary(r))
	}
	fmt.Fprint(stdout, msgResume)
}
