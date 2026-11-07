package approval

import (
	"errors"
	"io/fs"
	"slices"
	"sort"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

// Unread は読めなかった承認済みチケット（置き場ごと読めなければ Path は置き場）と説明。
type Unread struct {
	Path    string
	Message string
}

// LoadCopy は承認済みチケットを 1 本読む。読めなければ nil と理由を返す。BOM のような目に見えない原因も、--lint が名指しできるように、理由を返す。
//
// requireRecord は、completed_at（空でない値）を求めるかを決める。
// .ccnavi/approved/ は組み込みの保護がエージェントの書き込みを止めるので、置き場だけで承認と言える。
// 一方 wip/proposals/review/ はエージェントが書ける側にあるので、finish が書く欄を 2 つめの保護として求める。
//
// 計画のある親には、計画の after から計算した先行関係と、親に固定したフェーズ定義を入れる。
// 形の誤りは、ここでは落とさない。blockingProblems が止める。
func LoadCopy(path string, requireRecord bool) (*ticket.Ticket, string) {
	t, problems := ticket.Load(path)
	if t == nil {
		if len(problems) > 0 {
			return nil, problems[0].Detail
		}
		return nil, "チケットとして読めない"
	}
	if requireRecord && t.CompletedAt == "" {
		return nil, "`completed_at` がありません。`finish` を通っていない"
	}
	if !t.IsChild() && t.HasPlan() {
		t.Workflow = phases.Compute(t)
	}
	phases.TypesOf(t)
	// Tree は「どのツリーで見つけたか」。ScanAll が入れ直す。
	t.Tree = ""
	return t, ""
}

// loadDir は置き場のチケットを名前の順に読む。読めなかったものは (パス, 説明)。
// fsio を通すので、承認の plan（書き込みを溜める段）の中では同じ承認で動かした後の置き場を読む。
func loadDir(directory string, requireRecord bool) ([]*ticket.Ticket, []Unread) {
	names, err := fsio.Listdir(directory)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, []Unread{{directory, "承認済みチケットの置き場を読めない (" + err.Error() + ")"}}
	}
	sort.Strings(names)
	var found []*ticket.Ticket
	var unread []Unread
	for _, name := range names {
		ident, ok := strings.CutSuffix(name, config.MdExt)
		if !ok {
			continue
		}
		path := ospath.Join(directory, name)
		t, reason := LoadCopy(path, requireRecord)
		if t == nil {
			unread = append(unread, Unread{path, "承認済みチケット " + path + " を読めない（" + reason + "）"})
			continue
		}
		if t.ID != ident {
			unread = append(unread, Unread{path, "承認済みチケット " + path + " の識別子 " + t.ID + " がファイル名と違う"})
			continue
		}
		t.State = stateOf(directory, t)
		found = append(found, t)
	}
	return found, unread
}

func messages(unread []Unread) []string {
	out := make([]string, 0, len(unread))
	for _, u := range unread {
		out = append(out, u.Message)
	}
	return out
}

// stateOf は置き場の名前から状態を引く。done/ は取り消しの欄で 2 つに分ける。
func stateOf(directory string, t *ticket.Ticket) place.State {
	name := place.State(ospath.Basename(directory))
	if name == place.Done && t.CancelledAt != "" {
		return place.Cancelled
	}
	return name
}

// UnreadableCopies はそのツリーの読めない承認済みチケット（作業中・閉じた・レビュー待ち）。
func UnreadableCopies(conf *config.Settings, treeRoot string) []Unread {
	approved := conf.ApprovedDir(treeRoot)
	var out []Unread
	for _, d := range []struct {
		dir     string
		require bool
	}{
		{ospath.Join(approved, string(place.Doing)), false},
		{ospath.Join(approved, string(place.Done)), false},
		{reviewDir(conf, treeRoot), true},
	} {
		_, unread := loadDir(d.dir, d.require)
		out = append(out, unread...)
	}
	return out
}

// ScanAll は全ツリーの承認済みチケット（closed なら閉じたもの）を、重複をまとめずに集める。子のワークツリーにも
// 同じものが checkout されているので、まとめないほうはボードが「どこにコピーがあるか」を見せるのに使う。
func ScanAll(conf *config.Settings, root string, closed bool) ([]*ticket.Ticket, []string) {
	state := place.Doing
	if closed {
		state = place.Done
	}
	var found []*ticket.Ticket
	var notes []string
	for _, tr := range Trees(conf, root) {
		got, unread := loadDir(ospath.Join(conf.ApprovedDir(tr.Root), string(state)), false)
		for _, t := range got {
			t.Tree, t.TreeRoot, t.Project = tr.Name, tr.Root, tr.Project
		}
		found = append(found, got...)
		notes = append(notes, messages(unread)...)
	}
	return found, notes
}

// reviewAll は全ツリーのレビュー待ち（提案の置き場の review/）を、重複をまとめずに集める。completed_at が
// 無いファイルは読まない（LoadCopy の requireRecord）。
func reviewAll(conf *config.Settings, root string) ([]*ticket.Ticket, []string) {
	var found []*ticket.Ticket
	var notes []string
	for _, tr := range Trees(conf, root) {
		got, unread := loadDir(reviewDir(conf, tr.Root), true)
		for _, t := range got {
			t.Tree, t.TreeRoot, t.State, t.Project = tr.Name, tr.Root, place.Review, tr.Project
		}
		found = append(found, got...)
		notes = append(notes, messages(unread)...)
	}
	return found, notes
}

// Raw は、全ツリーの承認済みチケット（作業中・閉じた・レビュー待ち）を、まとめる前のまま読んだもの。
// 1 回の判定の中で Scan を続けて呼ぶ呼び手が、同じ置き場を何度も読まないために持ち回る。
// 覚えておいたものではなく、その呼び出しの中で今しがた読んだもの。判定の途中でファイルが動く経路では、読み直す。
type Raw struct {
	open        []*ticket.Ticket
	openNotes   []string
	closed      []*ticket.Ticket
	closedNotes []string
	review      []*ticket.Ticket
	reviewNotes []string
}

// Everything は作業中・閉じた・レビュー待ちの全部（正とするツリーを決めるのに使う）。
func (r *Raw) Everything() []*ticket.Ticket {
	out := make([]*ticket.Ticket, 0, len(r.open)+len(r.closed)+len(r.review))
	out = append(out, r.open...)
	out = append(out, r.closed...)
	return append(out, r.review...)
}

// ReadRaw は承認済みチケットの置き場を 1 度ずつ読む。
func ReadRaw(conf *config.Settings, root string) *Raw {
	r := &Raw{}
	r.open, r.openNotes = ScanAll(conf, root, false)
	r.closed, r.closedNotes = ScanAll(conf, root, true)
	r.review, r.reviewNotes = reviewAll(conf, root)
	return r
}

// Scan は、判定と承認が読む承認済みチケットを返す（closed なら閉じたもの）。raw が nil なら読む。
// 正とするツリー（親のツリー）の側だけを残し、そこに無ければ外す。
// 子のワークツリーに checkout されているのは切った時点の版なので、残すと閉じたチケットが開いたものとして復活してしまう。
// 作業中の側には、blockingProblems と親子のチケットの立ち位置から、信頼できない理由（Blocked）を付ける。
// 置き場を手で動かす承認は --agree を通らないため。
func Scan(conf *config.Settings, root string, closed bool, raw *Raw) ([]*ticket.Ticket, []string) {
	if raw == nil {
		raw = ReadRaw(conf, root)
	}
	found, notes := raw.open, raw.openNotes
	if closed {
		found, notes = raw.closed, raw.closedNotes
	}
	kept := authoritative(found, raw.Everything())
	if !closed {
		markBlocked(conf, kept)
		markImported(conf, root, kept)
	}
	return kept, append([]string(nil), notes...)
}

// ScanReview はレビュー待ちのチケット。正とするツリーの側だけを残す（Scan と同じ規則）。raw が nil なら読む。
func ScanReview(conf *config.Settings, root string, raw *Raw) ([]*ticket.Ticket, []string) {
	if raw == nil {
		raw = ReadRaw(conf, root)
	}
	kept := authoritative(raw.review, raw.Everything())
	markImported(conf, root, kept)
	return kept, append([]string(nil), raw.reviewNotes...)
}

// Approved は作業中・閉じた・レビュー待ちの承認済みチケット（Scan と ScanReview の答え）。親子を引くだけの呼び手が、
// 3 つの置き場を読む手順を繰り返さずに、1 回読んだ結果を持ち回る。
type Approved struct {
	Open, Closed, Review []*ticket.Ticket
}

// ScanApproved は Scan（作業中・閉じた）と ScanReview を、同じ raw から続けて求める。raw が nil なら読む。
func ScanApproved(conf *config.Settings, root string, raw *Raw) *Approved {
	if raw == nil {
		raw = ReadRaw(conf, root)
	}
	open, _ := Scan(conf, root, false, raw)
	closed, _ := Scan(conf, root, true, raw)
	review, _ := ScanReview(conf, root, raw)
	return &Approved{open, closed, review}
}

// All は作業中・閉じた・レビュー待ちの順に並べたもの。
func (a *Approved) All() []*ticket.Ticket { return slices.Concat(a.Open, a.Closed, a.Review) }

// authoritative は、正とするツリーの側だけを残す。決め方は ticket.Authority の 1 つで、承認済みの識別子の提案も同じ関数で決める。
// 決まらなければ全部残す。ほかのツリーのコピーで代用せず、リポジトリをまたいだ衝突もまとめない。
// まとめると、どちらかが気づかないうちに消え、--lint の「複数のリポジトリにある」も出なくなる。
func authoritative(found, everything []*ticket.Ticket) []*ticket.Ticket {
	seen := groups(everything)
	var kept []*ticket.Ticket
	for _, g := range ticket.ByTicket(found) {
		copies := seen[g.ID]
		if len(copies) == 0 {
			copies = g.Tickets
		}
		where, ok := ticket.Authority(copies)
		for _, t := range g.Tickets {
			if !ok || t.Tree == where {
				kept = append(kept, t)
			}
		}
	}
	return kept
}

func groups(found []*ticket.Ticket) map[string][]*ticket.Ticket {
	out := map[string][]*ticket.Ticket{}
	for _, g := range ticket.ByTicket(found) {
		out[g.ID] = g.Tickets
	}
	return out
}

// Stale は正とするツリーの外に残った todo/ の提案と、正とするツリーの名前。
type Stale struct {
	Ticket *ticket.Ticket
	Where  string
}

// Proposals は ReadProposals の答え。
type Proposals struct {
	Kept     []*ticket.Ticket
	Problems []rules.Problem
	// Stale は承認済みの識別子の todo/ の提案のうち、正とするツリーの外に在るので読まなかったもの。
	Stale []Stale
}

// ReadProposals は、提案（todo/ と review/）を、承認済みチケットがどのツリーにあるかに従ってまとめる。raw が nil なら読む。
// 承認済みの識別子の提案は、承認済みチケットで正とするツリーを決め、そのツリーにあるものだけを残す。
// 承認の前に切ったワークツリーに残った todo/ を、承認待ちや改版（巻き戻し）と読まないため。
func ReadProposals(conf *config.Settings, root string, raw *Raw) Proposals {
	if raw == nil {
		raw = ReadRaw(conf, root)
	}
	everything := raw.Everything()
	found, problems := ticket.ScanAll(root, conf.Tickets, conf.Projects)
	kept := ticket.Dedupe(found, everything)
	return Proposals{Kept: kept, Problems: problems, Stale: staleProposals(found, kept, everything)}
}

// staleProposals は正とするツリーの外の todo/ の提案。正とするツリーの todo/ に同じ中身の提案が在るもの
// （親のツリーの改版が子のワークツリーに入っているだけ）と review/ の提案は入れない。
func staleProposals(found, kept, everything []*ticket.Ticket) []Stale {
	keptSet := map[*ticket.Ticket]bool{}
	for _, t := range kept {
		keptSet[t] = true
	}
	// 承認済みの識別子について、正とするツリー以外に残った todo/ の提案を、古い提案として挙げる。
	settled := groups(everything)
	var out []Stale
	for _, g := range ticket.ByTicket(found) {
		copies := settled[g.ID]
		if len(copies) == 0 {
			continue
		}
		where, ok := ticket.Authority(copies)
		if !ok {
			continue
		}
		// 正とするツリーに残した提案と同じ中身のコピーは、ただの複製なので古い提案に数えない。
		var there []*ticket.Ticket
		for _, t := range g.Tickets {
			if keptSet[t] && t.State == place.Todo {
				there = append(there, t)
			}
		}
		for _, t := range g.Tickets {
			if t.State != place.Todo || keptSet[t] || t.Tree == where {
				continue
			}
			same := false
			for _, u := range there {
				if sameTodo(t, u) {
					same = true
					break
				}
			}
			if !same {
				out = append(out, Stale{t, where})
			}
		}
	}
	return out
}

// RevisionElsewhereText は、正とするツリーの外にある、計画・コピー・範囲の違う版の案内を返す。
// ファイルの場所と、改版を書く置き場を、ワークスペースルートからのパスで名指しする（--lint と --agree が同じ文を出す）。
// 親子のチケットが決まらないなら、止まった理由と手順（FamilyProblems と同じ文）を出す。
// 決まっていれば、親のブランチへ送る手順を足す。fams が nil なら作る。
func RevisionElsewhereText(conf *config.Settings, root string, t *ticket.Ticket, where string, fams *standing.Families) string {
	rel, ok := ospath.Relpath(t.Path, root)
	if !ok {
		rel = t.Path
	}
	rel = ospath.Slashed(rel)
	at := treePath(conf, root, where, t.Project)
	todo := conf.Tickets + "/" + string(place.Todo) + "/"
	text := t.ID + " の計画・コピー・範囲の違う提案が " + rel + " に在るが、" +
		"正とするツリー（" + at + "）の外なので承認の対象にならない。" +
		"改版なら " + at + " の " + todo + " に書き、古い版なら削除してください"
	st := FamilyStanding(conf, root, t, fams)
	if st.Stop != "" {
		return text + "。親子のチケットが止まっている: " + familyStopText(root, st)
	}
	if st.Home != nil {
		propose := config.ScriptCommand(root, config.TicketWrapper) + " approval-request " + t.ID
		text += "。書いたら '" + propose + "' で親のブランチへ送ってから承認を頼んでください"
	}
	return text
}
