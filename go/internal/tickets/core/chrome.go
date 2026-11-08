package core

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/proposal/agree"
	"ccnavi/internal/tickets/review/request"
)

// ここは git を持たない呼び手（Chrome 拡張の WASM）の入口。ホストの API で読んだ中身を組んだ仮のツリーを読み、
// 書くものは値（Changes）で返す。判定は手元の入口と同じ関数を通る。

// Actor は誰がどの経路で動かしたか。Account と Via はマーカーに書く（空なら書かない）。Via が空なら今の経路で動かす。
type Actor struct {
	Account string
	Via     via.Route
}

func (a Actor) route() via.Route {
	if a.Via != "" {
		return a.Via
	}
	return via.Current()
}

// Judged は JudgeApproval の答え。Text と Digest は比べた一覧の画面とダイジェスト、Mismatch は見せたものと今の
// ものが違うときの中身（nil なら同じか、見せたものを渡していない）。Code は承認が通らない根拠の種別で、通るなら空。
type Judged struct {
	Gathered *agree.Gathered
	Text     string
	Digest   string
	Mismatch *pyjson.Object
	Code     string
	v        verdict
}

// Ready は承認を並べてよいか（対象があり、絞りが通り、見せたものと同じ）。
func (j Judged) Ready() bool {
	return len(j.Gathered.Batch) > 0 && j.Gathered.Refused == "" && j.v.mismatch == nil
}

// JudgeApproval は承認の対象を組み、見せたもの（seen。nil なら比べない）と今のものを比べる。手元の `--agree --yes` と
// 同じ judge を通る。
func JudgeApproval(conf *config.Settings, root string, only []string, seen *Shown) Judged {
	v := judge(conf, root, only, seen)
	j := Judged{Gathered: v.gathered, Text: v.text, Digest: v.digest, Code: string(v.refusal()), v: v}
	if v.mismatch != nil {
		j.Mismatch = v.mismatch.json()
	}
	return j
}

// Plan は承認の書き込みを並べる（書かない）。時刻は stamp に固定する。
func Plan(conf *config.Settings, root string, j Judged, stamp string) (*Changes, error) {
	planned, err := agree.PlanBatch(conf, root, j.v.gathered.Batch, stamp)
	if err != nil {
		return nil, err
	}
	return &Changes{Planned: planned, conf: conf, root: root}, nil
}

// Change の操作。
const (
	opCreate = "create"
	opUpdate = "update"
	OpDelete = "delete"
)

// Change は書くもの 1 つ。Path はツリーのルートからの相対（"/" 区切り）。ツリーの外なら絶対パス。消すときは Content が nil。
type Change struct {
	Op      string
	Path    string
	Content []byte
}

// BranchChanges はブランチ 1 本に書くもの。Branch はツリーの HEAD のブランチ名（読めなければツリーの名前、ツリーの外は空）。
type BranchChanges struct {
	Branch  string
	Changes []Change
}

// Changes は書くもの（承認・レビュー済み・decide の答え）。Planned.Stage が書く順、Planned.Stopped は並べる途中で
// 止まったところ。
type Changes struct {
	Planned *agree.Planned
	conf    *config.Settings
	root    string
}

// Lines はユーザに見せる行（標準出力の分）。書き込みが失敗したときの行は入らない。
func (c *Changes) Lines() []string {
	out := []string{}
	for _, item := range c.Planned.Stage.Items {
		switch x := item.(type) {
		case fsio.Line:
			if x.Stream == fsio.StreamOut {
				out = append(out, x.Text)
			}
		case fsio.Call:
			out = append(out, x.Lines...)
		}
	}
	return out
}

// PerBranch はブランチ（ツリー）ごとの増減。ブランチ名の順、中はパスの順。同じ操作で作って消したものは載せない。
func (c *Changes) PerBranch() []BranchChanges {
	byName := map[string][]Change{}
	for _, t := range c.Planned.Stage.Touched() {
		if t.Gone && !t.Existed {
			continue
		}
		name, rel := "", t.Path
		if owner, ok := tree.TreeOf(c.root, ospath.Dirname(t.Path), c.conf.Projects); ok {
			name = owner.Name
			if found, ok := tree.BranchOf(owner.Root); ok {
				name = found
			}
			rel = tree.Relative(owner, t.Path)
		}
		change := Change{Op: opCreate, Path: rel, Content: t.Content}
		switch {
		case t.Gone:
			change = Change{Op: OpDelete, Path: rel}
		case t.Existed:
			change.Op = opUpdate
		}
		byName[name] = append(byName[name], change)
	}
	names := make([]string, 0, len(byName))
	for name := range byName {
		names = append(names, name)
	}
	slices.Sort(names)
	out := make([]BranchChanges, 0, len(names))
	for _, name := range names {
		changes := byName[name]
		slices.SortStableFunc(changes, func(a, b Change) int { return strings.Compare(a.Path, b.Path) })
		out = append(out, BranchChanges{Branch: name, Changes: changes})
	}
	return out
}

// Checked は Confirm の答え。通らなければ Changes が nil で、Code に最初の理由の種別（CORE_*）が入る。Problems は
// 標準エラーに出す行で、頭に `[<コード>]` が載る。
type Checked struct {
	Problems []string
	Changes  *Changes
	Code     string
}

func (c checked) export(conf *config.Settings, root string) Checked {
	out := Checked{Problems: list(c.problems), Code: string(c.code)}
	if c.planned != nil {
		out.Changes = &Changes{Planned: c.planned, conf: conf, root: root}
	}
	return out
}

// Confirm はレビュー済みにしてよいかを見て、通れば書くもの（子を done/ へ、受け入れ台帳、マーカー）を並べる。
// 手元の `review confirm` と同じ confirm を通る。changed は依頼の後に動いたかの説明（MovedDetailOnHost の Text）。
func Confirm(conf *config.Settings, root string, actor Actor, stamp, parentID string, number int, result host.Result, changed string, manual *phase.Manual, moved request.Moved) (Checked, error) {
	c, err := confirm(conf, root, actor, stamp, parentID, number, result, changed, manual, moved)
	if err != nil {
		return Checked{}, err
	}
	return c.export(conf, root), nil
}

// Reviewable は依頼済みでまだレビュー済みでないフェーズ（ボードが出す候補）。通るかは Confirm が決める。MR と Host は
// 依頼のマーカーの値。
type Reviewable struct {
	Phase    int
	MR       int
	Host     string
	Children []string
}

// ReviewableOf は親 parentID の、レビュー済みにできる候補のフェーズ。親が無ければ空。
func ReviewableOf(conf *config.Settings, root, parentID string) []Reviewable {
	parent := openParent(conf, root, parentID)
	if parent == nil {
		return nil
	}
	var out []Reviewable
	for _, p := range phase.Phases(conf, root, parent.ID, nil) {
		mark := p.Marks[marks.Requested]
		if _, reviewed := p.Marks[marks.Reviewed]; mark == nil || reviewed {
			continue
		}
		mr := 0
		if v := mark.Value("mr"); pyfmt.Truthy(v) {
			mr, _ = pyfmt.IntOf(v)
		}
		hostName := ""
		if v := mark.Value("host"); pyfmt.Truthy(v) {
			hostName = pyfmt.Str(v)
		}
		children := make([]string, len(p.Tickets))
		for i, t := range p.Tickets {
			children[i] = t.ID
		}
		out = append(out, Reviewable{Phase: p.Number, MR: mr, Host: hostName, Children: children})
	}
	return out
}

// requestedMark は依頼のマーカー。親・フェーズ・依頼の記録のどれかが無ければ nil。
func requestedMark(conf *config.Settings, root, parentID string, number int) *pyjson.Object {
	parent := openParent(conf, root, parentID)
	if parent == nil {
		return nil
	}
	p := request.PhaseOf(conf, root, parent, number)
	if p == nil {
		return nil
	}
	return p.Marks[marks.Requested]
}

// RequestedHead は依頼のマーカーに記録された親の先頭。依頼の記録（か親・フェーズ）が無ければ ok が偽。Chrome は
// 依頼の後に親のブランチが動いたかを、この先頭と比べて compare API で確かめる。
func RequestedHead(conf *config.Settings, root, parentID string, number int) (string, bool) {
	mark := requestedMark(conf, root, parentID, number)
	if mark == nil {
		return "", false
	}
	head := ""
	if v := mark.Value("head"); pyfmt.Truthy(v) {
		head = pyfmt.Str(v)
	}
	return head, true
}

// MovedDetailOnHost は、ホストで読んだ親の先頭 head と変更の一覧（listed が偽なら読めなかった）から、依頼の後に
// ユーザが見るものが動いたかを言う。手元の confirm と同じ request.MovedDetail で決める。依頼の記録が無ければ空を
// 返し、Confirm が「依頼の記録が無い」で止める。
func MovedDetailOnHost(conf *config.Settings, root, parentID string, number int, head string, changed []string, listed bool) request.Moved {
	mark := requestedMark(conf, root, parentID, number)
	if mark == nil {
		return request.Moved{}
	}
	return request.MovedDetail(conf, mark, head, changed, listed, false)
}

// ManualFor は手で動かした子の判定（移動の時刻は呼び手がホストの履歴から読んで渡す）。親かフェーズが無ければ nil。
// movedAt が nil のうちは NeedTime が真で、Paths の時刻を読んでから渡し直す。
func ManualFor(conf *config.Settings, root, parentID string, number int, movedAt *float64) *phase.Manual {
	parent := openParent(conf, root, parentID)
	if parent == nil {
		return nil
	}
	p := request.PhaseOf(conf, root, parent, number)
	if p == nil {
		return nil
	}
	m := phase.AssessManual(conf, root, parent, p, nil, phase.ManualOptions{External: true, MovedAt: movedAt})
	return &m
}
