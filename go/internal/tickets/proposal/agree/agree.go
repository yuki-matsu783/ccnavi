package agree

import (
	"fmt"
	"io"
	"slices"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
	"ccnavi/internal/tickets/proposal/planedit"
)

// Version は承認の JSON の版（`--agree --preview --json` と `--agree --yes … --json`）。VS Code のボード拡張が読み、
// 知らない番号なら読まずに版の違いを言う。
const Version = 1

// Gathered はいま --agree が見せる一覧と、その周り。見せるのと承認するのを同じ答えにするため、一覧を組む関数は
// Gather 1 つにしてある（--explain --json の pending_approval も同じ Waiting を通る）。
type Gathered struct {
	Batch    []*candidates.Candidate
	Rejected []candidates.Rejected
	// Problems は読めなかった提案・承認済みチケットの文（標準エラーにも出したもの）。
	Problems       []string
	Pool           map[string]*ticket.Ticket
	NothingPending bool
	Broken         bool
	// Refused は絞り込みが通らなかった理由。空でなければ何も承認しない。
	Refused string
	// Note はターミナルに出す 1 行（「承認待ち N 件のうち、指定の M 件だけを承認の対象にする」）。
	Note string
	// Elsewhere は正とするツリーの外に在る計画の違う版の案内（承認待ちには入れず、書く場所を名指しするだけ）。
	Elsewhere []string
	// Proposals は走査した提案の全部（preview の plans が親の todo/ の子の提案をまとめたハッシュを作るのに使う）。
	Proposals []*ticket.Ticket
}

// Text は画面の本文。
func (g *Gathered) Text() string {
	if g.NothingPending {
		return "承認待ちのチケットはありません。"
	}
	return Screen(g.Batch, g.Pool)
}

// Identifiers は承認の対象の識別子（名前順）。
func (g *Gathered) Identifiers() []string {
	out := make([]string, len(g.Batch))
	for i, c := range g.Batch {
		out[i] = c.Ticket.ID
	}
	slices.Sort(out)
	return out
}

// Gather は承認の対象を組む。読めないものと外した提案の理由は標準エラーにも出す（ターミナルのユーザはそこで
// 読み、拡張は JSON で読む）。only は識別子での絞り込み（--agree <識別子>...・ボード）。絞りは対象を狭めるだけで、
// 絞らないときに外れるものを通さない。承認待ちに無い識別子があれば何も承認しない（ボードが古いときに見せた以外の
// ものを通さない）。親の改版が承認待ちなのに対象から外した子があっても何も承認しない（外すと前の計画で検証される）。
func Gather(stderr io.Writer, conf *config.Settings, root string, only []string) *Gathered {
	raw := approval.ReadRaw(conf, root)
	read := approval.ReadProposals(conf, root, raw)
	for _, p := range read.Problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", p)
	}
	approved, notes := approval.Scan(conf, root, false, raw)
	for _, n := range notes {
		fmt.Fprintf(stderr, "ccnavi: %s\n", n)
	}
	closed, _ := approval.Scan(conf, root, true, raw)
	review, _ := approval.ScanReview(conf, root, raw)
	// 正とするツリーの外に書いた改版は承認待ちに入れず、書く場所を名指しする。出すのは呼び手（同じ名指しを 2 度
	// 出さないため、ここでは標準エラーに書かない）。
	openIndex := approval.ByID(approved)
	var elsewhere []string
	for _, s := range read.Stale {
		if approval.RevisionElsewhere(s.Ticket, openIndex[s.Ticket.ID]) {
			elsewhere = append(elsewhere, approval.RevisionElsewhereText(conf, root, s.Ticket, s.Where, nil))
		}
	}
	pending, revisions := Waiting(read.Kept, approved, closed, review)
	broken := slices.ContainsFunc(read.Problems, func(p rules.Problem) bool { return p.Severity == rules.Error })
	problems := make([]string, 0, len(read.Problems)+len(notes))
	for _, p := range read.Problems {
		problems = append(problems, p.String())
	}
	problems = append(problems, notes...)
	refuse := func(lines []string) *Gathered {
		for _, line := range lines {
			fmt.Fprintf(stderr, "ccnavi: %s\n", line)
		}
		return &Gathered{Problems: problems, Pool: map[string]*ticket.Ticket{}, Broken: broken,
			Refused: strings.Join(lines, "\n"), Elsewhere: elsewhere}
	}
	note := ""
	if len(only) > 0 {
		// 識別子の検査は「承認待ちが無い」より先（終了コードがほかの承認待ちの有無で変わらないように）。
		var wanted []string
		for _, id := range only {
			if id != "" && !slices.Contains(wanted, id) {
				wanted = append(wanted, id)
			}
		}
		known := map[string]bool{}
		for _, t := range slices.Concat(pending, revisions) {
			known[t.ID] = true
		}
		var unknown []string
		for _, id := range wanted {
			if !known[id] {
				unknown = append(unknown, id)
			}
		}
		if len(wanted) == 0 || len(unknown) > 0 {
			shown := strings.Join(unknown, ", ")
			if shown == "" {
				shown = "(空の識別子)"
			}
			return refuse([]string{"承認待ちに無い: " + shown, "何も承認しない。ボードを更新して承認待ちを確かめてください"})
		}
		skipped := map[string]bool{}
		for _, t := range revisions {
			if !slices.Contains(wanted, t.ID) {
				skipped[t.ID] = true
			}
		}
		var lines []string
		for _, t := range pending {
			if slices.Contains(wanted, t.ID) && t.IsChild() && skipped[t.Parent] {
				lines = append(lines, t.ID+": 親 "+t.Parent+" の改版が承認待ちなのに承認の対象にありません。")
			}
		}
		if len(lines) > 0 {
			return refuse(append(lines, "何も承認しない。親の改版も承認の対象に入れてください"))
		}
		count := len(pending) + len(revisions)
		keep := func(t *ticket.Ticket) bool { return !slices.Contains(wanted, t.ID) }
		pending = slices.DeleteFunc(slices.Clone(pending), keep)
		revisions = slices.DeleteFunc(slices.Clone(revisions), keep)
		note = fmt.Sprintf("承認待ち %d 件のうち、指定の %d 件だけを承認の対象にする。", count, len(wanted))
	}
	if len(pending) == 0 && len(revisions) == 0 {
		return &Gathered{Problems: problems, Pool: map[string]*ticket.Ticket{}, NothingPending: true, Broken: broken,
			Note: note, Elsewhere: elsewhere}
	}
	batch, rejected, pool := candidates.Candidates(conf, root, pending, revisions, approved)
	for _, r := range rejected {
		fmt.Fprintf(stderr, "ccnavi: %s は承認の対象にしない\n", r.Ticket.ID)
		for _, p := range r.Problems {
			fmt.Fprintf(stderr, "  %s\n", p)
		}
	}
	return &Gathered{Batch: batch, Rejected: rejected, Problems: problems, Pool: pool, Broken: broken, Note: note,
		Elsewhere: elsewhere, Proposals: read.Kept}
}

// Waiting はいま --agree で承認の対象に入るもの（新規の承認待ちと、親の改版）。承認待ちは todo/ に在ってどの置き場
// （作業中・レビュー待ち・閉じた）にも同じ識別子が無いもの。閉じたものの再開はユーザが承認済みチケットを戻す。
// 改版は作業中の親の承認済みチケットがあり、todo/ の提案の計画・コピー・範囲がそれと違うもの。--agree と
// --explain --json が同じ答えを出すため、ここで 1 度だけ決める。
func Waiting(proposals, approved, closed, review []*ticket.Ticket) (pending, revisions []*ticket.Ticket) {
	known := approval.ByID(slices.Concat(approved, closed, review))
	openIndex := approval.ByID(approved)
	for _, t := range proposals {
		if t.State != place.Todo {
			continue
		}
		if known[t.ID] == nil {
			pending = append(pending, t)
		}
		if current := openIndex[t.ID]; current != nil && !t.IsChild() && t.HasPlan() && approval.PlanDiffers(t, current) {
			revisions = append(revisions, t)
		}
	}
	return pending, revisions
}

// PreviewBody は --preview --json の本体（--verify --json も同じものに答えを足して返す）。plans は承認の対象に
// 入った計画を持つ親の図の中身で、--plan-order <親> --json と同じ関数で作る（--order は渡さない）。digest は
// ProposalsDigest（承認のとき照合する）。
func PreviewBody(conf *config.Settings, root string, g *Gathered, digest string) *pyjson.Object {
	var parents []planedit.Parent
	batch := []any{}
	for _, c := range g.Batch {
		if !c.Ticket.IsChild() && c.Ticket.HasPlan() {
			parents = append(parents, planedit.Parent{Proposal: c.Ticket, Current: c.Current})
		}
		batch = append(batch, batchEntry(c))
	}
	rejected := []any{}
	for _, r := range g.Rejected {
		ps := []any{}
		for _, p := range r.Problems {
			ps = append(ps, p.String())
		}
		rejected = append(rejected, pyjson.NewObject("ticket", r.Ticket.ID, "problems", ps))
	}
	problems := []any{}
	for _, p := range g.Problems {
		problems = append(problems, p)
	}
	return pyjson.NewObject("version", Version, "root", root, "generated_at", fsio.Stamp(), "batch", batch,
		"text", g.Text(), "digest", digest, "rejected", rejected, "problems", problems,
		"plans", planedit.PreviewPlans(conf, root, parents, g.Proposals))
}

func batchEntry(c *candidates.Candidate) *pyjson.Object {
	t := c.Ticket
	var parent, phase any
	if t.Parent != "" {
		parent = t.Parent
	}
	if t.IsChild() {
		phase = t.Phase
	}
	overflow := []any{}
	for _, p := range c.Overflow {
		overflow = append(overflow, p.Detail)
	}
	return pyjson.NewObject("ticket", t.ID, "title", t.Title, "parent", parent, "phase", phase,
		"revision", c.IsRevision(), "tree", t.Tree, "path", t.Path, "overflow", overflow)
}

// VerifyReason は --verify の理由の名前（JSON の verify.reason）。
type VerifyReason string

const (
	VerifyOK       VerifyReason = "ok"
	VerifyRefused  VerifyReason = "refused"
	VerifyNothing  VerifyReason = "nothing-pending"
	VerifyRejected VerifyReason = "rejected"
)

// Verdict は --verify の答え。通るかどうかと、その理由の名前と、ターミナルに出す本文。
type Verdict struct {
	OK     bool
	Reason VerifyReason
	Text   string
}

// Held は提案は通るが承認を頼めない状態（親のワークツリーの状態。呼び手が調べて渡す）の理由の名前と本文。
type Held struct {
	Reason VerifyReason
	Text   string
}

// VerifyVerdict は --verify の答え。判定は Gather が済ませてあり、ここは言い換えるだけ。held は nil でなければ、
// 承認の対象になる分の代わりに出す本文。
func VerifyVerdict(root string, g *Gathered, ticketsRel string, held *Held) Verdict {
	const head = "承認の可否（確かめるだけ）\n"
	// 読めなかったものは承認されない枝でも必ず出す（読めないのが自分の書いた 1 本である見込みがいちばん高い枝）。
	unreadable := unreadableSection(g)
	if g.Refused != "" {
		return Verdict{false, VerifyRefused, head + "\n" + g.Refused + "\n" + unreadable}
	}
	elsewhere := elsewhereSection(g)
	if g.NothingPending {
		text := "\n承認待ちのチケットはありません。提案は " + ticketsRel + "/todo/ に置いてください"
		// 場所違いの改版を名指ししたときは「同じ名前で置いても承認待ちにならない」を言わない（改版そのものが
		// できないと読めるため）。
		if elsewhere != "" {
			text += "。\n"
		} else {
			text += "（承認済みの識別子と同じ名前で置いても承認待ちにはならない）。\n"
		}
		return Verdict{false, VerifyNothing, head + elsewhere + text + unreadable}
	}
	lines := []string{head}
	if g.Note != "" {
		lines = append(lines, "\n"+g.Note+"\n")
	}
	type row struct {
		name, mark string
		notes      []string
	}
	var rows []row
	width := 0
	// 苦情の文から識別子を省く（行の頭に同じものが出ている）。残すのは重さと中身。
	for _, c := range g.Batch {
		var notes []string
		for _, p := range c.Complaints {
			notes = append(notes, string(p.Severity)+": "+p.Detail)
		}
		for _, p := range c.Overflow {
			notes = append(notes, "承認しても書けない: "+p.Detail)
		}
		rows = append(rows, row{c.Ticket.ID, "通る", notes})
	}
	for _, r := range g.Rejected {
		var notes []string
		for _, p := range r.Problems {
			notes = append(notes, string(p.Severity)+": "+p.Detail)
		}
		rows = append(rows, row{r.Ticket.ID, "外れる", notes})
	}
	for _, r := range rows {
		width = max(width, utf8.RuneCountInString(r.name))
	}
	slices.SortStableFunc(rows, func(a, b row) int {
		if c := strings.Compare(a.name, b.name); c != 0 {
			return c
		}
		if c := strings.Compare(a.mark, b.mark); c != 0 {
			return c
		}
		return slices.Compare(a.notes, b.notes)
	})
	lines = append(lines, "\n")
	for _, r := range rows {
		lines = append(lines, "  "+r.name+strings.Repeat(" ", width-utf8.RuneCountInString(r.name))+"  "+r.mark+"\n")
		for _, n := range r.notes {
			lines = append(lines, noteLine(n))
		}
	}
	lines = append(lines, elsewhere, unreadable)
	var reason VerifyReason
	var tail string
	switch {
	case held != nil:
		reason, tail = held.Reason, "\n"+held.Text
	case len(g.Rejected) > 0:
		reason = VerifyRejected
		tail = fmt.Sprintf("\n%d 件が承認の対象にならない。\n%s\n", len(g.Rejected), texts.Say(root, "NEXT_VERIFY_REJECTED", nil, nil))
	default:
		reason = VerifyOK
		childless := slices.ContainsFunc(g.Batch, func(c *candidates.Candidate) bool {
			return slices.ContainsFunc(c.Complaints, func(p rules.Problem) bool { return strings.Contains(p.Detail, phases.ChildlessMark) })
		})
		// 子の無い親は通るが、書き忘れに気づけるよう、次の一手を子の提案に向ける。
		id := "NEXT_VERIFY_OK"
		if childless {
			id = "NEXT_VERIFY_CHILDLESS"
		}
		tail = fmt.Sprintf("\n%d 件が承認の対象に入る。\n%s\n", len(g.Batch), texts.Say(root, id, nil, nil))
	}
	lines = append(lines, tail)
	return Verdict{reason == VerifyOK, reason, strings.Join(lines, "")}
}

// elsewhereSection は正とするツリーの外に在る計画の違う版（承認待ちに入らない）を名指しする段。
func elsewhereSection(g *Gathered) string {
	if len(g.Elsewhere) == 0 {
		return ""
	}
	out := "\n承認待ちに入らない改版がある（正とするツリーの外）。\n"
	for _, line := range g.Elsewhere {
		out += noteLine(line)
	}
	return out
}

// unreadableSection は読めなかったものを名指しする段。終了コードは動かさないが、省くと「確かめは『いいえ』なのに
// 承認は通る」になり、自分が書いた 1 本かもしれないので、件数と文を出す。
func unreadableSection(g *Gathered) string {
	if len(g.Problems) == 0 {
		return ""
	}
	out := fmt.Sprintf("\n読めなかったファイルが %d 件ある"+
		"（提案か承認済みチケット。提案なら承認待ちに並ばない）。\n", len(g.Problems))
	for _, p := range g.Problems {
		out += noteLine(p)
	}
	return out + "        いま書いた提案が混じっていないか確かめてください。\n"
}

// noteLine は行の下に添える 1 件。複数行の苦情は 2 行目からも同じだけ下げる（下げないと次の段落に見える）。
// 行頭が `次: ` の行は機械が行頭の語を数えるので下げない。
func noteLine(note string) string {
	lines := ospath.Splitlines(note)
	if len(lines) == 0 {
		lines = []string{""}
	}
	out := "      - " + lines[0] + "\n"
	for _, line := range lines[1:] {
		if strings.HasPrefix(line, "次: ") {
			out += line + "\n"
		} else {
			out += "        " + line + "\n"
		}
	}
	return out
}
