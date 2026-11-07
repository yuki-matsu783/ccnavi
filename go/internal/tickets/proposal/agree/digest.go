package agree

import (
	"cmp"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
	"ccnavi/internal/tickets/proposal/internal/yamlval"
)

// ProposalsDigest は承認の対象の指紋（16 進の小文字）。提案の識別子の一覧と、各提案ファイルの元のバイト列全体の
// SHA-256 から作る。ボード・ターミナル・Chrome の承認は、見せたときの指紋を承認の直前に取り直して比べる。
//
// 解析した後の値では比べず、読んだバイト列そのもの（Ticket.Source）を使う（BOM・改行・本文だけの書き換えも拾う。
// 読み直すと照合した中身と承認の対象の中身が別の読みになる）。判定が読んだ中身と承認で書く中身は入れない。部分は
// 区切りの文字でつながず、件数と並べたリストのハッシュを取る（区切りが中身に出たときにつなぎ目をずらせるため）。
func ProposalsDigest(batch []*candidates.Candidate) string {
	sorted := slices.Clone(batch)
	slices.SortStableFunc(sorted, func(a, b *candidates.Candidate) int { return strings.Compare(a.Ticket.ID, b.Ticket.ID) })
	lines := []string{strconv.Itoa(len(sorted))}
	for _, c := range sorted {
		raw, ok := c.Ticket.Source, c.Ticket.Source != nil
		if !ok {
			raw, ok = fsio.ReadBytes(c.Ticket.Path)
		}
		sum := "-"
		if ok {
			sum = digest.HexBytes(digest.SHA256, raw, 0)
		}
		lines = append(lines, c.Ticket.ID+"\n"+sum)
	}
	return digest.Hex(digest.SHA256, strings.Join(lines, "\n"), 0)
}

// reviseCopy は承認済みチケットの計画・phases:・範囲を差し替えて書く（本文はそのまま。古い範囲は git の履歴だけに
// 残る。改版の時刻と先行関係は書かない）。書けなければ理由。
func reviseCopy(approvedDir string, current, revised *ticket.Ticket) string {
	current.Raw = revisedFront(current, revised)
	text, err := ticket.Render(current)
	if err != nil {
		return err.Error()
	}
	return marks.WriteTicket(approval.CopyPath(approvedDir, current.ID), text)
}

// revisedFront は改版で書く frontmatter（current は変えない）。承認画面の phases: の差分もこれから組むので、画面に
// 出した中身と書く中身が食い違わない。phases: は提案の値に差し替える（差し替えないと、改版で足したステップが
// phases: に無い定義名を使い、承認したとたんに親と子が全部止まる）。提案に無い範囲の節と phases: は外す。
func revisedFront(current, revised *ticket.Ticket) *yaml.Map {
	var drop []string
	for _, s := range rules.Sections {
		if !revised.Raw.Has(string(s)) {
			drop = append(drop, string(s))
		}
	}
	if !revised.Raw.Has(ticket.PhasesKey) {
		drop = append(drop, ticket.PhasesKey)
	}
	front := cmp.Or(current.Raw, &yaml.Map{}).Without(drop...)
	for _, s := range rules.Sections {
		if v, ok := revised.Raw.Lookup(string(s)); ok {
			front.Put(string(s), v)
		}
	}
	front.Put("plan", yamlval.Steps(revised.Plan))
	if revised.FeedbackPlanned {
		front.Put("feedback", yamlval.Steps(revised.Feedback))
	}
	if v, ok := revised.Raw.Lookup(ticket.PhasesKey); ok {
		front.Put(ticket.PhasesKey, v)
	}
	return front
}
