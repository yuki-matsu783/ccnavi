package post

import (
	"fmt"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/builtin"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/fields"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase/scope"
)

const (
	// codeViolation はルールが守ると宣言した場所の変更。
	codeViolation = "POST_VIOLATION"
	// CodeOutOfScope は承認されたチケットの範囲の外の変更（実行前に止める reasons.CodeTicketScope とは別の値）。
	// 実行前チェックは Write・Edit の引数しか見ないので、シェル・ビルド・スクリプトが書いたものはここでしか見つからない。
	CodeOutOfScope = "POST_TICKET_SCOPE"
)

// codeOrder は記録の code に載せる強さの順。ルールの宣言はチケットの範囲より強い（ルールの deny はチケットが緩めない）。
var codeOrder = []string{codeViolation, CodeOutOfScope}

// recordCode は見つけた件のうち、記録の code に載せる 1 つ。件が無ければ空。
func recordCode(found []*finding) string {
	for _, c := range codeOrder {
		if slices.ContainsFunc(found, func(f *finding) bool { return f.code == c }) {
			return c
		}
	}
	return ""
}

// codesNote は件の code が混ざった回の内訳（`codes POST_VIOLATION 2, POST_TICKET_SCOPE 1`）。混ざらなければ空。
func codesNote(found []*finding) string {
	var shown []string
	for _, c := range codeOrder {
		n := 0
		for _, f := range found {
			if f.code == c {
				n++
			}
		}
		if n > 0 {
			shown = append(shown, fmt.Sprintf("%s %d", c, n))
		}
	}
	if len(shown) < 2 {
		return ""
	}
	return "codes " + strings.Join(shown, ", ")
}

// Places はチケットの置き場（ツリーのルートからの相対。提案と承認済み）。
type Places struct{ Tickets, Approved string }

func (p Places) holds(rel string) bool { return place.IsTicketPlace(rel, p.Tickets, p.Approved) }

// ScopeGuard は、承認された範囲を実行後の側から当てるための持ち物。実行前チェックと同じ範囲・同じ当て方を使う
// （別に書くと、実行前に通った書き込みが実行後に報告されてしまうため）。Copies は承認済みチケットを識別子で引く表、
// Projects はプロジェクトの置き場。
type ScopeGuard struct {
	Root     string
	Copies   map[string]*ticket.Ticket
	Projects string
	Places   Places
}

// finding は full が範囲の外なら、報告する文の出所（組み込みの名前のルール）とその承認済みチケットの場所を返す。
// チケットと下書きの置き場は外でも報告しない（次のチケットを提案できなくすると、承認した範囲から出られなくなる）。
func (g *ScopeGuard) finding(full string) (*rules.Rule, bool) {
	t, ok := tree.TreeOf(g.Root, full, g.Projects)
	if !ok || t.IsMain() {
		return nil, false
	}
	tk, ok := tree.Lookup(g.Copies, t.Name)
	if !ok {
		return nil, false
	}
	rel := tree.Relative(t, full)
	if g.Places.holds(rel) || place.IsHandoffPlace(rel) {
		return nil, false
	}
	var parent *ticket.Ticket
	if tk.IsChild() {
		parent = g.Copies[tk.Parent]
	}
	// 定義は親に固定したコピーから引く。phases.yml は開かない。
	found := scope.Of(tk, parent, scope.TypeFor(tk, parent), rel)
	if !found.Outside() {
		return nil, false
	}
	// 文は報告のときに文面ファイルから引く。外に出た理由ごとに識別子を分ける。
	values := map[string]string{"ticket": tk.ID, "worktree": t.Name}
	var id string
	switch {
	case found.Limit == scope.LimitBlocked:
		// チケット自体が信頼できない。範囲を見せても直しようが無いので、引っかかった検査を名指しする。
		id = "RULE_TICKET_SCOPE__BLOCKED"
		values["blocked"] = tk.Blocked
	case found.Limit == scope.LimitType && found.Type != nil:
		id = "RULE_TICKET_SCOPE__TYPE"
		values["area"] = tk.ScopeLine()
		values["type_title"] = found.Type.Title
		values["type_id"] = found.Type.ID
		values["scope"] = strings.Join(found.Type.ScopeGlobs, ", ")
	case found.Limit == scope.LimitParent && parent != nil:
		id = "RULE_TICKET_SCOPE__PARENT"
		values["area"] = tk.ScopeLine()
		values["parent"] = parent.ID
		values["parent_area"] = parent.ScopeLine()
	default:
		id = "RULE_TICKET_SCOPE__PLAIN"
		values["area"] = tk.ScopeLine()
	}
	rule := &rules.Rule{ID: builtin.TicketScopeRuleID, Root: g.Root, Text: rules.TextRef{ID: id, Values: values}}
	return rule, true
}

// finding は報告する 1 件。何が変わったかと、それを報告しているルール（報告の文は記録名で出所を名指しする）。
type finding struct {
	change git.Change
	group  []*rules.Rule
	code   string
	// どのツリーで見つけたか。記録のキーと報告に使う。
	treeName string
	treeRoot string
}

// key は記録のキー。別のリポジトリに同じ名前のワークツリーがありうるので、名前とルートの場所を入れる。
func (f *finding) key() string {
	if f.treeName == "" {
		return f.change.Key()
	}
	where := f.treeName
	if f.treeRoot != "" {
		where += "@" + pathcase.Fold(f.treeRoot)
	}
	return where + "|" + f.change.Key()
}

// scan は 1 つのツリーの変更を照合する材料。top と root が空なら、サブコマンドの書き込みを外さない。
type scan struct {
	changes []git.Change
	rules   *rules.RuleSet
	mine    []string
	scope   *ScopeGuard
	where   tree.Tree
	top     string
	places  Places
	root    string
}

// findings は、変更のうち報告すべきものを返す。ccnavi 自身が書く場所は先に除く（除かないと、記録を 1 行足すたびに
// 自分を違反として報告してしまう）。チケットの置き場のうち、サブコマンドが書いたと内容から読めるものも除く（scriptWrites）。
// ルールの deny・ask にヒットした変更は、範囲の外でもルールの側で報告する（範囲を広げても済まない場所のため）。
// allow にヒットする変更にもチケットの範囲は当てる。実行前チェックがルールとチケットの厳しい側を採るのと同じ。
func (s scan) findings() []*finding {
	var own []string
	for _, p := range s.mine {
		if p != "" {
			own = append(own, ospath.Realpath(p))
		}
	}
	script := s.scriptWrites()
	guarding := append(slices.Clone(s.rules.Deny), s.rules.Ask...)
	var found []*finding
	for _, change := range s.changes {
		if slices.ContainsFunc(own, func(p string) bool { _, under := pathcase.Under(change.Full, p); return under }) {
			continue
		}
		if script[change.Full] {
			continue
		}
		var group []*rules.Rule
		for _, rule := range guarding {
			if guardsWrites(rule, change.Full) {
				group = append(group, rule)
			}
		}
		if len(group) > 0 {
			found = append(found, &finding{change, group, codeViolation, s.where.Name, s.where.Root})
			continue
		}
		if s.scope == nil {
			continue
		}
		if rule, ok := s.scope.finding(change.Full); ok {
			found = append(found, &finding{change, []*rules.Rule{rule}, CodeOutOfScope, s.where.Name, s.where.Root})
		}
	}
	return found
}

// guardsWrites はルールがこのパスへの書き込みを禁じているか。当て方は実行前チェックと同じ Matches に任せる。
func guardsWrites(rule *rules.Rule, path string) bool {
	return slices.ContainsFunc(config.WriteTools(), func(tool string) bool { return rule.Matches(tool, path) })
}

// version はチケットの 1 つの版（無ければ present が偽）。
type version struct {
	text    string
	present bool
}

// shape はその版を正規化した内容。無い・チケットでないなら ok が偽。
func (v version) shape(drop []string) (string, bool) {
	if !v.present {
		return "", false
	}
	return fields.ScriptShape(v.text, drop)
}

func sameShape(a, b version, drop []string) bool {
	x, xok := a.shape(drop)
	y, yok := b.shape(drop)
	return xok == yok && x == y
}

type placed struct {
	change git.Change
	rel    string
}

// scriptWrites はチケットの置き場の変更のうち、ccnavi のサブコマンド（ticket start・finish・cancel、review-request・
// confirm・ready）が書いたと内容から読めるものの実パスを返す。誰が書いたかは記録せず、何が変わったかで答える。承認が
// 親のブランチに乗って届いた別の機械でも、同じ答えにするため。
//
// 外すのは、次の場合である。どちらの版も範囲を宣言していないもの（マーカー・記録）。スクリプトだけが書く欄（と review-request の
// `mr:` の書き足し）以外が 1 文字も変わっていないチケット。finish・cancel の移動（同じ内容が 1 対 1 で消えて現れた組）。
// ready の退避が消したもの。読めなかったものは外さない。読めないことは「変わっていない」ではなく、git を数秒止めるだけで
// 除外が通ってしまうため。
func (s scan) scriptWrites() map[string]bool {
	out := map[string]bool{}
	if s.top == "" || s.places.Tickets == "" && s.places.Approved == "" {
		return out
	}
	var here []placed
	for _, change := range s.changes {
		if rel := tree.Relative(s.where, change.Full); s.places.holds(rel) {
			here = append(here, placed{change, rel})
		}
	}
	if len(here) == 0 {
		return out
	}
	type left struct {
		change git.Change
		before version
		drop   []string
	}
	var gone []left
	type landed struct {
		change git.Change
		now    version
	}
	var arrived []landed
	for _, p := range here {
		text, present, readable := git.CommittedText(s.top, p.change.Path, 0)
		if !readable {
			continue
		}
		before := version{text, present}
		nowText, nowOK := fsio.ReadText(p.change.Full, fsio.Replace)
		now := version{nowText, nowOK}
		// 省いてよいのは、コミット済みの版がまだ持っていない欄だけ（サブコマンドはどれも 1 度しか書かない）。
		drop := droppable(before)
		// `mr:` が空から整数になっただけの変更も省いて比べる。最初の比べだけで、移動の組には使わない。
		same := append(slices.Clone(drop), mrFilled(before, now)...)
		_, nowShaped := now.shape(drop)
		_, beforeShaped := before.shape(drop)
		switch {
		case sameShape(before, now, same):
			out[p.change.Full] = true
		case !now.present || !nowShaped:
			if before.present && place.LeavesOpenState(p.rel, s.places.Tickets, s.places.Approved) {
				gone = append(gone, left{p.change, before, drop})
			}
		case !beforeShaped && place.LandsInFinishedState(p.rel, s.places.Tickets, s.places.Approved):
			arrived = append(arrived, landed{p.change, now})
		}
	}
	for full := range s.archivedRemovals(here) {
		out[full] = true
	}
	for _, g := range gone {
		// 行き先は消えた側の省く欄で比べる（finish が足す completed_at は消えた側がまだ持っていない）。
		want, _ := g.before.shape(g.drop)
		var to []git.Change
		for _, a := range arrived {
			if got, ok := a.now.shape(g.drop); ok && got == want {
				to = append(to, a.change)
			}
		}
		from := 0
		for _, other := range gone {
			if got, ok := other.before.shape(g.drop); ok && got == want {
				from++
			}
		}
		// 組は 1 対 1 のときだけ外す。同じ内容が 2 つ以上あると、どれがどれの行き先かを内容から決められず、
		// ただの削除や行き先に直接置いた偽物まで一緒に外れる。曖昧なら全部報告する。
		if len(to) == 1 && from == 1 {
			out[g.change.Full] = true
			out[to[0].Full] = true
		}
	}
	return out
}

// archivedRemovals は ready の退避が消したと読める削除の実パス（ready のマーカーに載り、中身が退避したコピーと同じ）。
func (s scan) archivedRemovals(here []placed) map[string]bool {
	out := map[string]bool{}
	if s.root == "" || s.places.Approved == "" {
		return out
	}
	approved := strings.Trim(s.places.Approved, "/")
	var gone []placed
	for _, p := range here {
		if !ospath.DiskLexists(p.change.Full) {
			gone = append(gone, p)
		}
	}
	if len(gone) == 0 {
		return out
	}
	ready := archive.ReadyFiles(s.root, s.where.Project, s.where.Root, archive.TreeHead(s.where.Root, "HEAD"))
	if len(ready) == 0 {
		return out
	}
	for _, p := range gone {
		inside, ok := strings.CutPrefix(p.rel, approved+"/")
		if !ok {
			continue
		}
		before, readable := git.Blob(s.top, "HEAD", p.change.Path, git.Options{})
		if !readable || before == nil {
			continue
		}
		if wap.ArchivedRemoval(inside, nil, before, ready, s.root, s.where.Project) {
			out[p.change.Full] = true
		}
	}
	return out
}

// droppable は正規化で省いてよいスクリプトの欄。コミット済みの版がまだ持っていない欄だけ。
func droppable(before version) []string {
	var held []string
	if before.present {
		held = fields.ScriptFieldsSet(before.text)
	}
	var out []string
	for _, f := range ticket.ScriptFields() {
		if !slices.Contains(held, f) {
			out = append(out, f)
		}
	}
	return out
}

// mrFilled は review-request が親の承認済みチケットに書き足す `mr:` を、比べるときだけ省く欄。判定が緩む向きの例外
// なので、コミット済みの版の `mr:` が空（欄なし・値なし）で、いまの版が `mr: 12` の 1 行だけの正の整数のときに限る。
func mrFilled(before, now version) []string {
	if !before.present || !now.present {
		return nil
	}
	if _, ok := fields.ScriptShape(before.text, nil); !ok {
		return nil
	}
	if !fields.MREmpty(before.text) {
		return nil
	}
	if _, ok := fields.PlainIntField(now.text, fields.MRKey); !ok {
		return nil
	}
	return []string{fields.MRKey}
}
