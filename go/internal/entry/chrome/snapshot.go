package chrome

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// branch はホストから読んだブランチ 1 本の置き場の中身。files は読んだ順（親子のチケットの見分けは最初に見つけた
// ものを採るので、順が答えに効く）。binary と links は本文を読まなかったパス。
type branch struct {
	head   string
	paths  []string
	files  map[string]string
	binary []any
	links  []any
}

func (b *branch) text(path string) (string, bool) {
	if b == nil {
		return "", false
	}
	text, ok := b.files[path]
	return text, ok
}

// snapshot は要求の snapshot。branches は統合先を含む。プロジェクトのリポジトリなら project と workspace
// （ワークスペースの統合先の中身）が付く。
type snapshot struct {
	integration string
	branches    map[string]*branch
	order       []string
	absent      []string
	project     string
	workspace   *branch
	wsName      string
	hints       any
	parsed      map[string]*ticket.Ticket
}

// readBranch は files・binary・links を読む。files が無い・本文が文字列でなければ断る。
func readBranch(name string, v any) (*branch, error) {
	obj, _ := v.(*pyjson.Object)
	var files *pyjson.Object
	if obj != nil {
		files, _ = obj.Value("files").(*pyjson.Object)
	}
	if files == nil {
		return nil, refusedf("ブランチ %s の files がありません", name)
	}
	b := &branch{files: map[string]string{}}
	for _, path := range files.Keys() {
		if err := checkRel(path); err != nil {
			return nil, err
		}
		text, ok := files.Value(path).(string)
		if !ok {
			return nil, refusedf("%s:%s の本文が文字列ではありません", name, path)
		}
		b.paths = append(b.paths, path)
		b.files[path] = text
	}
	if v := obj.Value("head"); pyfmt.Truthy(v) {
		b.head = pyfmt.Str(v)
	}
	b.binary = listOf(obj.Value("binary"))
	b.links = listOf(obj.Value("links"))
	return b, nil
}

func listOf(v any) []any {
	if !pyfmt.Truthy(v) {
		return nil
	}
	list, _ := v.([]any)
	return list
}

// readSnapshot は要求の snapshot を読む。統合先の中身が無いもの、パスが仮のツリーの外へ出るもの、プロジェクト名が
// 不正なものは断る。
func readSnapshot(req *pyjson.Object) (*snapshot, error) {
	raw, ok := req.Value("snapshot").(*pyjson.Object)
	if !ok {
		return nil, refused("snapshot がありません")
	}
	integ, ok1 := raw.Value("integration").(*pyjson.Object)
	branches, ok2 := raw.Value("branches").(*pyjson.Object)
	if !ok1 || !ok2 {
		return nil, refused("snapshot の形式が正しくありません（integration と branches が必要です）")
	}
	name, ok := integ.Value("name").(string)
	if _, in := branches.Get(name); !ok || !in {
		return nil, refused("統合先のブランチの内容が snapshot に含まれていません")
	}
	s := &snapshot{integration: name, branches: map[string]*branch{}, parsed: map[string]*ticket.Ticket{}, hints: raw.Value("hints")}
	for _, bname := range branches.Keys() {
		b, err := readBranch(bname, branches.Value(bname))
		if err != nil {
			return nil, err
		}
		s.branches[bname] = b
		s.order = append(s.order, bname)
	}
	for _, v := range listOf(raw.Value("absent")) {
		if text, ok := v.(string); ok {
			s.absent = append(s.absent, text)
		}
	}
	if project := raw.Value("project"); pyfmt.Truthy(project) {
		text, ok := project.(string)
		if !ok || !ids.IsValidName(text) {
			return nil, refusedf("プロジェクト名が不正です：%s", pyfmt.Repr(project))
		}
		if config.IsReservedLayerName(text) {
			return nil, refusedf("プロジェクト名 %s はレイヤー名として予約されています（common・self）", text)
		}
		ws, _ := raw.Value("workspace").(*pyjson.Object)
		var wsInteg *pyjson.Object
		if ws != nil {
			wsInteg, _ = ws.Value("integration").(*pyjson.Object)
		}
		if wsInteg == nil {
			return nil, refused("プロジェクトのリポジトリには、ワークスペースの統合先（workspace）の指定が必要です")
		}
		wsName, ok := wsInteg.Value("name").(string)
		if !ok {
			return nil, refused("ワークスペースの統合先の名前がありません")
		}
		b, err := readBranch("(ワークスペース)", ws)
		if err != nil {
			return nil, err
		}
		s.project, s.workspace, s.wsName = text, b, wsName
	}
	return s, nil
}

// checkRel はホストから来た相対パスを、仮のツリーの外へ出ない形に限る（根から始まるもの・`\`・空や `.`・`..` の段を断る）。
func checkRel(path string) error {
	bad := path == "" || strings.Contains(path, `\`) || rooted(path)
	for _, part := range strings.Split(path, "/") {
		bad = bad || part == "" || part == "." || part == ".."
	}
	if bad {
		return refusedf("不正なパスです：%s", pyfmt.Repr(path))
	}
	return nil
}

// rooted は絶対パス・ホーム・ドライブで始まるか。
func rooted(path string) bool {
	c := path[0]
	if c == '/' || c == '\\' || c == '~' {
		return true
	}
	letter := c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z'
	return letter && len(path) > 1 && path[1] == ':'
}

func (s *snapshot) branch(name string) *branch { return s.branches[name] }

func (s *snapshot) has(name string) bool {
	_, ok := s.branches[name]
	return ok
}

func (s *snapshot) isAbsent(name string) bool { return slices.Contains(s.absent, name) }

// workspaceFiles はワークスペースの統合先の中身（共通レイヤー・設定・互換のマーカー）。ワークスペース自身なら統合先。
func (s *snapshot) workspaceFiles() *branch {
	if s.project != "" {
		return s.workspace
	}
	return s.branch(s.integration)
}

// found はブランチの置き場で見つけたチケット 1 つと、その状態。
type found struct {
	state place.State
	t     *ticket.Ticket
}

// parse はチケットの本文を読む（同じ本文は 1 度だけ読む）。
func (s *snapshot) parse(text string) *ticket.Ticket {
	if t, ok := s.parsed[text]; ok {
		return t
	}
	t, _ := ticket.Parse(text)
	s.parsed[text] = t
	return t
}

// ticketsIn はブランチの置き場にあるチケット（提案と承認済み）。states が nil なら全部の状態。読めないものと、
// ファイル名と識別子が合わないものは飛ばす。
func (s *snapshot) ticketsIn(b *branch, states []place.State) []found {
	if b == nil {
		return nil
	}
	type dir struct {
		base  string
		state place.State
	}
	var dirs []dir
	for _, st := range place.States() {
		dirs = append(dirs, dir{config.DefaultTickets, st})
	}
	dirs = append(dirs, dir{config.DefaultApproved, place.Doing}, dir{config.DefaultApproved, place.Done})
	var out []found
	for _, d := range dirs {
		if states != nil && !slices.Contains(states, d.state) {
			continue
		}
		prefix := d.base + "/" + string(d.state) + "/"
		for _, path := range b.paths {
			rest, ok := strings.CutPrefix(path, prefix)
			if !ok || !strings.HasSuffix(rest, config.MdExt) || strings.Contains(rest, "/") {
				continue
			}
			if t := s.parse(b.files[path]); t != nil && t.ID == strings.TrimSuffix(rest, config.MdExt) {
				out = append(out, found{d.state, t})
			}
		}
	}
	return out
}

// openStates は開いた親子のチケットを数える状態（提案の todo/・review/ と承認済みの doing/）。
var openStates = append(slices.Clone(place.States()), place.Doing)

// familyOf は識別子が属する親子のチケットの親（子の形なら親、そうでなければ自分）。
func familyOf(ident string) string {
	if c, ok := ids.ParseChild(ident); ok {
		return c.Parent
	}
	return ident
}

// claimers はブランチ name が親のブランチである親チケット。見るのはそのブランチの開いたもので、識別子が name を
// 変換したもの（ids.BranchToID）と同じ親。
func (s *snapshot) claimers(name string) []found {
	var out []found
	for _, f := range s.ticketsIn(s.branch(name), openStates) {
		if !f.t.IsChild() && f.t.ID == ids.BranchToID(name) {
			out = append(out, f)
		}
	}
	return out
}

func idents(fs []found) []string {
	var out []string
	for _, f := range fs {
		if !slices.Contains(out, f.t.ID) {
			out = append(out, f.t.ID)
		}
	}
	slices.Sort(out)
	return out
}

// familyIdent は親のブランチ name の親子のチケットの識別子。その識別子の親チケットがブランチの上に無い・2 つ以上
// あれば断る。
func (s *snapshot) familyIdent(name string) (string, error) {
	found := idents(s.claimers(name))
	switch len(found) {
	case 1:
		return found[0], nil
	case 0:
		return "", refusedf("ブランチ %s の上に、識別子が %s（ブランチ名の `/` を `-` に置き換えたもの）の親チケットがありません",
			name, ids.BranchToID(name))
	}
	return "", refusedf("ブランチ %s の上に、識別子が同じ親チケットが 2 つ以上あります（%s）。親子チケットを特定できません",
		name, strings.Join(found, ", "))
}

// treeIdent は仮のツリーに組むブランチ name の識別子（閉包の親子のチケットを含む）。親チケットが無い（閉包の先行の
// 親子のチケットで、親がそのブランチの置き場に無い）ときも、変換した識別子が識別子の形ならそれ。
func (s *snapshot) treeIdent(name string) (string, error) {
	found := idents(s.claimers(name))
	if len(found) == 1 {
		return found[0], nil
	}
	if ident := ids.BranchToID(name); len(found) == 0 && ids.IsValidID(ident) {
		return ident, nil
	}
	return s.familyIdent(name)
}

// identBranches は識別子 → その識別子の親チケットを持つブランチ（統合先以外、名前順）。
func (s *snapshot) identBranches() map[string][]string {
	out := map[string][]string{}
	for _, name := range s.order {
		if name == s.integration {
			continue
		}
		for _, f := range s.claimers(name) {
			if !slices.Contains(out[f.t.ID], name) {
				out[f.t.ID] = append(out[f.t.ID], name)
			}
		}
	}
	for k := range out {
		slices.Sort(out[k])
	}
	return out
}

// rivalProblem は親子のチケット ident の親チケットを持つブランチが name のほかにもあれば、その理由。
func (s *snapshot) rivalProblem(name, ident string) string {
	var others []string
	for _, b := range s.identBranches()[ident] {
		if b != name {
			others = append(others, b)
		}
	}
	if len(others) == 0 {
		return ""
	}
	return "親子のチケット " + ident + " になるブランチが 1 本ではありません（" + strings.Join(append([]string{name}, others...), ", ") +
		"。どれも変換すると識別子が同じで、その上に同じ識別子の親チケットがある）。どれを正とするか決まらないので、この親子のチケットは判定しません"
}

// closed は統合先の done/ にある識別子（取り消し済みを含む）。
func (s *snapshot) closed() []string {
	var out []string
	for _, f := range s.ticketsIn(s.branch(s.integration), []place.State{place.Done}) {
		out = append(out, f.t.ID)
	}
	return out
}

// hintsOf は要求か snapshot の hints（識別子 → 親のブランチ名）。読むブランチの見当にだけ使う。
func hintsOf(req *pyjson.Object, s *snapshot) (map[string]string, error) {
	raw, given := req.Get("hints")
	if !given || raw == nil {
		raw = s.hints
	}
	if raw == nil {
		return map[string]string{}, nil
	}
	obj, ok := raw.(*pyjson.Object)
	if !ok {
		return nil, refused("hints は識別子からブランチ名への対応")
	}
	out := map[string]string{}
	for _, k := range obj.Keys() {
		v, ok := obj.Value(k).(string)
		if !ok {
			return nil, refused("hints は識別子からブランチ名への対応")
		}
		out[k] = v
	}
	return out, nil
}
