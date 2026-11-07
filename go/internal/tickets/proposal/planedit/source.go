package planedit

import (
	"cmp"
	"fmt"
	"slices"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
)

// file は読んだ提案と、そのバイト列。
type file struct {
	ticket *ticket.Ticket
	raw    []byte
}

// Source は振り直しの元。親の提案と、その親の todo/ の子の提案（読んだバイト列ごと）。
type Source struct {
	Parent   *ticket.Ticket
	raw      []byte
	children []file
	current  *ticket.Ticket
	fixed    map[int]bool
	// referrers はほかの親の子の提案（todo/）のうち、predecessors がこの親の todo/ の子を指すもの。子の識別子を
	// 付け替えると、この参照も直す。
	referrers []file
	// proposals は走査した提案の全部（付け替え先の識別子の衝突を見るため）。
	proposals []*ticket.Ticket
	conf      *config.Settings
	root      string
}

func (s *Source) files() []file {
	return slices.Concat([]file{{s.Parent, s.raw}}, s.children, s.referrers)
}

// sha は提案のバイト列をまとめたハッシュ（source_sha）。ツリーとツリーからの相対パスで区切り、機械ごとに違う
// 絶対パスは入れない（拡張とテストが同じ値を見られるように）。
func (s *Source) sha() string {
	files := s.files()
	slices.SortStableFunc(files, func(a, b file) int {
		return cmp.Or(strings.Compare(a.ticket.Tree, b.ticket.Tree),
			strings.Compare(approval.SourcePath(a.ticket), approval.SourcePath(b.ticket)))
	})
	h := digest.New(digest.SHA256)
	for _, f := range files {
		fmt.Fprintf(h, "%s\x00%s\x00%d\x00", f.ticket.Tree, approval.SourcePath(f.ticket), len(f.raw))
		h.Write(f.raw)
	}
	return digest.HexSum(h)
}

// Load は振り直しの元を読む。親の提案を 1 回だけ読み、その中身で計画とハッシュを決める。
func Load(conf *config.Settings, root string, proposal *ticket.Ticket, proposals []*ticket.Ticket, current *ticket.Ticket) (*Source, string) {
	raw, ok := fsio.ReadBytes(proposal.Path)
	if !ok {
		return nil, proposal.Path + " を読めない"
	}
	parent := reparse(raw, proposal)
	if parent == nil || parent.ID != proposal.ID || parent.IsChild() {
		return nil, proposal.Path + " を親の提案として読めない"
	}
	if !parent.HasPlan() {
		return nil, proposal.ID + " は計画（plan:）を持たない"
	}
	var children []file
	for _, t := range proposals {
		if !t.IsChild() || t.Parent != parent.ID || t.State != place.Todo || t.Path == "" {
			continue
		}
		data, ok := fsio.ReadBytes(t.Path)
		if !ok {
			return nil, "子の提案 " + t.Path + " を読めない"
		}
		children = append(children, file{cmp.Or(reparse(data, t), t), data})
	}
	own := map[string]bool{}
	for _, c := range children {
		own[c.ticket.ID] = true
	}
	var referrers []file
	for _, t := range proposals {
		if t.Parent == parent.ID || t.State != place.Todo || t.Path == "" {
			continue
		}
		if !slices.ContainsFunc(t.Predecessors, func(p string) bool { return own[p] }) {
			continue
		}
		data, ok := fsio.ReadBytes(t.Path)
		if !ok {
			return nil, "提案 " + t.Path + " を読めない"
		}
		referrers = append(referrers, file{cmp.Or(reparse(data, t), t), data})
	}
	return &Source{
		Parent: parent, raw: raw, children: children, current: current,
		fixed: candidates.FixedNumbers(conf, root, parent.ID), referrers: referrers,
		proposals: slices.Clone(proposals), conf: conf, root: root,
	}, ""
}

// reparse は読み直したバイト列のチケットに、見つけた場所（プロジェクト・状態・ツリー・パス）を移す。
func reparse(raw []byte, found *ticket.Ticket) *ticket.Ticket {
	text, ok := decode(raw)
	if !ok {
		return nil
	}
	t, _ := ticket.Parse(text)
	if t == nil {
		return nil
	}
	t.Project, t.State, t.Tree, t.TreeRoot, t.Path = found.Project, found.State, found.Tree, found.TreeRoot, found.Path
	return t
}

// decode は bytes.decode("utf-8")（改行はそのまま）。
func decode(raw []byte) (string, bool) {
	return string(raw), utf8.Valid(raw)
}

// Pick は承認待ち（todo/）の親の提案と、決まらない理由。同じ識別子の提案が 2 つ以上のツリーに残っていれば、
// 打ったツリー（cwd を含むいちばん深いツリー）の側だけを採る。ほかのツリーは別のセッションの作業かもしれない
// ので書かない。cwd がどのツリーにも当たらなければ理由を返す。
func Pick(proposals []*ticket.Ticket, name, cwd string) (*ticket.Ticket, string) {
	var found []*ticket.Ticket
	for _, t := range proposals {
		if t.ID == name && t.State == place.Todo && !t.IsChild() && t.Path != "" {
			found = append(found, t)
		}
	}
	switch len(found) {
	case 0:
		return nil, ""
	case 1:
		return found[0], ""
	}
	here := ospath.Realpath(cwd)
	var mine []*ticket.Ticket
	for _, t := range found {
		if t.TreeRoot == "" {
			continue
		}
		top := ospath.Realpath(t.TreeRoot)
		if here == top || strings.HasPrefix(here, top+ospath.Sep) {
			mine = append(mine, t)
		}
	}
	slices.SortStableFunc(mine, func(a, b *ticket.Ticket) int {
		return cmp.Compare(utf8.RuneCountInString(ospath.Realpath(b.TreeRoot)), utf8.RuneCountInString(ospath.Realpath(a.TreeRoot)))
	})
	if len(mine) > 0 {
		return mine[0], ""
	}
	places := make([]string, len(found))
	for i, t := range found {
		places[i] = cmp.Or(t.Tree, "ワークスペースルート")
	}
	return nil, name + " の提案が複数の場所にある（" + strings.Join(places, ", ") + "）。書き換えるツリーの中から実行します" +
		"（ほかのツリーの提案は書かない）"
}

// CurrentParent は改版なら、その親の承認済みチケット。新規なら nil。
func CurrentParent(conf *config.Settings, root, name string) *ticket.Ticket {
	copies, _ := approval.Scan(conf, root, false, nil)
	current := approval.ByID(copies)[name]
	if current == nil || current.IsChild() {
		return nil
	}
	return current
}

// RereadSHA は書いたあとに読み直したハッシュ（付け替えた子は新しい名前で読む）。読めなければ空。
func RereadSHA(conf *config.Settings, root, name string, current *ticket.Ticket, cwd string) string {
	again := approval.ReadProposals(conf, root, nil).Kept
	found, _ := Pick(again, name, cwd)
	if found == nil {
		return ""
	}
	if reread, _ := Load(conf, root, found, again, current); reread != nil {
		return reread.sha()
	}
	return ""
}
