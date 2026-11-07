package launch

import (
	"slices"
	"strings"

	"ccnavi/internal/hook/agentkind"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// 起動の tool_input の欄。
const (
	keyDescription  = "description"
	keyPrompt       = "prompt"
	keyCwd          = "cwd"
	keyIsolation    = "isolation"
	keySubagentType = "subagent_type"
)

// maxShownChars は止める理由に載せる cwd の表記の長さの上限（文字）。
const maxShownChars = 120

// role は担当の候補の種類。
type role string

const (
	roleChild  role = "child"
	roleParent role = "parent"
	roleDirect role = "direct"
)

// Stop は止める理由。Text は文面ファイルから組んだ文。Kind は config.Gate か config.Aux（dry-run でも止める）。
type Stop struct {
	Code string
	Rule string
	Text string
	Kind config.HoldKind
}

// Result は Decide の答え。Stop があれば止める。prompt が空でなければ、起動のプロンプトをそれに差し替える。
type Result struct {
	Stop   *Stop
	prompt string
}

// Apply は元の tool_input にプロンプトの差し替えを当てた写し。差し替えが無ければ nil。元は変えない。
func (r Result) Apply(input *pyjson.Object) *pyjson.Object {
	if r.prompt == "" {
		return nil
	}
	return input.With(keyPrompt, r.prompt)
}

// target は担当の候補。同じワークツリーの言及は 1 つにまとまる。
type target struct {
	name     string
	role     role
	worktree *tree.Tree
	ticket   *ticket.Ticket
	state    place.State
}

func (t *target) key() string {
	if t.worktree != nil {
		return pathcase.Fold(t.worktree.Root)
	}
	return pathcase.Fold(t.name)
}

// book は承認済みチケットの索引。作業中・レビュー待ち・閉じたの 3 つを 1 度ずつ読む。
type book struct {
	open, review, closed      []*ticket.Ticket
	index, reviewIx, closedIx map[string]*ticket.Ticket
}

func newBook(conf *config.Settings, root string, raw *approval.Raw) *book {
	if raw == nil {
		raw = approval.ReadRaw(conf, root)
	}
	b := &book{}
	b.open, _ = approval.Scan(conf, root, false, raw)
	b.review, _ = approval.ScanReview(conf, root, raw)
	b.closed, _ = approval.Scan(conf, root, true, raw)
	b.index, b.reviewIx, b.closedIx = approval.ByID(b.open), approval.ByID(b.review), approval.ByID(b.closed)
	return b
}

// find はこの名前のチケットと置き場の状態。無ければ nil。
func (b *book) find(name string) (*ticket.Ticket, place.State) {
	for _, s := range []struct {
		state place.State
		index map[string]*ticket.Ticket
	}{{place.Doing, b.index}, {place.Review, b.reviewIx}, {place.Done, b.closedIx}} {
		if found, ok := tree.Lookup(s.index, name); ok {
			return found, s.state
		}
	}
	return nil, ""
}

// children は言及に数える子（作業中・レビュー待ち・閉じたのどれかにある子チケット）の識別子。識別子の順。
func (b *book) children() []string {
	var ids []string
	for _, pool := range [][]*ticket.Ticket{b.open, b.review, b.closed} {
		for _, t := range pool {
			if t.IsChild() && !slices.Contains(ids, t.ID) {
				ids = append(ids, t.ID)
			}
		}
	}
	slices.Sort(ids)
	return ids
}

// working は親の作業中の子（着手済みで blocked でないもの）。識別子の順。
func (b *book) working(parent *ticket.Ticket) []*ticket.Ticket {
	var out []*ticket.Ticket
	for _, c := range b.open {
		if c.Parent == parent.ID && c.StartedAt != "" && c.Blocked == "" && c.InProgress() {
			out = append(out, c)
		}
	}
	slices.SortStableFunc(out, func(a, b *ticket.Ticket) int { return strings.Compare(a.ID, b.ID) })
	return out
}

// pool は先行を引く対応表。読んだ 3 つの一覧から組む（approval.PredecessorPool は読み直す）。
func (b *book) pool(conf *config.Settings, root string) approval.Pool {
	proposals, _ := ticket.Scan(root, conf.Tickets, conf.Projects, nil)
	pool := approval.PoolOf(b.open, b.review, b.closed, proposals)
	pool.AlignImported(conf, root)
	return pool
}

// Decide は起動ツールの呼び出しについて、止める理由か、プロンプトの差し替えを返す。起動ツールでなければ空。raw は
// 呼び手が読んだ承認済みチケット（nil なら読む）。止める理由の優先順位は次のとおり。
//
//  1. isolation の指定（値によらず、チケット制御の有無によらず）
//  2. 担当先が 2 つ以上（入れ子なら呼び手の子も 1 つに数える）
//  3. 担当先が親のワークツリー
//  4. 担当先が担当にできない子
//  5. 指定された cwd が担当先と食い違う。どれにも当たらなければ担当の案内を返す
//  6. 言及が無く、親のワークツリーから編集する種類を起こす
func Decide(conf *config.Settings, root string, in hookio.Input, raw *approval.Raw) Result {
	if !slices.Contains(config.AgentTools(), in.ToolName) {
		return Result{}
	}
	// 作業場所は ccnavi が決める。isolation のワークツリー（.claude/worktrees/agent-<id>）にはチケットの範囲が当たらない。
	if _, ok := in.ToolInput.Get(keyIsolation); ok {
		return Result{Stop: isolationStop(root)}
	}
	var b *book
	if conf.TicketsEnabled() {
		b = newBook(conf, root, raw)
	}
	var here *tree.Tree
	if in.Cwd != "" {
		if t, ok := tree.TreeOf(root, in.Cwd, conf.Projects); ok {
			here = &t
		}
	}
	trees := tree.Worktrees(root, conf.Projects)
	groups := targets(trees, mentions(trees, in.Field(keyDescription), b), b)
	if in.AgentID != "" {
		// 入れ子で呼び手の担当が子なら、それも担当先に数える。違う言及があれば 2 つ以上になる。
		if caller := callerChild(b, here); caller != nil && !hasKey(groups, caller.key()) {
			groups = append(groups, caller)
		}
	}

	if len(groups) >= 2 {
		return Result{Stop: multiTargetStop(root, groups, b != nil)}
	}
	if len(groups) == 1 {
		t := groups[0]
		if t.role == roleParent && b != nil {
			return Result{Stop: parentTreeStop(root, t, b)}
		}
		if t.role == roleChild && b != nil {
			if why := unavailable(t); why != "" {
				return Result{Stop: childUnavailableStop(root, t, why)}
			}
		}
		// JSON の null は書かれていないものとして読む（Python の dict.get）。
		if given := in.ToolInput.Value(keyCwd); given != nil && t.worktree != nil {
			s, ok := given.(string)
			same := ok && (s == "" || pathcase.Fold(ospath.Realpath(s)) == pathcase.Fold(ospath.Realpath(t.worktree.Root)))
			if !same {
				return Result{Stop: cwdConflictStop(root, pyfmt.Head(pyfmt.Str(given), maxShownChars), t)}
			}
		}
		return Result{prompt: plan(conf, root, in.Field(keyPrompt), t, b)}
	}

	if b != nil && here != nil && !here.IsMain() {
		parent, ok := tree.Lookup(b.index, here.Name)
		if ok && !parent.IsChild() {
			working := b.working(parent)
			kind, _ := in.ToolInput.Value(keySubagentType).(string)
			if len(working) > 0 && agentkind.Of([]string{here.Root, root}, kind, nil) == agentkind.Edit {
				return Result{Stop: noChildStop(root, parent, working)}
			}
		}
	}
	return Result{}
}

func hasKey(groups []*target, key string) bool {
	return slices.ContainsFunc(groups, func(t *target) bool { return t.key() == key })
}

// mentions は description に語として現れる担当の名前（承認済みの子の識別子と、ワークツリーの名前）。担当にできるかは
// 見ない。チケット制御が無効（b が nil）なら子の識別子は拾わない。
func mentions(trees []tree.Tree, description string, b *book) []string {
	if description == "" {
		return nil
	}
	var names []string
	if b != nil {
		names = b.children()
	}
	for _, t := range trees {
		names = append(names, t.Name)
	}
	var found []string
	for _, name := range names {
		if !slices.Contains(found, name) && wordIn(name, description) {
			found = append(found, name)
		}
	}
	return found
}

// idChars は識別子に使われる字（式の文字クラスの中身）。
const idChars = "A-Za-z0-9._-"

// wordIn は名前が語として現れるか（前後が識別子の字でない。大文字小文字は区別しない）。後ろの `.` は、続く字が識別子の
// 字のときだけ識別子の一部と読む（`C-1 と C-2.` の C-2 を落とすと、担当が 2 つあるのを 1 つと読む）。
func wordIn(ident, text string) bool {
	re, err := pyre.Compile(`(?<![`+idChars+`])`+pyre.Escape(ident)+`(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9_-])`, pyre.IgnoreCase)
	if err != nil {
		// 名前は Escape したので組めないことは無い。組めなければ言及に数える（担当先が増えて止まる側）。
		return true
	}
	return re.Search(text)
}

// targets は名前を、ワークツリーの実体ごとにまとめる（初めに現れた名前を残す）。
func targets(trees []tree.Tree, names []string, b *book) []*target {
	var out []*target
	for _, name := range names {
		t := &target{name: name, role: roleDirect}
		folded := pathcase.Fold(pyfmt.NFC(name))
		for i := range trees {
			if pathcase.Fold(trees[i].Name) == folded {
				t.worktree = &trees[i]
				break
			}
		}
		if b != nil {
			if t.ticket, t.state = b.find(name); t.ticket != nil {
				t.role = roleParent
				if t.ticket.IsChild() {
					t.role = roleChild
				}
			}
		}
		if !hasKey(out, t.key()) {
			out = append(out, t)
		}
	}
	return out
}

// callerChild は、呼び手（体）の cwd が子のワークツリーなら、その子を担当先として返す。
func callerChild(b *book, here *tree.Tree) *target {
	if b == nil || here == nil || here.IsMain() {
		return nil
	}
	found, state := b.find(here.Name)
	if found == nil || !found.IsChild() {
		return nil
	}
	return &target{name: found.ID, role: roleChild, worktree: here, ticket: found, state: state}
}

// unavailable は子を担当にできない理由。担当にできるなら空。
func unavailable(t *target) string {
	c := t.ticket
	switch {
	case c == nil:
		return ""
	case t.state == place.Done:
		if c.CancelledAt != "" {
			return "取り消された子です"
		}
		return "閉じた子です"
	case t.state == place.Review || c.CompletedAt != "":
		return "完了してレビュー待ちの子です"
	case c.Blocked != "":
		return "blocked の子です（" + c.Blocked + "）"
	case c.StartedAt == "":
		return "未着手の子です"
	case t.worktree == nil:
		return "ワークツリーがありません"
	}
	return ""
}
