package phase

import (
	"strconv"
	"strings"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/config/risk"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// 止めている間の呼び名。依頼のマーカーが境目で、未依頼はエージェントの番、依頼済みはユーザ待ち。
const (
	LabelPreparing = "レビュー準備中"
	LabelWaiting   = "レビュー待ち"
)

// Phase は 1 つの親の 1 つのフェーズ。親が計画を持てば Item・Type・Owner が入り、持たなければ番号だけで、
// 子の human_review からレビューの要否を決める。
type Phase struct {
	Parent  string
	Number  int
	Tickets []*ticket.Ticket
	// States は子の識別子 → 置き場（状態）。
	States map[string]place.State
	Marks  map[marks.Kind]*pyjson.Object
	// 計画があるときだけ。Item は計画のステップ、Type は定義、Owner は親の承認済みチケット。
	Item  *ticket.PlanItem
	Type  *phases.PhaseType
	Owner *ticket.Ticket
	// Risks は子の識別子 → 閉じるときに数えた実績のリスクの記録。
	Risks map[string]*pyjson.Object
	// CoveredReviews は延期を引き受けた前のフェーズが、定義として宣言している「レビューが要るか」。
	CoveredReviews []bool
}

func newPhase(parent string, number int) *Phase {
	return &Phase{
		Parent: parent, Number: number,
		States: map[string]place.State{},
		Marks:  map[marks.Kind]*pyjson.Object{},
		Risks:  map[string]*pyjson.Object{},
	}
}

func (p *Phase) has(kind marks.Kind) bool {
	_, ok := p.Marks[kind]
	return ok
}

// Risk はこのフェーズの実績のリスク（子の最大。同点なら子の並びで先のもの）。無ければ nil。
func (p *Phase) Risk() *pyjson.Object {
	var best *pyjson.Object
	for _, t := range p.Tickets {
		record, ok := p.Risks[t.ID]
		if !ok {
			continue
		}
		if best == nil || points(record) > points(best) {
			best = record
		}
	}
	return best
}

// points は記録の点（Python の int(record.get("points") or 0)）。読めない値は 0。
func points(record *pyjson.Object) int {
	n, _ := pyfmt.IntOf(record.Value("points"))
	return n
}

// RiskEscalates は実績のリスクが、宣言に関わらずレビューが要る扱いにするレベルか。
func (p *Phase) RiskEscalates() bool {
	record := p.Risk()
	if record == nil {
		return false
	}
	level := ""
	if v := record.Value("level"); pyfmt.Truthy(v) {
		level = pyfmt.Str(v)
	}
	return risk.Escalates(level)
}

// RiskLine はユーザ向けの 1 行（`リスク: 58 (HIGH) — …`）。無ければ空。
func (p *Phase) RiskLine() string {
	if body := p.RiskBody(); body != "" {
		return "リスク: " + body
	}
	return ""
}

// RiskBody は RiskLine から頭の `リスク: ` を除いたもの。見出しを自分で付ける側が使う。
func (p *Phase) RiskBody() string {
	record := p.Risk()
	if record == nil {
		return ""
	}
	var hits []string
	if list, ok := record.Value("hits").([]any); ok {
		for _, h := range list {
			if hit, ok := h.(*pyjson.Object); ok {
				detail := ""
				if v := hit.Value("detail"); pyfmt.Truthy(v) {
					detail = pyfmt.Str(v)
				}
				hits = append(hits, detail)
			}
		}
	}
	text := orDefault(record, "points", 0) + " (" + orDefault(record, "level", "") + ")"
	if len(hits) > 0 {
		text += " — " + strings.Join(hits, "、")
	}
	return text
}

// orDefault は Python の str(record.get(key, fallback))。
func orDefault(record *pyjson.Object, key string, fallback any) string {
	if v, ok := record.Get(key); ok {
		return pyfmt.Str(v)
	}
	return pyfmt.Str(fallback)
}

// Planned は親の計画のステップか。
func (p *Phase) Planned() bool { return p.Item != nil }

// Title はユーザ向けの名前。定義が無ければステップの型の名前、計画も無ければ空。
func (p *Phase) Title() string {
	if p.Type != nil {
		return p.Type.Title
	}
	if p.Item != nil {
		return p.Item.Type
	}
	return ""
}

// Label は `1（設計）` の形。名前が無ければ番号だけ。
func (p *Phase) Label() string {
	if title := p.Title(); title != "" {
		return strconv.Itoa(p.Number) + "（" + title + "）"
	}
	return strconv.Itoa(p.Number)
}

// Deferred はレビューを後のフェーズに延期したステップか。
func (p *Phase) Deferred() bool { return p.Item != nil && p.Item.Deferred }

// ReviewAt はこのフェーズのレビューが行われる番号。延期なら引き受ける番号で、引き受け手が無ければ偽。
func (p *Phase) ReviewAt() (int, bool) {
	if p.Owner == nil || !p.Planned() {
		return p.Number, true
	}
	return p.Owner.ReviewAt(p.Number)
}

// Covers はこのフェーズのレビューが含む、延期した前のフェーズの番号。
func (p *Phase) Covers() []int {
	if p.Owner == nil || !p.Planned() {
		return nil
	}
	return p.Owner.CoveredBy(p.Number)
}

// Ended は終わったか。作業中・未着手の子が無く、レビュー待ちか閉じた子が 1 枚以上。取り消しだけでは終わらない。
func (p *Phase) Ended() bool {
	if len(p.States) == 0 {
		return false
	}
	closed := false
	for _, s := range p.States {
		switch s {
		case place.Todo, place.Doing, "":
			return false
		case place.Review, place.Done:
			closed = true
		}
	}
	return closed
}

// DeclaredReview は計画のステップと定義が言う「レビューが要るか」。ステップの指定が定義より優先し、どちらも
// 言わなければ Unsaid。
func (p *Phase) DeclaredReview() ticket.Need {
	item, typ := ticket.Unsaid, ticket.Unsaid
	if p.Item != nil {
		item = p.Item.Review
	}
	if p.Type != nil {
		typ = ticket.NeedOf(p.Type.Review)
	}
	return ticket.ChosenReview(item, typ)
}

// ReviewRequired はこのフェーズの終わりにユーザのレビューが要るか。
//
// 要るかを言えるのは計画のステップ・定義・引き受けた延期で、1 つでも要ると言えば要る。子の required: true と
// 実績のリスクは要るの側にだけ上げる。根拠が 1 つも無ければ（計画が無い・定義が読めない）、子が要らないと
// 明示しない限り要る。要らない側に倒すと、定義が読めないときにレビューが消える。延期したフェーズは自分では
// 要らず、引き受けたフェーズが要る。
func (p *Phase) ReviewRequired() bool {
	if p.Deferred() {
		return false
	}
	var closed []*ticket.Ticket
	for _, t := range p.Tickets {
		if s := p.States[t.ID]; s == place.Review || s == place.Done {
			closed = append(closed, t)
		}
	}
	needed := p.RiskEscalates()
	for _, t := range closed {
		needed = needed || t.ReviewRequired == ticket.Needed
	}
	declared := p.CoveredReviews
	if d := p.DeclaredReview(); d.Said() {
		declared = append([]bool{d == ticket.Needed}, declared...)
	}
	if len(declared) > 0 {
		for _, d := range declared {
			needed = needed || d
		}
		return needed
	}
	for _, t := range closed {
		needed = needed || t.ReviewRequired != ticket.Unneeded
	}
	return needed
}

// GateClosed はレビューが済むまで止めているか。止める条件はここだけに置き、hook・ボード・局面が読む。
func (p *Phase) GateClosed() bool {
	return p.Ended() && p.ReviewRequired() && !p.has(marks.Reviewed)
}

// ReviewWaiting は依頼を出したあとの待ちか（ボードの「受け入れ」はこれで出し分ける）。
func (p *Phase) ReviewWaiting() bool { return p.GateClosed() && p.has(marks.Requested) }

// ReviewLabel は止めている間の呼び名。未依頼なら動くのはエージェントでレビュー準備中、依頼済みならレビュー待ち。
func (p *Phase) ReviewLabel() string {
	if p.ReviewWaiting() {
		return LabelWaiting
	}
	return LabelPreparing
}

// ReviewedOrSkipped はレビューが済んでいるか、要らないか。延期は引き受けるフェーズに委ねる。
func (p *Phase) ReviewedOrSkipped() bool {
	return p.Deferred() || !p.ReviewRequired() || p.has(marks.Reviewed)
}

// coveredReview は延期を引き受けた側に渡す「覆っている分のレビューが要るか」。読めなければ要る（延期できるのは
// レビューのある定義だけなので、読めなくなったことを理由にレビューを消さない）。
func coveredReview(p *Phase) bool {
	if p == nil {
		return true
	}
	d := p.DeclaredReview()
	return !d.Said() || d == ticket.Needed
}
