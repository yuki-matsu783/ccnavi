package ticket

import (
	"fmt"
	"slices"
	"strings"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/wiki"
	"ccnavi/internal/tickets/model/place"
)

const (
	// Fence は frontmatter の囲い（yaml.FrontBody が読むのと同じ行）。
	Fence = yaml.FrontFence
	// Version はこのビルドが読めるチケット書式の版。
	Version = 1

	// MaintenanceKey は保守用の子の印の欄。印から決まる例外は IsConsolidate の 1 か所から当てる。
	MaintenanceKey = "maintenance"
	// MaintenanceConsolidate は統合の子の印の値。
	MaintenanceConsolidate = "learning-consolidate"
	// ConsolidatePhase は統合の子のフェーズ番号。親の計画には載らない番号（計画の番号は 1 から）。
	ConsolidatePhase = 0

	// WorkflowKey は手で書かせない `workflow:` の欄（承認・lint・判定が error にする）。
	WorkflowKey = "workflow"
	// WorkflowDAG は先行関係の並べ方（いつもこれ）。
	WorkflowDAG = "dag"
	// PhasesKey は親が計画の使うフェーズ定義のコピーを書く欄。
	PhasesKey = "phases"

	// MaxScopeEntries は範囲の件数の上限。大量に並べた中に広い範囲を紛れ込ませる手口を防ぐ。
	MaxScopeEntries = 20

	// Outside は範囲の判定で「どの項も言及していない」。範囲の外。
	Outside rules.Decision = ""
)

// consolidateScope は統合の子が書ける場所の上限（ccnavi の中に固定した定義）。
var consolidateScope = []string{wiki.PatternsDir + "/*", wiki.InboxDir + "/*"}

// ConsolidateScope は consolidateScope の写しを返す（呼び手が書き換えても元は変わらない）。
func ConsolidateScope() []string { return slices.Clone(consolidateScope) }

// scriptFields はスクリプトだけが書く欄。承認は提案の中身を変えずに動かすので、提案に空でない値があれば
// 承認と lint が error にする。
var scriptFields = []string{
	"started_at", "completed_at", "base_sha", "merged_sha", "cancelled_at", "cancel_reason",
	// finish が子をどこに置いたかと、レビュー待ちの子を done/ へ動かした経路。手で動かした子は settled_by を持たない。
	"finished_to", "settled_by",
}

// ScriptFields は scriptFields の写しを返す（呼び手が書き換えても元は変わらない）。
func ScriptFields() []string { return slices.Clone(scriptFields) }

// wildcards は glob のワイルドカード。これより前が字義どおりの前置。
const wildcards = "*?["

// Need はレビューの要否の宣言。書かなければ Unsaid で、フェーズ定義に従う。
type Need int8

const (
	Unsaid Need = iota
	Needed
	Unneeded
)

// NeedOf は真偽を宣言にする。
func NeedOf(b bool) Need {
	if b {
		return Needed
	}
	return Unneeded
}

// Said は宣言があるか。
func (n Need) Said() bool { return n != Unsaid }

// ChosenReview はステップの終わりにレビューが要るか。ステップの指定（チケットの記載）が定義より優先する。
func ChosenReview(item, typ Need) Need {
	if item.Said() {
		return item
	}
	return typ
}

// Entry は範囲の 1 項。
type Entry struct {
	Decision rules.Decision
	Glob     string
	Regex    string
	Compiled *pyre.Regexp
}

// Matches は相対パスがこの項に当たるか。ワイルドカードの無い glob は前置で読む（`src` は `src` という名前の
// ファイルも `src/` の下も中）。大文字小文字は揃えてから比べる。範囲はユーザが宣言する意図なので、
// 機械の都合ではなく書かれたパスの意味で読む（Windows と Linux で止まる場所を食い違わせない）。
func (e *Entry) Matches(rel string) bool {
	if e.Compiled == nil {
		return false
	}
	if e.Glob != "" && !strings.ContainsAny(e.Glob, wildcards) {
		here, there := pyfmt.Lower(rel), pyfmt.Lower(e.Glob)
		return here == there || strings.HasPrefix(here, there+"/")
	}
	return e.Compiled.Match(rel)
}

// Prefix は字義どおりの前置。部分集合の検査に使う。regex の項は空。
func (e *Entry) Prefix() string {
	if e.Regex != "" {
		return ""
	}
	if i := strings.IndexAny(e.Glob, wildcards); i >= 0 {
		return e.Glob[:i]
	}
	return e.Glob
}

// AfterError は読めなかった `after` の値とその理由。
type AfterError struct {
	Value  any
	Reason string
}

// PlanItem は親の計画の 1 ステップ。After は読めた正しい番号（1 以上、自分より小さい、その計画の範囲の中）
// だけを小さい順に持つ。形の誤りは読み込みで落とさず AfterErrors に持つ（落とすと承認済みチケットが索引に
// 入らず、判定がその親を知らないまま範囲を当てなくなる）。AfterRepeated は 2 度挙げた番号。
type PlanItem struct {
	Type          string
	Review        Need
	Deferred      bool
	After         []int
	AfterErrors   []AfterError
	AfterRepeated []int
}

// Raw は書き戻す形。何も添えなければ名前だけ、添えたものがあれば {type, review, deferred, after}。
func (p *PlanItem) Raw() any {
	if !p.Review.Said() && !p.Deferred && len(p.After) == 0 {
		return p.Type
	}
	raw := pyjson.NewObject("type", p.Type)
	if p.Review.Said() {
		raw.Set("review", p.Review == Needed)
	}
	if p.Deferred {
		raw.Set("deferred", true)
	}
	if len(p.After) > 0 {
		after := make([]any, len(p.After))
		for i, n := range p.After {
			after[i] = n
		}
		raw.Set("after", after)
	}
	return raw
}

// Same は改版かどうかを見る比べ方。After は入れない（推移的な先行で比べる。直接の先行だけを書いた
// after と、推移的に全部を書いた after は同じ先行になる）。
func (p *PlanItem) Same(o *PlanItem) bool {
	return p.Type == o.Type && p.Review == o.Review && p.Deferred == o.Deferred
}

// Workflow は計画のステップの先行関係。after から都度計算する結果で、ファイルにもチケットの欄にも持たない。
// Waits は番号 → 推移的に辿った先行の全部（小さい順）、ReviewAt は延期した番号 → 引き受ける番号。
type Workflow struct {
	Order    string
	Waits    map[int][]int
	ReviewAt map[int]int
}

// Step は番号を振った計画のステップ。
type Step struct {
	Number int
	Item   *PlanItem
}

// Ticket はチケット 1 本。提案としても承認済みチケットとしても同じ形。
type Ticket struct {
	ID     string
	Parent string
	// Phase は子のフェーズ番号。子でなければ意味を持たない（ChildPhase で読む）。
	Phase        int
	Predecessors []string
	// Issue は元になった課題の番号（0 は無し）。親だけが持つ。IssueRepo は別のリポジトリの課題の `owner/repo`。
	Issue     int
	IssueRepo string
	// MR はこの親の仕事を載せるマージリクエストの番号（0 は無し）。スクリプトだけが書く欄ではない。
	MR int
	// Project は作業のプロジェクト。決めるのは提案を置いた場所で、Scan が入れる。DeclaredProject は frontmatter に
	// 書かれた `project:` で、置き場と違えば承認しない。Scan を通さずに読めば同じ値。
	Project         string
	DeclaredProject string
	// Plan は全体計画、Feedback はフィードバック計画。FeedbackPlanned が偽なら「まだ計画していない」、
	// 真で空なら「見たうえで対応なし」。
	Plan            []PlanItem
	Feedback        []PlanItem
	FeedbackPlanned bool
	// Workflow は計画の先行関係。上の段が after から計算して入れる。nil は入れていない読み方。
	Workflow *Workflow
	// PhasesRaw は親の `phases:` を読んだままの値（無い・null なら nil）。形の誤りは blocked の理由になる。
	PhasesRaw any
	// PhaseTypes は PhasesRaw を定義に読んだもの。型は tickets/config が持ち、model からは読まない。
	PhaseTypes any
	// ReviewRequired は子が human_review で言った人間レビューの要否。
	ReviewRequired Need
	ReviewReason   string
	Title          string
	Rationale      string
	Entries        []Entry
	// スクリプトが書く欄（ScriptFields）。
	StartedAt    string
	CompletedAt  string
	BaseSHA      string
	MergedSHA    string
	CancelledAt  string
	CancelReason string
	FinishedTo   string
	SettledBy    string
	// Raw は読んだままの frontmatter。
	Raw  *yaml.Map
	Body string
	// 見つけた場所。提案なら状態とツリー、承認済みチケットなら置き場から。
	State    place.State
	Tree     string
	TreeRoot string
	Path     string
	// Source は読んだファイルのバイト列（Load）。承認はこれで照合し、同じバイト列を書く。Parse だけなら nil。
	Source []byte
	// Blocked は「読めるが信じられない」理由。空でなければ判定は範囲を当てずに止める。
	Blocked string
	// maintenance は保守用の子の印の書かれたままの値（null は空文字列）。HasMaintenance は欄があるか。
	maintenance    any
	HasMaintenance bool
}

// scriptField はスクリプトの欄の名前から、その値の置き場を返す。
func (t *Ticket) scriptField(key string) *string {
	switch key {
	case "started_at":
		return &t.StartedAt
	case "completed_at":
		return &t.CompletedAt
	case "base_sha":
		return &t.BaseSHA
	case "merged_sha":
		return &t.MergedSHA
	case "cancelled_at":
		return &t.CancelledAt
	case "cancel_reason":
		return &t.CancelReason
	case "finished_to":
		return &t.FinishedTo
	case "settled_by":
		return &t.SettledBy
	}
	return nil
}

// IsChild は子か。
func (t *Ticket) IsChild() bool { return t.Parent != "" }

// ChildPhase は子のフェーズ番号と、子か。
func (t *Ticket) ChildPhase() (int, bool) { return t.Phase, t.IsChild() }

// IsConsolidate は統合の子か（子で、phase: 0 で、maintenance: learning-consolidate）。統合の子だけの例外
// （計画への所属・範囲・学び・リスク・レビュー）はすべてこれで決める。組み合わせのおかしいものは偽になり、
// 構造の誤り（MaintenanceProblem）で止まる。
func (t *Ticket) IsConsolidate() bool {
	s, ok := t.maintenance.(string)
	return t.IsChild() && t.Phase == ConsolidatePhase && ok && s == MaintenanceConsolidate
}

// MaintenanceProblem は印の構造の誤りの理由。無ければ空。印の無い phase: 0 の子はここでは言わない
// （計画に無い番号として止まる）。
func (t *Ticket) MaintenanceProblem() string {
	if !t.HasMaintenance {
		return ""
	}
	if !t.IsChild() {
		return "親に `" + MaintenanceKey + ":` の印がある。印は統合の子だけが持つ"
	}
	if s, ok := t.maintenance.(string); !ok || s != MaintenanceConsolidate {
		return "`" + MaintenanceKey + ": " + pyfmt.Str(t.maintenance) + "` は知らない値" +
			"（読めるのは `" + MaintenanceConsolidate + "` だけ）"
	}
	if t.Phase != ConsolidatePhase {
		return fmt.Sprintf("`%s: %s` の子は `phase: %d` でなければならない（今は `phase: %d`）",
			MaintenanceKey, MaintenanceConsolidate, ConsolidatePhase, t.Phase)
	}
	return ""
}

// HasPlan は全体計画を持つか。
func (t *Ticket) HasPlan() bool { return len(t.Plan) > 0 }

// InProgress は着手していて、終わってもいないし取り消されてもいない。
func (t *Ticket) InProgress() bool {
	return t.StartedAt != "" && t.CompletedAt == "" && t.CancelledAt == ""
}

// Numbered は計画のステップに番号を振る。全体計画が 1 から、フィードバック計画はその続き。
func (t *Ticket) Numbered() []Step {
	out := make([]Step, 0, len(t.Plan)+len(t.Feedback))
	for i := range t.Plan {
		out = append(out, Step{Number: len(out) + 1, Item: &t.Plan[i]})
	}
	for i := range t.Feedback {
		out = append(out, Step{Number: len(out) + 1, Item: &t.Feedback[i]})
	}
	return out
}

// ItemAt はその番号のステップ。無ければ nil。
func (t *Ticket) ItemAt(number int) *PlanItem {
	for _, s := range t.Numbered() {
		if s.Number == number {
			return s.Item
		}
	}
	return nil
}

// InPlan はその番号が全体計画の中か。
func (t *Ticket) InPlan(number int) bool { return 1 <= number && number <= len(t.Plan) }

// ReviewAt はその番号のフェーズのレビューが行われる番号。延期なら引き受ける番号。先行関係があればそれで読み、
// 無ければ後ろで延期していない最初の番号を採る。延期の先が無ければ偽（計画が壊れている）。
func (t *Ticket) ReviewAt(number int) (int, bool) {
	item := t.ItemAt(number)
	if item == nil {
		return 0, false
	}
	if !item.Deferred {
		return number, true
	}
	if t.Workflow != nil {
		at, ok := t.Workflow.ReviewAt[number]
		return at, ok
	}
	for _, s := range t.Numbered() {
		if s.Number > number && !s.Item.Deferred {
			return s.Number, true
		}
	}
	return 0, false
}

// CoveredBy はこの番号のレビューが含む、それより前の延期したフェーズの番号（小さい順）。
func (t *Ticket) CoveredBy(number int) []int {
	var found []int
	if t.Workflow != nil {
		for n, at := range t.Workflow.ReviewAt {
			if at == number {
				found = append(found, n)
			}
		}
		slices.Sort(found)
		return found
	}
	for _, s := range t.Numbered() {
		if s.Number >= number {
			break
		}
		if at, ok := t.ReviewAt(s.Number); s.Item.Deferred && ok && at == number {
			found = append(found, s.Number)
		}
	}
	return found
}

// emptyScope は範囲の項が 1 件も無いチケットの範囲の表記。
const emptyScope = "(空)"

// ScopeLine はチケットの範囲（allow と ask の項）を 1 行に並べたもの。文の中で範囲を見せるときはこれを使う
// （判定・実行後チェック・サブエージェントの終わりで、同じ範囲が同じ表記になるように）。
func (t *Ticket) ScopeLine() string {
	if area := strings.Join(append(t.Paths(rules.Allow), t.Paths(rules.Ask)...), ", "); area != "" {
		return area
	}
	return emptyScope
}

// Paths はそのタイプの項の式（glob か regex）。
func (t *Ticket) Paths(decision rules.Decision) []string {
	var out []string
	for _, e := range t.Entries {
		if e.Decision == decision {
			if e.Glob != "" {
				out = append(out, e.Glob)
			} else {
				out = append(out, e.Regex)
			}
		}
	}
	return out
}

// Decide はこのチケットがツリーのルートからの相対パスをどう扱うか。強いタイプから見て、どこにも当たらなければ
// Outside（範囲外）。書いていない場所は範囲外、が子のファイルだけ読んで範囲が分かる条件。
func (t *Ticket) Decide(rel string) rules.Decision {
	if e := t.EntryFor(rel); e != nil {
		return e.Decision
	}
	return Outside
}

// EntryFor は Decide がその判定の根拠にした項。どれにも当たらなければ nil。
func (t *Ticket) EntryFor(rel string) *Entry {
	for _, name := range rules.Sections() {
		for i := range t.Entries {
			if e := &t.Entries[i]; e.Decision == name && e.Matches(rel) {
				return e
			}
		}
	}
	return nil
}

// IssueLabel は課題の表記（`#12`・`owner/repo#12`）。MR の Closes と承認の画面が使う。無ければ空。
func (t *Ticket) IssueLabel() string {
	if t.Issue == 0 {
		return ""
	}
	return fmt.Sprintf("%s#%d", t.IssueRepo, t.Issue)
}

// combineOrder は親子の判定の厳しさ。範囲外がいちばん厳しい。
var combineOrder = map[rules.Decision]int{rules.Deny: 3, rules.Ask: 2, rules.Allow: 1, Outside: 4}

// Combine は親子の判定を、厳しい側を採る形で合わせる。
func Combine(child, parent rules.Decision) rules.Decision {
	if combineOrder[child] >= combineOrder[parent] {
		return child
	}
	return parent
}
