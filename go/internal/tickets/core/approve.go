package core

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/lifecycle/worktrees"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/agree"
	"ccnavi/internal/tickets/proposal/candidates"
)

// updatedNotice は見せたあとに承認待ちの提案が更新されたときの知らせ（ターミナル・ボード・Chrome で共通）。
const updatedNotice = "内容が更新されました。"

// mismatch は見せた一覧・ダイジェストと今のものの違い（承認の JSON の mismatch）。
type mismatch struct {
	expected, current      []string
	shownDigest, nowDigest string
}

func (m *mismatch) json() *pyjson.Object {
	return pyjson.NewObject("expected", m.expected, "current", m.current,
		"digest", pyjson.NewObject("expected", m.shownDigest, "current", m.nowDigest))
}

// verdict は judge の答え。messages は標準エラーに出す文面（呼び手が出す）。text と digest は比べた一覧
// （絞りが通らなかったときは絞らない一覧）の画面とダイジェスト。
type verdict struct {
	gathered *agree.Gathered
	text     string
	digest   string
	messages string
	mismatch *mismatch
}

// Shown は見せたもの（識別子とダイジェスト）。
type Shown struct {
	IDs    []string
	Digest string
}

// judge は承認の対象を組み、ダイジェストを出す。shown を渡せば、今のものと比べる。照合するのは、承認の対象の識別子と、各提案ファイルの元のバイト列全体だけである。承認の可否は、ここで最新の状態から計算し直す。絞り（only）が通らなかったときは、絞らない一覧を今の一覧として比べる。ボードの一覧が古いからである。
func judge(conf *config.Settings, root string, only []string, seen *Shown) verdict {
	var err bytes.Buffer
	gathered := agree.Gather(&err, conf, root, only)
	current := gathered
	if seen != nil && gathered.Refused != "" {
		current = agree.Gather(&err, conf, root, nil)
	}
	digest := agree.ProposalsDigest(current.Batch)
	v := verdict{gathered: gathered, text: current.Text(), digest: digest}
	if seen != nil {
		wanted := wantedIDs(seen.IDs)
		now := current.Identifiers()
		if !slices.Equal(wanted, now) || pyfmt.Lower(ospath.Strip(seen.Digest)) != digest {
			v.mismatch = &mismatch{wanted, list(now), seen.Digest, digest}
		}
	}
	v.messages = err.String()
	return v
}

// refusal は承認が通らない根拠の種別（通るなら空）。見せたものと違えば、識別子の違いを中身の違いより先に見る。
// 違わなくても、承認する対象が無いか、絞りが通らなければ codeApproveNothing。
func (v verdict) refusal() code {
	if m := v.mismatch; m != nil {
		if !slices.Equal(m.expected, m.current) {
			return codeApproveListChanged
		}
		return codeApproveDigestChanged
	}
	if len(v.gathered.Batch) == 0 || v.gathered.Refused != "" {
		return codeApproveNothing
	}
	return ""
}

// wantedIDs は見せた識別子（空を除き、重ねず、名前順）。
func wantedIDs(ids []string) []string {
	out := []string{}
	for _, id := range ids {
		if id = ospath.Strip(id); id != "" && !slices.Contains(out, id) {
			out = append(out, id)
		}
	}
	slices.Sort(out)
	return out
}

func list[T any](xs []T) []T {
	if xs == nil {
		return []T{}
	}
	return xs
}

// sayElsewhere は正とするツリーの外に在る計画の違う版の案内を標準エラーに出す。--verify のテキストは本文に同じ段を
// 持つので、そこでは呼ばない。
func sayElsewhere(stderr io.Writer, g *agree.Gathered) {
	for _, line := range g.Elsewhere {
		io.WriteString(stderr, linePrefix+line+"\n")
	}
}

// plan は承認の書き込みを並べる（書かない）。
func plan(conf *config.Settings, root string, v verdict) (*agree.Planned, error) {
	return agree.PlanBatch(conf, root, v.gathered.Batch, fsio.Stamp())
}

func dumps(v any) string {
	text, err := pyjson.Dumps(v, pyjson.Options{RawUnicode: true})
	if err != nil {
		// 組むのはここの型だけで、出せない値は入らない。
		return "{}"
	}
	return text
}

// Approve は未承認の提案をまとめてユーザに見せ、承認されたら承認済みチケットを置く。ユーザがターミナルで `--agree` を打って使う。承認は提案の中身を書き換えない。書き換えると、承認したユーザが承認したものの作者になってしまうからである。対象は、いま承認待ちのものすべてである。only は識別子で絞る（ボードの絞り込み）。絞りは対象を狭めるだけで、絞らないときに失敗するものを通すことはない。確認を待つ間に提案が更新されていれば、承認せず、最新を見せ直して確認を取り直す。
//
// placedOut があれば、書き込みに入ったあとに、書けた承認済みチケットのパスと、書けなかったものと書かなかったものを JSON で書き出す。ccnavi-user-agree.sh は、書けた分だけをコミットして push し、残りを名指しする。一部を外したとき、または途中で止まったとき（置いたものは残る）は config.ExitPartial を返す。
func Approve(stdin io.Reader, stdout, stderr io.Writer, conf *config.Settings, root string, only []string, placedOut string) (int, error) {
	answers := bufio.NewReader(stdin)
	v := judge(conf, root, only, nil)
	var gathered *agree.Gathered
	for {
		io.WriteString(stderr, v.messages)
		sayElsewhere(stderr, v.gathered)
		gathered = v.gathered
		switch {
		case gathered.Refused != "":
			return 1, nil
		case gathered.NothingPending:
			io.WriteString(stdout, "承認待ちのチケットはありません。\n")
			// 読めない提案があれば標準エラーに出ている。不正な提案を「何も無い」で通さない。
			if gathered.Broken {
				return 1, nil
			}
			return 0, nil
		case len(gathered.Batch) == 0:
			return 1, nil
		}
		if gathered.Note != "" {
			io.WriteString(stdout, gathered.Note+"\n\n")
		}
		io.WriteString(stdout, gathered.Text()+"\n\n")
		fmt.Fprintf(stdout, "この %d 件を承認する場合は y、やめる場合はそれ以外: ", len(gathered.Batch))
		if !fsio.ReadYes(answers) {
			io.WriteString(stderr, "ccnavi: 承認しなかった\n")
			return 1, nil
		}
		// 確認を待つ間に提案が更新されていないか、承認の直前に最新の状態から照合する（ボード・Chrome と同じ強さ）。
		latest := judge(conf, root, only, &Shown{gathered.Identifiers(), v.digest})
		if latest.mismatch == nil {
			v = latest
			gathered = latest.gathered
			break
		}
		io.WriteString(stdout, "\n"+updatedNotice+"最新の承認待ちを表示し直します。\n\n")
		v = judge(conf, root, only, nil)
	}
	planned, err := plan(conf, root, v)
	if err != nil {
		return 1, err
	}
	applied, written := writeRecorded(stdout, stderr, root, planned)
	// 承認は止めない。承認の後に戻せなくなる変更があれば、止まる前に知らせる。
	var placed []*ticket.Ticket
	for _, c := range gathered.Batch {
		if slices.Contains(applied.Placed, c.Ticket.ID) {
			placed = append(placed, c.Ticket)
		}
	}
	dirty := dirtyParents(conf, root, placed)
	sayDirty(stderr, dirty)
	if placedOut != "" {
		report, err := placedReport(conf, root, gathered, applied, written, dirty)
		if err != nil {
			return 1, err
		}
		if failed := fsio.WriteText(placedOut, dumps(report)+"\n", fsio.NativeNewline, fsio.Atomic); failed != "" {
			fmt.Fprintf(stderr, "ccnavi: 書けたパスの一覧を %s に書けなかった（%s）\n", placedOut, failed)
		}
	}
	if applied.Code == 0 && len(gathered.Rejected) > 0 {
		// 一部を外したときは、通ったぶんを置いてから失敗で終わる（成功で終わると、ターミナルを見ていない側は全部
		// 通ったと読む）。置いたので繰り返してよい。
		fmt.Fprintf(stderr, "ccnavi: %d 件は承認の対象にしなかった。"+
			"直して出し直してください（通ったぶんの承認済みチケットは置いた）\n", len(gathered.Rejected))
		return config.ExitPartial, nil
	}
	if applied.Code != 0 && len(applied.Placed) > 0 {
		return config.ExitPartial, nil
	}
	return applied.Code, nil
}

// copyPaths は承認済みチケットの置き場でのパス（ids の分）。承認の対象 batch から引く。
func copyPaths(conf *config.Settings, root string, batch []*candidates.Candidate, ids []string) ([]string, error) {
	byID := map[string]*ticket.Ticket{}
	for _, c := range batch {
		byID[c.Ticket.ID] = c.Ticket
	}
	out := []string{}
	for _, id := range ids {
		t, ok := byID[id]
		if !ok {
			continue
		}
		path, err := copyPath(conf, root, t)
		if err != nil {
			return nil, err
		}
		out = append(out, path)
	}
	return out, nil
}

func copyPath(conf *config.Settings, root string, t *ticket.Ticket) (string, error) {
	home, err := approval.HomeDir(conf, root, t.ID, t.Parent, t.Project)
	if err != nil {
		return "", err
	}
	return approval.CopyPath(home, t.ID), nil
}

// placedReport は承認の書き込みの結果（--placed-out の JSON）を表す。placed は書けた承認済みチケットのパス。written は、この承認で書いたパスと消したパス。failed は書き込みで止まった提案。unwritten は、止まったために書かなかった提案。rejected は検査で外した提案。dirty_parent は、承認した親のワークツリーに残る、置き場の外の変更である。
func placedReport(conf *config.Settings, root string, g *agree.Gathered, applied agree.Applied, written []string, dirty []dirtyParent) (*pyjson.Object, error) {
	failed := []any{}
	unwritten := []string{}
	if applied.Code != 0 {
		if applied.StoppedAt != "" {
			failed = append(failed, pyjson.NewObject("ticket", applied.StoppedAt, "reason", applied.Reason))
		}
		for _, c := range g.Batch {
			if id := c.Ticket.ID; !slices.Contains(applied.Placed, id) && id != applied.StoppedAt {
				unwritten = append(unwritten, id)
			}
		}
	}
	placed, err := copyPaths(conf, root, g.Batch, applied.Placed)
	if err != nil {
		return nil, err
	}
	return pyjson.NewObject("version", agree.Version, "placed", placed, "written", list(written),
		"failed", failed, "unwritten", unwritten, "rejected", rejectedJSON(g.Rejected),
		"dirty_parent", dirtyJSON(dirty)), nil
}

func rejectedJSON(rejected []candidates.Rejected) []any {
	out := []any{}
	for _, r := range rejected {
		problems := []string{}
		for _, p := range r.Problems {
			problems = append(problems, p.String())
		}
		out = append(out, pyjson.NewObject("ticket", r.Ticket.ID, "problems", problems))
	}
	return out
}

// Preview は --agree --preview。一覧を見せるだけで置かない（ターミナルの確認は要らない）。JSON の形は
// docs/11_requirements/json-shapes.md「承認の JSON」で、一覧が空でも 0。only は --agree と同じ絞り。
func Preview(stdout, stderr io.Writer, conf *config.Settings, root string, asJSON bool, only []string) int {
	v := judge(conf, root, only, nil)
	io.WriteString(stderr, v.messages)
	sayElsewhere(stderr, v.gathered)
	g := v.gathered
	if g.Refused != "" {
		return 1
	}
	if !asJSON {
		if g.Note != "" {
			io.WriteString(stdout, g.Note+"\n\n")
		}
		io.WriteString(stdout, g.Text()+"\n")
		return 0
	}
	io.WriteString(stdout, dumps(agree.PreviewBody(conf, root, g, v.digest))+"\n")
	return 0
}

// Verify は `--agree --preview --verify` を行う。いま `--agree` を打てば通るかを、置かずに終了コードと本文で伝える。エージェントは、承認を頼む前に自分で確かめる。答えを `--preview` に持たせないのは、ボードが 0 以外を失敗と読むからである。外れる提案が 1 件あるだけで、一覧を出せなくなってしまう。
//
// 通るのは、指定したもの（指定が無ければ承認待ちのすべて）がそのまま承認の対象に入り、承認を頼める状態（checkReadiness）にあるときである。範囲の超過は `--agree` が止めないので、失敗にはしない。行に付けて見せる。答えは「はい」（0）か「いいえ」（config.ExitAnswerNo）で、使い方の誤り（1）とは分ける。
func Verify(stdout, stderr io.Writer, conf *config.Settings, root string, asJSON bool, only []string) int {
	judged := judge(conf, root, only, nil)
	io.WriteString(stderr, judged.messages)
	if asJSON {
		// テキストの本文には VerifyVerdict が名指しの段を入れる。JSON の本体には無いので標準エラーへ出す。
		sayElsewhere(stderr, judged.gathered)
	}
	g := judged.gathered
	var held *agree.Held
	if len(g.Batch) > 0 && len(g.Rejected) == 0 && g.Refused == "" {
		ids := make([]string, len(g.Batch))
		for i, c := range g.Batch {
			ids[i] = c.Ticket.ID
		}
		if ready := checkReadiness(conf, root, ids, g); !ready.ok() {
			held = &agree.Held{Reason: ready.reason, Text: ready.text()}
			if asJSON { // JSON の本体には理由が無いので標準エラーへ出す
				io.WriteString(stderr, held.Text)
			}
		}
	}
	answer := agree.VerifyVerdict(root, g, conf.Tickets, held)
	// 新規の親のブランチ名が既にあるブランチと同じか。warn なので答えは変えない。
	var fresh []*ticket.Ticket
	for _, c := range g.Batch {
		if !c.IsRevision() && !c.Ticket.IsChild() {
			fresh = append(fresh, c.Ticket)
		}
	}
	branches := candidates.ExistingBranchWarnings(conf, root, fresh, nil)
	if asJSON {
		body := agree.PreviewBody(conf, root, g, judged.digest)
		body.Set("verify", pyjson.NewObject("ok", answer.OK, "reason", string(answer.Reason)))
		body.Set("branch_warnings", list(branches))
		io.WriteString(stdout, dumps(body)+"\n")
	} else {
		io.WriteString(stdout, answer.Text)
		if len(branches) > 0 {
			io.WriteString(stdout, "\n既にあるブランチと同じ名前の親（warn）:\n")
			for _, line := range branches {
				io.WriteString(stdout, "  - "+line+"\n")
			}
		}
	}
	if answer.OK {
		return config.ExitOK
	}
	return config.ExitAnswerNo
}

// ApproveYes は `--agree --yes <識別子,…> --digest <ダイジェスト> [<絞り>...]` を行う。ボードのオーバーレイで押した承認である。ターミナルの確認の代わりに、次の 2 つが今のものと同じであることを求める。見せた一覧（expected）と、見せた各提案ファイルのバイト列のダイジェスト（digest）。違えば、何も置かずに mismatch を返す。見ていないものを承認する経路を作らないためである。ダイジェストが無ければ承認しない。
//
// 見せた識別子と絞り（only。preview に渡したもの）は分けて受ける。絞りで狭めた対象と見せた識別子を比べると、いつも一致してしまうからである。途中で止まれば、置いたものを partial で返す。通れば、子のワークツリーの計画と承認後の案内を添える。
func ApproveYes(stdout, stderr io.Writer, conf *config.Settings, root string, expected []string, asJSON bool, only []string, digest string) (int, error) {
	var narrowed []string
	for _, s := range only {
		if s = ospath.Strip(s); s != "" {
			narrowed = append(narrowed, s)
		}
	}
	if ospath.Strip(digest) == "" {
		io.WriteString(stderr, tagged(codeApproveNoDigest,
			"ccnavi: --yes には --digest（見せた提案の識別子の一覧と提案ファイルの"+
				"バイト列のダイジェスト。preview の digest）が必要です")+"\n")
		return 1, nil
	}
	v := judge(conf, root, narrowed, &Shown{expected, digest})
	io.WriteString(stderr, v.messages)
	sayElsewhere(stderr, v.gathered)
	g := v.gathered
	if m := v.mismatch; m != nil {
		if asJSON {
			io.WriteString(stdout, dumps(pyjson.NewObject("version", agree.Version, "mismatch", m.json()))+"\n")
		}
		if !slices.Equal(m.expected, m.current) {
			io.WriteString(stderr, tagged(codeApproveListChanged, fmt.Sprintf(
				"ccnavi: %s見せた一覧と今の一覧が違う（見せた: %s / 今: %s）。何も承認していない。最新を見直してから承認してください",
				updatedNotice, orNone(m.expected), orNone(m.current)))+"\n")
		} else {
			io.WriteString(stderr, tagged(codeApproveDigestChanged, "ccnavi: "+updatedNotice+
				"識別子は同じだが、提案ファイルの中身が見せたときと違う。何も承認していない。最新を見直してから承認してください")+"\n")
		}
		return 1, nil
	}
	if len(g.Batch) == 0 {
		io.WriteString(stderr, tagged(codeApproveNothing, "ccnavi: 承認するものがありません。")+"\n")
		return 1, nil
	}
	planned, err := plan(conf, root, v)
	if err != nil {
		return 1, err
	}
	var lines bytes.Buffer
	applied, written := writeRecorded(&lines, stderr, root, planned)
	if applied.Code != 0 {
		// 途中で止まった。置いたものは残るので、どこまで置いたかを返す（言わずに失敗を返すと「何も起きていない」と
		// 読まれる）。
		placed := "無し"
		if len(applied.Placed) > 0 {
			placed = strings.Join(applied.Placed, ", ")
		}
		io.WriteString(stderr, tagged(codeApproveStopped, "ccnavi: 承認を途中で止めた。置いたものはそのまま残る（"+placed+"）")+"\n")
		if !asJSON {
			io.WriteString(stdout, lines.String())
			return applied.Code, nil
		}
		paths, err := copyPaths(conf, root, g.Batch, applied.Placed)
		if err != nil {
			return 1, err
		}
		// lines は止まるまでに出た行（マーカーを消した、改版した）。拡張はこの JSON しか見ない。
		partial := pyjson.NewObject("placed", list(applied.Placed), "paths", paths, "written", written,
			"ticket", applied.StoppedAt, "reason", applied.Reason, "lines", shownLines(lines.String()))
		io.WriteString(stdout, dumps(pyjson.NewObject("version", agree.Version, "partial", partial))+"\n")
		return applied.Code, nil
	}
	tickets := make([]*ticket.Ticket, len(g.Batch))
	revisions := map[string]bool{}
	for i, c := range g.Batch {
		tickets[i] = c.Ticket
		if c.IsRevision() {
			revisions[c.Ticket.ID] = true
		}
	}
	// 子のワークツリーの計画。拡張が create の子を承認の後に作る（作れなければ案内に名指しで出る）。
	worktreePlans := worktrees.ChildWorktrees(root, tickets)
	plansJSON := []any{}
	for _, p := range worktreePlans {
		plansJSON = append(plansJSON, p.JSON())
	}
	// 承認は止めない。親のワークツリーに残る提案以外の変更は、エージェントに渡す文に載せる。
	dirty := dirtyParents(conf, root, tickets)
	notices := make([]string, len(dirty))
	for i, d := range dirty {
		notices[i] = d.text
	}
	prompt := agree.ApprovedText(tickets, revisions, root, worktreePlans, notices)
	if !asJSON {
		sayDirty(stderr, dirty)
		io.WriteString(stdout, lines.String())
		return 0, nil
	}
	approved := make([]string, len(tickets))
	copies := make([]string, len(tickets))
	for i, t := range tickets {
		approved[i] = t.ID
		if copies[i], err = copyPath(conf, root, t); err != nil {
			return 1, err
		}
	}
	body := pyjson.NewObject("version", agree.Version, "approved", approved, "copies", copies,
		"lines", shownLines(lines.String()), "written", written, "worktrees", plansJSON,
		"dirty_parent", dirtyJSON(dirty), "prompt", prompt)
	io.WriteString(stdout, dumps(body)+"\n")
	return 0, nil
}

// shownLines は書いている間に出た行のうち、空でないもの。
func shownLines(text string) []string {
	out := []string{}
	for _, line := range ospath.Splitlines(text) {
		if ospath.Strip(line) != "" {
			out = append(out, line)
		}
	}
	return out
}

func orNone(ids []string) string {
	if len(ids) == 0 {
		return "(無し)"
	}
	return strings.Join(ids, ", ")
}
