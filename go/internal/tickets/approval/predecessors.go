package approval

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

// PredState は先行 1 本の今。満たしたとみなすのは PredDone だけ（done/ に在り、取り消しでない）。
type PredState string

const (
	PredTodo                = PredState(place.Todo)
	PredDoing               = PredState(place.Doing)
	PredReview              = PredState(place.Review)
	PredDone                = PredState(place.Done)
	PredCancelled           = PredState(place.Cancelled)
	PredMissing   PredState = "missing"
	PredScattered PredState = "scattered"
	// 形の上で満たせない先行。待っても通らないので、ふつうの error にする。
	PredSelf     PredState = "self"     // 自分自身
	PredAncestor PredState = "ancestor" // 自分の親（親は子より先に閉じない）
	PredCycle    PredState = "cycle"    // 先行を辿ると自分に戻る
	// PredUndecided は取り込み済みの親子のチケットの先行で、その親子のチケットが決まらない・親のブランチ上に無い。
	PredUndecided PredState = "undecided"
)

var predLabels = map[PredState]string{
	PredTodo:      "承認待ち（todo/）",
	PredDoing:     "作業中（doing/）",
	PredReview:    "レビュー待ち（review/）",
	PredCancelled: "取り消し済み（done/ で cancelled_at を持つ）",
	PredMissing: "どの置き場にもありません。閉じて片付いた親子のチケットなら、" +
		"取り込み済みなので predecessors から削除してください",
	PredScattered: "複数の場所にある",
	PredSelf:      "自分自身",
	PredAncestor:  "自分の親",
	PredCycle:     "先行を辿ると自分に戻る",
	PredUndecided: "親子のチケットが決まらない",
}

// Predecessor は先行 1 本の今。Where は複数の場所にあるときの在り処（`ツリー:置き場` の並び）、循環の経路、
// 決まらない理由。
type Predecessor struct {
	Ticket string
	State  PredState
	Where  string
}

// Met は満たしているか。
func (p Predecessor) Met() bool { return p.State == PredDone }

// Waiting は先行が閉じれば満たす状態か。取り消し・無い・複数の場所は、待っても満たさない。
func (p Predecessor) Waiting() bool {
	return p.State == PredTodo || p.State == PredDoing || p.State == PredReview
}

// Label は見せる文。
func (p Predecessor) Label() string {
	text, ok := predLabels[p.State]
	if !ok {
		text = string(p.State)
	}
	if p.Where != "" {
		return text + ": " + p.Where
	}
	return text
}

// Pool は先行を引く対応表（識別子 → 正とする側のチケットの全部）。PoolOf で作り、AlignImported で親子の
// チケットの側に読み直す。nil は何も引けない表。
type Pool map[string][]poolHit

// poolHit は対応表の 1 項目。undecided が空でなければ「親子のチケットが決まらない」で、その理由。
type poolHit struct {
	t         *ticket.Ticket
	undecided string
}

func (h poolHit) state() PredState {
	if h.undecided != "" {
		return PredUndecided
	}
	return PredState(h.t.State)
}

// PoolOf は、先行を引く対応表を組む。承認済みチケット（作業中・レビュー待ち・閉じた）は、どれも数える。todo/ の提案は、同じ識別子の承認済みチケットがどこにも無いときだけ数える。在れば、改版の候補か書き損じだからである。2 つ以上残れば、どれが正かが決まらない。手元の退避（logs/archive/）は PC ごとのもので答えが割れるので、読まない。
func PoolOf(open, review, closed, proposals []*ticket.Ticket) Pool {
	pool := Pool{}
	for _, list := range [][]*ticket.Ticket{open, review, closed} {
		for _, t := range list {
			pool[t.ID] = append(pool[t.ID], poolHit{t: t})
		}
	}
	for _, t := range proposals {
		// 2 つめの todo/ の提案は数えない（1 つめで識別子が対応表に入る）。
		if _, known := pool[t.ID]; t.State == place.Todo && !known {
			pool[t.ID] = append(pool[t.ID], poolHit{t: t})
		}
	}
	return pool
}

// AlignImported は、親子のチケットの先行を、その親のブランチ上のチケットで読み直す。Chrome は参照の閉包の親子のチケットから引くので、手元もそれに揃える。通る向きには読み替えない。厳しくする向きだけを読み替える。親子のチケットが決まらないとき、または親のワークツリーにその識別子が無いときは「決まらない」になる。親のワークツリーのチケットが 1 つで閉じていなければ、それを採る。閉じている、または 2 つ以上あるときは、元の答えのまま使う。
func (pool Pool) AlignImported(conf *config.Settings, root string) {
	fams := standing.NewFamilies(conf, root)
	for ident, hits := range pool {
		if len(hits) == 0 {
			continue
		}
		st := FamilyStanding(conf, root, hits[0].t, fams)
		if st.Stop != "" {
			pool[ident] = []poolHit{{t: hits[0].t, undecided: st.Stop}}
			continue
		}
		var mine []poolHit
		for _, h := range hits {
			if st.Home != nil && standing.SameTree(h.t.TreeRoot, st.Home.Root) {
				mine = append(mine, h)
			}
		}
		switch {
		case len(mine) == 0:
			var trees []string
			for _, h := range hits {
				name := h.t.Tree
				if name == "" {
					name = "(" + workspaceLabel + ")"
				}
				trees = append(trees, name)
			}
			pool[ident] = []poolHit{{t: hits[0].t, undecided: "親のブランチ " + st.Family + " のワークツリーに " + ident +
				" のチケットがありません（" + strings.Join(trees, ", ") + " にしか無い）"}}
		case len(mine) == 1 && mine[0].state() != PredDone:
			pool[ident] = mine
		}
	}
}

// predecessorStates はこのチケットの先行のそれぞれの今。順序は predecessors の順（重ねて書いたものは 1 つ）。
func predecessorStates(t *ticket.Ticket, pool Pool) []Predecessor {
	var out []Predecessor
	seen := map[string]bool{}
	for _, ident := range t.Predecessors {
		if seen[ident] {
			continue
		}
		seen[ident] = true
		hits := pool[ident]
		switch {
		case ident == t.ID:
			out = append(out, Predecessor{Ticket: ident, State: PredSelf})
			continue
		case t.Parent != "" && ident == t.Parent:
			out = append(out, Predecessor{Ticket: ident, State: PredAncestor})
			continue
		}
		if len(hits) == 1 && hits[0].state() != PredDone {
			if loop := loopBack(t.ID, ident, pool); loop != nil {
				out = append(out, Predecessor{ident, PredCycle, strings.Join(append([]string{t.ID}, loop...), " → ")})
				continue
			}
		}
		switch {
		case len(hits) == 0:
			out = append(out, Predecessor{Ticket: ident, State: PredMissing})
		case len(hits) == 1 && hits[0].undecided != "":
			out = append(out, Predecessor{ident, PredUndecided, hits[0].undecided})
		case len(hits) > 1:
			where := make([]string, 0, len(hits))
			for _, h := range hits {
				name := h.t.Tree
				if name == "" {
					name = "(" + workspaceLabel + ")"
				}
				where = append(where, name+":"+string(h.state()))
			}
			out = append(out, Predecessor{ident, PredScattered, strings.Join(where, ", ")})
		default:
			state := hits[0].state()
			if state == "" {
				state = PredDoing
			}
			out = append(out, Predecessor{Ticket: ident, State: state})
		}
	}
	return out
}

// loopBack は first から先行を辿って origin に戻る経路（戻らなければ nil）。辿るのは対応表で 1 つに決まる子だけ
// （決まらないものは別の苦情になる）。閉じたチケットは満たしているので、そこで循環が切れる。
func loopBack(origin, first string, pool Pool) []string {
	type frame struct {
		ident string
		route []string
	}
	seen := map[string]bool{}
	stack := []frame{{first, []string{first}}}
	for len(stack) > 0 {
		top := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if seen[top.ident] {
			continue
		}
		seen[top.ident] = true
		hits := pool[top.ident]
		if len(hits) != 1 || hits[0].state() == PredDone || !hits[0].t.IsChild() {
			continue
		}
		for _, next := range hits[0].t.Predecessors {
			route := append(append([]string(nil), top.route...), next)
			if next == origin {
				return route
			}
			stack = append(stack, frame{next, route})
		}
	}
	return nil
}

// UnmetPredecessors は満たしていない先行。子だけが先行を持つ。
func UnmetPredecessors(t *ticket.Ticket, pool Pool) []Predecessor {
	if !t.IsChild() {
		return nil
	}
	var out []Predecessor
	for _, p := range predecessorStates(t, pool) {
		if !p.Met() {
			out = append(out, p)
		}
	}
	return out
}

// PredecessorProblems は、承認を通さない先行の苦情を返す。先行が閉じれば同じ提案が通るもの（承認待ち・作業中・レビュー待ち）は rules.KindNotYet にする。--lint は warn で言う。取り消し・自分自身・親・循環・決まらない・どこにも無い・複数の場所、は待っても満たさないので error にする。approvedRel は承認済みの置き場で、文に出す。
func PredecessorProblems(t *ticket.Ticket, pool Pool, approvedRel string) []rules.Problem {
	var out []rules.Problem
	for _, p := range UnmetPredecessors(t, pool) {
		var detail string
		kind := ""
		switch p.State {
		case PredTodo, PredDoing, PredReview:
			kind = rules.KindNotYet
			detail = "先行 " + p.Ticket + " が完了していない（いまは " + p.Label() + "）。" + p.Ticket + " が " +
				approvedRel + "/" + string(place.Done) + "/ に入る（作業を終え、レビューが必要なら" +
				"ユーザのレビューが済む）まで承認しない。先行が不要なら " +
				"predecessors から削除して" +
				"出し直してください"
		case PredCancelled:
			detail = "先行 " + p.Ticket + " は" + p.Label() + "。取り消した先行は満たせないので承認しない。" +
				"predecessors から削除して出し直してください"
		case PredSelf, PredAncestor, PredCycle:
			why := map[PredState]string{
				PredSelf: "自分自身を先行に挙げている。自分が閉じるのを待つことはできない",
				PredAncestor: "自分の親を先行に挙げている。親は子が全部閉じてから閉じるので、" +
					"待っても満たされない",
				PredCycle: "先行が循環している（" + p.Where + "）。どれも他が閉じるのを待つので、" +
					"待っても満たされない",
			}[p.State]
			detail = "先行 " + p.Ticket + " は" + predLabels[p.State] + "。" + why + "。" +
				"predecessors から削除して出し直してください"
		case PredUndecided:
			detail = "先行 " + p.Ticket + " が属する親子のチケットが決まらない（" + p.Where + "）。" +
				"取り込み済みの親子のチケットの先行は" +
				"その親のブランチ上のチケットで確かめる。親のワークツリーを切り直すか、" +
				"'ccnavi-sync.sh' で取り込み直してから出し直してください"
		case PredMissing:
			detail = "先行 " + p.Ticket + " がどの置き場（todo/・doing/・review/・done/）にもありません。" +
				"閉じて片付いた親子のチケットなら、取り込み済みなので predecessors から" +
				"削除してください。表記の誤りなら直して出し直してください"
		default:
			detail = "先行 " + p.Ticket + " が" + p.Label() + "。" +
				"どれが正か決まらないので満たしたとみなさない。" +
				"先に 1 つに決めてください" +
				"（先へ進んだ側を合流させるか、残ったワークツリーを片付ける）"
		}
		out = append(out, rules.Problem{Severity: rules.Error, Rule: t.ID, Detail: detail, Kind: kind})
	}
	return out
}

// PredecessorPool はいまの置き場から先行を引く対応表を組む（AlignImported まで当てたもの）。
func PredecessorPool(conf *config.Settings, root string) Pool {
	raw := ReadRaw(conf, root)
	open, _ := Scan(conf, root, false, raw)
	review, _ := ScanReview(conf, root, raw)
	closed, _ := Scan(conf, root, true, raw)
	proposals, _ := ticket.Scan(root, conf.Tickets, conf.Projects, nil)
	pool := PoolOf(open, review, closed, proposals)
	pool.AlignImported(conf, root)
	return pool
}
