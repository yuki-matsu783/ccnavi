package notice

import (
	"fmt"
	"io"
	"slices"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/builtin"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/guard"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase/scope"
)

// 承認済みチケットの allow / ask の項のうち、判定のときに上位に退けられて効かないもの（REQ-SES-02）を、
// 判定と同じ順（ルールの deny → 親の範囲 → フェーズ定義の上限）で適用して名指しする。承認は超過をどこにも
// 残さないので、いまの承認済みチケットとルールから計算し直す（記録を足すと、承認のあとにルールや定義が
// 変わったときに古い事実を示す）。glob 同士の包含は決められないので、項を「字義どおりの前置 + 1 文字」の
// パスにして適用する近似で、項の一部だけが退けられる形は拾わない。regex の項は確かめられないと数えるだけ。
// 判定は変えない。

const (
	// rejectsShown は SessionStart の文に出す件数の上限。残りは件数で言い、--explain へ誘う。
	rejectsShown = 3
	// rejectsBudget は走査に使う時間の既定。索引の更新（docsearch）に SessionStart の期限の残りを渡すため短く取る。
	rejectsBudget = 500 * time.Millisecond
	// rejectsMaxEntries は 1 回で適用する項の数の上限（チケット数 × 項数 × ルール数で伸びる）。
	rejectsMaxEntries = 500

	approximateNote = "重なりは glob の前置に 1 文字追加したパスで近似している" +
		"（項の一部だけが退けられる形は拾わない）。"

	originRule   = "ルール"
	originParent = "親"
	originType   = "フェーズ定義"
)

// Reject は却下された要求 1 件。Origin は退けた側の名指し（「ルール no-secret（レイヤー common）」
// 「親 i0001」「フェーズ定義 research」のどれか）。
type Reject struct {
	Ticket   string
	Decision rules.Decision
	Request  string
	Reason   string
	Origin   string
}

// Found は走査の結果。Unchecked は regex の項の数、Truncated は期限か件数の上限で打ち切ったこと。
type Found struct {
	Rejects   []Reject
	Unchecked int
	Truncated bool
}

// probeOf は項を適用するパス。ワイルドカードがあれば前置に 1 文字足す（ticket の subset の検査と同じプローブ）。
func probeOf(e ticket.Entry) string {
	prefix := e.Prefix()
	if e.Glob != prefix {
		return prefix + "x"
	}
	return e.Glob
}

// ruleSetFor はこのツリーへの書き込みに適用するルール集合（共通レイヤーに行き先のレイヤーを足した和）。苦情は
// 判定の経路が言うので捨てる。ccnavi 自身の保護と承認済みの置き場の保護は組み込みにある（ルールファイルに無い）
// ので、実行後の保護範囲と同じ条件で足す。足さないと、それらが退けた項として出なくなる。
func ruleSetFor(conf *config.Settings, root string, target tree.Tree) (*rules.RuleSet, error) {
	record := &audit.Record{}
	rs, _ := ruleload.LoadRules(io.Discard, conf, record, root)
	if record.Fallback != builtin.Fallback {
		ruleload.AddLayers(io.Discard, rs, ruleload.LayerFor(conf, root, &target), root, record)
	}
	if conf.GuardCoreFiles != string(config.Disable) {
		selfguard.AddNamedRules(rs, conf.ProjectHome, root)
	}
	if conf.TicketsEnabled() {
		rule, err := guard.ApprovedWriteRule(conf.Approved, root)
		if err != nil {
			return nil, err
		}
		rs.Deny = append(rs.Deny, rule)
	}
	return rs, nil
}

// deniedBy はこのパスへの書き込みを止める deny。レイヤーの deny を土台より先に採る（判定の名指しと同じ）。
func deniedBy(rs *rules.RuleSet, full string) *rules.Rule {
	var hits []*rules.Rule
	for _, r := range rs.Deny {
		if slices.ContainsFunc(config.WriteTools, func(tool string) bool { return r.Matches(tool, full) }) {
			hits = append(hits, r)
		}
	}
	if len(hits) == 0 {
		return nil
	}
	if i := slices.IndexFunc(hits, func(r *rules.Rule) bool { return !r.Base }); i >= 0 {
		return hits[i]
	}
	return hits[0]
}

// targetOf はチケットのワークツリー。まだ無くても、在るはずの場所で組む（ルールは場所で決まる）。
func targetOf(conf *config.Settings, root string, t *ticket.Ticket) tree.Tree {
	path := tree.WorktreePath(root, t.ID)
	if ospath.IsDir(path) {
		if found, ok := tree.TreeOf(root, path, conf.Projects); ok && !found.IsMain() {
			return found
		}
	}
	return tree.Tree{Name: t.ID, Root: path, Project: t.Project, Kind: tree.KindWorktree}
}

// check はチケットごとに、項を退ける上位を判定と同じ順で適用する。
func check(conf *config.Settings, root string, tickets []*ticket.Ticket, index map[string]*ticket.Ticket,
	deadline time.Time) (*Found, error) {
	found := &Found{}
	loaded := map[string]*rules.RuleSet{}
	count := 0
	for _, t := range tickets {
		if t.Blocked != "" {
			// 範囲そのものが効いていない。名指しは判定と --lint が別にする。
			continue
		}
		target := targetOf(conf, root, t)
		rs, ok := loaded[target.Project]
		if !ok {
			var err error
			if rs, err = ruleSetFor(conf, root, target); err != nil {
				return nil, err
			}
			loaded[target.Project] = rs
		}
		var parent *ticket.Ticket
		if t.IsChild() {
			parent = index[t.Parent]
		}
		pt := scope.TypeFor(t, parent)
		for _, e := range t.Entries {
			if e.Decision == rules.Deny {
				continue
			}
			count++
			if count > rejectsMaxEntries || time.Now().After(deadline) {
				found.Truncated = true
				return found, nil
			}
			if e.Regex != "" {
				found.Unchecked++
				continue
			}
			if r, ok := rejectOf(t, e, rs, target, parent, pt); ok {
				found.Rejects = append(found.Rejects, r)
			}
		}
	}
	return found, nil
}

// rejectOf は 1 つの項を、ルールの deny → 親 → 定義の順に適用する。最初に退けた側を名指しする。
func rejectOf(t *ticket.Ticket, e ticket.Entry, rs *rules.RuleSet, target tree.Tree, parent *ticket.Ticket,
	pt *phases.PhaseType) (Reject, bool) {
	probe := probeOf(e)
	if rule := deniedBy(rs, ospath.Join(target.Root, strings.Split(probe, "/")...)); rule != nil {
		source := rule.Source
		if source == "" {
			source = "?"
		}
		origin := originRule + " " + rules.RecordName(rule) + "（レイヤー " + source + "）"
		return Reject{t.ID, e.Decision, e.Glob, texts.Spoken(rule), origin}, true
	}
	// 統合の子には親の範囲の上限を適用しない（判定の scope と同じ）。
	if parent != nil && !t.IsConsolidate() {
		if d := parent.Decide(probe); d != rules.Allow && d != rules.Ask {
			return Reject{t.ID, e.Decision, e.Glob, "親の範囲の外", originParent + " " + parent.ID}, true
		}
	}
	if pt != nil && !pt.Inherit && pt.Decide(probe) != rules.Allow {
		reason := "定義の範囲 " + strings.Join(pt.ScopeGlobs, ", ") + " の外"
		return Reject{t.ID, e.Decision, e.Glob, reason, originType + " " + pt.ID}, true
	}
	return Reject{}, false
}

// RejectOptions は FindRejects の絞り方。Only はそのチケット（ツリーの名前で引く）だけを見る。Budget のゼロ値は
// rejectsBudget、Deadline のゼロ値は期限なし（Budget だけで打ち切る）。Copies を渡せば置き場を読み直さない
// （nil なら読む）。
type RejectOptions struct {
	Only     string
	Budget   time.Duration
	Deadline time.Time
	Copies   []*ticket.Ticket
}

// FindRejects は開いている承認済みチケットの却下を全件返す（絞りも文面も作らない）。SessionStart（Rejects）と
// --explain が使う。Only のチケットが見つからなければ空。
func FindRejects(conf *config.Settings, root string, o RejectOptions) (*Found, error) {
	budget := o.Budget
	if budget == 0 {
		budget = rejectsBudget
	}
	limit := time.Now().Add(budget)
	if !o.Deadline.IsZero() && o.Deadline.Before(limit) {
		limit = o.Deadline
	}
	copies := o.Copies
	if copies == nil {
		copies, _ = approval.Scan(conf, root, false, nil)
	}
	index := approval.ByID(copies)
	if o.Only == "" {
		return check(conf, root, copies, index, limit)
	}
	bound, ok := tree.Lookup(index, o.Only)
	if !ok {
		return &Found{}, nil
	}
	return check(conf, root, []*ticket.Ticket{bound}, index, limit)
}

// ExplainLines は --explain の節の本文。1 件 1 行で、末尾に近似であることと確かめられない件数を添える。
func ExplainLines(found *Found) []string {
	var lines []string
	for _, r := range found.Rejects {
		lines = append(lines, fmt.Sprintf("  %s %s `%s`: %s（出所: %s）", r.Ticket, r.Decision, r.Request, r.Reason, r.Origin))
	}
	if found.Truncated {
		lines = append(lines, "  （件数か時間の上限で途中まで数えた）")
	}
	return append(lines, "  "+approximateNote+uncheckedNote(found))
}

func uncheckedNote(found *Found) string {
	if found.Unchecked == 0 {
		return ""
	}
	return "regex の項 " + strconv.Itoa(found.Unchecked) + " 件は確かめられない。"
}

// Rejects は SessionStart に足す、却下された要求の文。却下が無ければ空。cwd のツリーに結び付く承認済みチケット
// だけを見る。ワークスペースルート（チケットを持たないツリー）からは、却下のあるチケットの件数を 1 行で言う。
// deadline のゼロ値は期限なし（rejectsBudget だけで打ち切る）。
func Rejects(conf *config.Settings, root, cwd string, deadline time.Time) string {
	if cwd == "" {
		cwd = root
	}
	here, ok := tree.TreeOf(root, cwd, conf.Projects)
	if !ok || here.IsMain() {
		found, err := FindRejects(conf, root, RejectOptions{Deadline: deadline})
		if err != nil {
			return ""
		}
		return countLine(found)
	}
	copies, _ := approval.Scan(conf, root, false, nil)
	bound, ok := tree.Lookup(approval.ByID(copies), here.Name)
	if !ok {
		return ""
	}
	if copies == nil {
		copies = []*ticket.Ticket{}
	}
	found, err := FindRejects(conf, root, RejectOptions{Only: here.Name, Deadline: deadline, Copies: copies})
	if err != nil {
		return ""
	}
	return renderRejects(bound.ID, found)
}

func countLine(found *Found) string {
	names := map[string]bool{}
	for _, r := range found.Rejects {
		names[r.Ticket] = true
	}
	if len(names) == 0 {
		return ""
	}
	cut := ""
	if found.Truncated {
		cut = "（時間の上限で途中まで数えた）"
	}
	return fmt.Sprintf("[ccnavi] 承認済みチケット %d 件に、上位の設定に却下された要求がある%s。"+
		"そのワークツリーで始めたセッションに詳細を出す。%s --explain でも範囲を見られる。", len(names), cut, config.Launcher)
}

// renderRejects は却下の段落。件数と先頭の数件。常駐の文脈を短く保つため、空行を挟まない 1 段落にする。
func renderRejects(ident string, found *Found) string {
	if len(found.Rejects) == 0 {
		return ""
	}
	total := len(found.Rejects)
	more := ""
	if found.Truncated {
		more = "以上（時間の上限で途中まで数えた）"
	}
	lines := []string{fmt.Sprintf("[ccnavi] 承認済みチケット %s の要求のうち %d 件%sは、"+
		"上位の設定に却下されていて、判定のときに退けられる（書き込めない）。", ident, total, more)}
	for _, r := range found.Rejects[:min(total, rejectsShown)] {
		lines = append(lines, fmt.Sprintf("- %s `%s`: %s（出所: %s）", r.Decision, r.Request, r.Reason, r.Origin))
	}
	if total > rejectsShown {
		lines = append(lines, fmt.Sprintf("- ほか %d 件。全部は %s --explain で範囲とルールを見る。", total-rejectsShown, config.Launcher))
	} else {
		lines = append(lines, "詳しくは "+config.Launcher+" --explain で範囲とルールを見る。")
	}
	lines = append(lines, approximateNote+uncheckedNote(found))
	return strings.Join(lines, "\n")
}
