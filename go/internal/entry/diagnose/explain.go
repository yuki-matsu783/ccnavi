package diagnose

import (
	"fmt"
	"io"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"ccnavi/internal/hook/judge"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/builtin"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/config/risk"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/notice"
	"ccnavi/internal/tickets/phase"
)

// layerLabels はレイヤーの見出し。共通と自身だけ日本語で出す。プロジェクトは名前そのもの（id の前置きと同じ表記）。
var layerLabels = map[string]string{
	config.LayerCommon: "共通レイヤー",
	config.LayerSelf:   "ワークスペースのレイヤー",
}

func layerLabel(name string) string {
	if label, ok := layerLabels[name]; ok {
		return label
	}
	return name
}

// layerCol はレイヤー列の表示幅（全角は 2 桁）。「ワークスペースのレイヤー」が収まる幅。
const layerCol = 26

// undeclaredCol は、どのルールにも一致しない呼び出しの表の、モードの列の表示幅。
const undeclaredCol = 19

// explainRejectsBudget は却下の走査に使う時間の上限。SessionStart と違って期限が無いので長めに取る。
const explainRejectsBudget = 5 * time.Second

// displayWidth は表示幅（全角は 2 桁）。
func displayWidth(text string) int {
	n := 0
	for _, r := range text {
		if w := pyfmt.EastAsianWidth(r); w == "W" || w == "F" {
			n += 2
		} else {
			n++
		}
	}
	return n
}

// pad は表示幅 width になるまで右に空白を足す。
func pad(text string, width int) string {
	return text + strings.Repeat(" ", max(0, width-displayWidth(text)))
}

// ljust は Python の f"{text:<width}"（字数で数える）。
func ljust(text string, width int) string {
	return text + strings.Repeat(" ", max(0, width-utf8.RuneCountInString(text)))
}

// shown はパスをワークスペースルートからの相対で出す。外に在るなら書かれたまま。
func shown(root, path string) string {
	if path == "" {
		return noneShown
	}
	rel, ok := pathcase.Under(path, root)
	if !ok {
		return path
	}
	return ospath.Slashed(rel)
}

// Explain は `--explain`。いま有効な宣言を、判定を行わずに一覧する。言えるのは「どのルールがどのタイプにあるか」と
// 「チケットの範囲が有効か」まで（パスごとの実効権限は、宣言済み領域という概念がまだ無いので言わない）。
func Explain(stdout, stderr io.Writer, conf *config.Settings, root string) (int, error) {
	p := func(format string, a ...any) { fmt.Fprintf(stdout, format, a...) }
	views := ruleload.Survey(stderr, conf, root)
	source := conf.Rules
	if views[0].Unreadable != "" {
		source = string(builtin.Source)
	}
	p("ccnavi: いま有効な宣言（出所 %s）\n", source)
	p("  パスを持つツールは共通レイヤー + 対象のレイヤー、持たないツールは全部のレイヤーを組み合わせて判定する\n")
	for _, view := range views {
		explainRules(stdout, root, view)
	}
	explainPhases(stdout, conf, root, views)
	explainRisk(stdout, conf, root, views)
	explainUndeclared(stdout, conf)
	explainModes(stdout, conf)

	if projectless := tree.WorktreesWithoutProject(root, conf.Projects); len(projectless) > 0 {
		p("\n■ 元のプロジェクトが無いワークツリー\n")
		for _, w := range projectless {
			p("  %s\n", w.Said())
		}
	}

	p("\n■ チケットの作業範囲（承認済みチケット）\n")
	p("  ワークツリーに結び付いたチケットの範囲は、ルールの allow / ask より優先されます。編集可能範囲の外への書き込みは拒否されます。\n")
	control := conf.TicketControl
	if control == "" {
		control = string(config.Enable)
	}
	p("  チケット制御: %s\n", control)
	learning := config.Disable
	if conf.LearningEnabled() {
		learning = config.Enable
	}
	p("  学びの蓄積: %s\n", learning)
	if !conf.TicketsEnabled() {
		p("  %s=disable。範囲の制限は掛かっていない\n", env.TicketControl)
		return config.ExitOK, nil
	}
	explainTickets(stdout, conf, root)
	return config.ExitOK, nil
}

func explainRules(w io.Writer, root string, view ruleload.LayerView) {
	p := func(format string, a ...any) { fmt.Fprintf(w, format, a...) }
	counts := make([]string, len(rules.Sections()))
	for i, name := range rules.Sections() {
		counts[i] = fmt.Sprintf("%s %d", name, len(*view.RuleSet.Section(name)))
	}
	p("\n■ rules %s（%s、%s）\n", layerLabel(view.Name), shown(root, view.Path), strings.Join(counts, " / "))
	if view.Unreadable != "" {
		p("  読めない: %s。このレイヤーは空として扱う\n", view.Unreadable)
		return
	}
	if view.Missing {
		p("  このレイヤーの設定ファイルはありません。設定なしとして扱います。\n")
		return
	}
	for _, name := range rules.Sections() {
		for _, r := range *view.RuleSet.Section(name) {
			body := r.WrittenBody()
			if len(r.Excepts) > 0 {
				excepts := make([]string, len(r.Excepts))
				for i, e := range r.Excepts {
					excepts[i] = e.Glob
					if excepts[i] == "" {
						excepts[i] = e.Regex
					}
				}
				body += "  except: " + strings.Join(excepts, ", ")
			}
			p("  %s %s %s %s\n", ljust(string(name), 5), ljust(r.ID, 28), ljust(r.Match, 34), body)
			// 組み合わせ条件は 1 行だと入れ子が読みにくいので、個別条件が 3 つ以上なら字下げで続ける。
			if r.When != nil && leaves(r.When) >= 3 {
				for _, line := range r.When.Lines("") {
					p("        %s\n", line)
				}
			}
		}
	}
}

func leaves(c *rules.Condition) int {
	n := 0
	for _, node := range c.Walk() {
		if node.IsLeaf() {
			n++
		}
	}
	return n
}

func explainPhases(w io.Writer, conf *config.Settings, root string, views []ruleload.LayerView) {
	type table struct {
		name       string
		items      []*phases.PhaseType
		unreadable string
	}
	tables := make([]table, len(views))
	counts := make([]string, len(views))
	for i, v := range views {
		items, why := layerPhaseTypes(layerConfig(conf, root, v.Name, config.KindPhases))
		tables[i] = table{v.Name, items, why}
		counts[i] = fmt.Sprintf("%s %d 種", layerLabel(v.Name), len(items))
	}
	fmt.Fprintf(w, "\n■ phases（%s。共通レイヤーと、親チケットの project: で指定されたレイヤーを組み合わせて適用します）\n",
		strings.Join(counts, "、"))
	fmt.Fprintf(w, "  %s%s%s%s%sscope\n", ljust("id", 16), pad("レイヤー", layerCol), ljust("kind", 8), ljust("title", 16),
		ljust("review", 8))
	for _, t := range tables {
		if t.unreadable != "" {
			fmt.Fprintf(w, "  %s: 読めない: %s。このレイヤーは空として扱う\n", layerLabel(t.name), t.unreadable)
		}
		for _, pt := range t.items {
			fmt.Fprintf(w, "  %s%s%s%s%s%s\n", ljust(pt.ID, 16), pad(layerLabel(t.name), layerCol), ljust(string(pt.Kind), 8),
				ljust(pt.Title, 16), ljust(strconv.FormatBool(pt.Review), 8), strings.Join(scopeWords(pt), ", "))
		}
	}
}

func explainRisk(w io.Writer, conf *config.Settings, root string, views []ruleload.LayerView) {
	type table struct {
		name       string
		items      []*risk.Factor
		unreadable string
	}
	tables := make([]table, len(views))
	for i, v := range views {
		items, why := layerRisk(conf, v.Name, layerConfig(conf, root, v.Name, config.KindRisk))
		tables[i] = table{v.Name, items, why}
	}
	// 共通レイヤーの側の既定の閾値。レイヤーの levels はキーごとに小さいほうを採るので、実際の値はチケットのレイヤーで決まる。
	common, _ := risk.Load(conf.Risk)
	t := risk.EffectiveLevels(common.Levels)
	fmt.Fprintf(w, "\n■ risk（levels: medium %d / high %d / critical %d）\n", t.Medium, t.High, t.Critical)
	fmt.Fprintf(w, "  %s%s%s%smessage\n", ljust("id", 16), pad("レイヤー", layerCol), ljust("加点条件", 20), ljust("points", 8))
	for _, tb := range tables {
		if tb.unreadable != "" {
			fmt.Fprintf(w, "  %s: 読めない: %s。このレイヤーは空として扱う\n", layerLabel(tb.name), tb.unreadable)
		}
		for _, f := range tb.items {
			how := fmt.Sprintf("%s %s", f.Kind, pyfmt.Str(factorValue(f)))
			fmt.Fprintf(w, "  %s%s%s%s%s\n", ljust(f.ID, 16), pad(layerLabel(tb.name), layerCol), ljust(how, 20),
				ljust(strconv.Itoa(f.Points), 8), f.Message)
		}
	}
}

// undeclaredOutcome は、どのルールも言及しない呼び出しの結末（judge.UndeclaredVerdict の値）をユーザ向けの言葉にする。
var undeclaredOutcome = map[rules.Decision]string{
	judge.Handover: "権限モードに委ねる",
	rules.Ask:      "ccnavi が確認を出す",
	rules.Deny:     "ccnavi が止める",
}

// handoverNote は委ねた先がモードごとに違うときの補足。auto だけは判断するのが classifier。
var handoverNote = map[string]string{"auto": "（classifier が判断）"}

// unknownMode は名前を知らないモードと、payload に権限モードが無い呼び出しの行の見出し（判定はどちらも同じ扱い）。
const unknownMode = "(不明・無し)"

// explainUndeclared は、どのルールも言及しない呼び出しの結末を、権限モード × 読めたか で表にする。判定と同じ関数で組む
// （文で書くと、判定を変えたときにここだけ説明が残る）。conf.GuardUnwatched は hook と同じく解いた後の値。
func explainUndeclared(w io.Writer, conf *config.Settings) {
	fmt.Fprintf(w, "\n■ どのルールにも一致しないツール呼び出し\n")
	fmt.Fprintf(w, "  ccnavi は判定を持たない。読めた呼び出しは Claude Code の権限モードに委ねる。"+
		"読み切れない呼び出し（実行される部分まで読み解けず、生の文字列に適用したコマンド）は、"+
		"読めなかったことを言うためにモードを問わず委ねない\n")
	for _, name := range slices.Concat(judge.PermissionJudged(), judge.PermissionNoJudge(), []string{unknownMode}) {
		pm := name
		if name == unknownMode {
			pm = ""
		}
		read := judge.UndeclaredVerdict(pm, "", conf.GuardUnwatched)
		unread := judge.UndeclaredVerdict(pm, "x", conf.GuardUnwatched)
		said := undeclaredOutcome[read]
		if read == judge.Handover {
			said += handoverNote[name]
		}
		fmt.Fprintf(w, "    %s%s読めた: %s / 読み切れない: %s\n", name,
			strings.Repeat(" ", max(1, undeclaredCol-displayWidth(name))), said, undeclaredOutcome[unread])
	}
	unwatched := conf.GuardUnwatched
	if unwatched == "" {
		unwatched = string(config.Enable)
	}
	fmt.Fprintf(w, "  %s=%s（%s の読めた呼び出しの結末を決める。disable なら委ね、それ以外は止める）\n",
		env.GuardUnwatched, unwatched, strings.Join(judge.PermissionNoJudge(), " / "))
}

// failClosedRows は、判定に達せなかったときの扱いを見せるツールの代表と、その見出し。扱いは config.FailClosed で引く。
var failClosedRows = []struct{ tool, label string }{
	{"", "ツールが分からない"},
	{config.ToolWrite, "Write・Edit・MultiEdit・NotebookEdit・Agent・Task"},
	{config.ToolRead, "Read・Grep・Glob"},
	{"SubagentHandback", "サブエージェントの報告用（SubagentHandback・StructuredOutput）"},
	{config.ToolBash, "Bash"},
	{"Foo", "知らないツール"},
}

// explainModes は、dry-run で止めるものと止めないもの、判定に達せなかったときの扱い。分類とツールごとの扱いは判定と
// 同じ定数・関数から組む。
func explainModes(w io.Writer, conf *config.Settings) {
	p := func(format string, a ...any) { fmt.Fprintf(w, format, a...) }
	p("\n■ dry-run のときに止めるもの\n")
	p("  一般の保護（ルールの deny・ask）: dry-run は止めずに報告だけにする\n" +
		"  チケットのゲート（状態の操作、承認済みの置き場、範囲、レビュー待ち）と、その抜け道をふさぐ補助の保護（生の git、環境変数の指定など）: dry-run でも止める\n")
	if !conf.TicketsEnabled() {
		p("  %s=disable なので、ゲートと補助の保護も効いていない\n", env.TicketControl)
	}
	p("\n■ 判定に達せなかったとき（内部の例外・期限切れ・payload が読めない）\n")
	held := func(code int) string {
		if code != config.ExitOK {
			return "止める"
		}
		return "通す"
	}
	for _, row := range failClosedRows {
		enable := held(config.FailClosed(config.Enable, row.tool, false))
		dry := held(config.FailClosed(config.DryRun, row.tool, false))
		safe := held(config.FailClosed(config.Enable, row.tool, true))
		switch {
		case slices.Contains(config.ReadTools(), row.tool):
			p("  %s: 最低限の deny にヒットしなければ%s、ヒットすれば%s\n", row.label, safe, enable)
		case slices.Contains(config.ShellTools(), row.tool):
			p("  %s: 最低限の許可リストにヒットすれば%s、ヒットしなければ%s\n", row.label, safe, enable)
		default:
			p("  %s: enable は%s、dry-run は%s\n", row.label, enable, dry)
		}
	}
	p("  内部の期限には限りがあり、期限切れも「判定に達せなかった」として上の扱いになる\n")
}

// reviewNeed は子の宣言したレビューの要否の言葉。
var reviewNeed = map[ticket.Need]string{ticket.Needed: "要", ticket.Unneeded: "不要", ticket.Unsaid: "宣言なし"}

// explainTickets は承認済みチケットを 1 件ずつ、作業範囲として書く。置き場はここで 1 度だけ読み、局面とフェーズまで
// 持ち回る。読むだけで、置き場のファイルを動かさない。
func explainTickets(w io.Writer, conf *config.Settings, root string) {
	p := func(format string, a ...any) { fmt.Fprintf(w, format, a...) }
	raw := approval.ReadRaw(conf, root)
	copies, notes := approval.Scan(conf, root, false, raw)
	for _, note := range notes {
		p("  %s\n", note)
	}
	if len(copies) == 0 {
		p("  承認されたチケットがありません。範囲の制限は掛かっていない\n")
		return
	}
	closed, _ := approval.Scan(conf, root, true, raw)
	review, _ := approval.ScanReview(conf, root, raw)
	proposals, _ := ticket.Scan(root, conf.Tickets, conf.Projects, nil)
	preds := approval.PoolOf(copies, review, closed, proposals)
	preds.AlignImported(conf, root)
	listed := slices.Concat(copies, review)
	times := approval.ApprovedTimes(conf, listed)
	sorted := slices.Clone(listed)
	sort.SliceStable(sorted, func(i, j int) bool {
		a, b := familyOf(sorted[i]), familyOf(sorted[j])
		if a != b {
			return a < b
		}
		return sorted[i].ID < sorted[j].ID
	})
	for _, t := range sorted {
		bound := "ワークツリー無し"
		if tree.IsWorktreeOf(root, tree.WorktreePath(root, t.ID)) {
			bound = "ワークツリーあり"
		}
		where := "作業中"
		if t.State == place.Review {
			where = "レビュー待ち"
		}
		head := fmt.Sprintf("%s（%s、承認 %s、%s、%s）", t.ID, t.Title, times[t.Path].Label(), where, bound)
		if t.IsChild() {
			head += fmt.Sprintf(" 親 %s フェーズ %d 子の宣言のレビュー%s", t.Parent, t.Phase, reviewNeed[t.ReviewRequired])
			if unmet := approval.UnmetPredecessors(t, preds); len(unmet) > 0 {
				parts := make([]string, len(unmet))
				for i, u := range unmet {
					parts[i] = fmt.Sprintf("%s（%s）", u.Ticket, u.Label())
				}
				head += " 先行を満たしていない: " + strings.Join(parts, ", ")
			}
		}
		p("  %s\n", head)
		for _, name := range rules.Sections() {
			for _, path := range t.Paths(name) {
				p("    %s %s\n", ljust(string(name), 5), path)
			}
		}
	}
	for _, parent := range copies {
		if !parent.IsChild() {
			explainParent(w, conf, root, parent, raw)
		}
	}
	explainRejects(w, conf, root, copies)
}

func familyOf(t *ticket.Ticket) string {
	if t.Parent != "" {
		return t.Parent
	}
	return t.ID
}

func explainParent(w io.Writer, conf *config.Settings, root string, parent *ticket.Ticket, raw *approval.Raw) {
	p := func(format string, a ...any) { fmt.Fprintf(w, format, a...) }
	if stage := phase.Stage(conf, root, parent, raw); stage != "" {
		p("  %s の局面: %s\n", parent.ID, stage)
	}
	home := approval.HomeDirRead(conf, root, parent.ID, "", parent.Project)
	if wrapped, ok := marks.ReadFilledParent(home, parent.ID, marks.CloseEarly); ok {
		reason := ""
		if v, ok := wrapped.Get("reason"); ok {
			reason = pyfmt.Str(v)
		}
		p("  %s はユーザが早めにクローズした: %s\n", parent.ID, reason)
	}
	if _, ok := marks.ReadFilledParent(home, parent.ID, marks.Ready); ok {
		p("  %s のマージリクエストの Draft を解除した。マージはユーザが行う\n", parent.ID)
	}
	for _, ph := range phase.Phases(conf, root, parent.ID, raw) {
		var present []string
		for kind := range ph.Marks {
			present = append(present, string(kind))
		}
		sort.Strings(present)
		shownMarks := strings.Join(present, ", ")
		if shownMarks == "" {
			shownMarks = "マーカーなし"
		}
		state := "進行中"
		switch {
		case len(ph.Tickets) == 0:
			state = "未計画（子チケットはまだありません）"
		case ph.Ended():
			state = "終了"
		}
		hold := "止めていない"
		if ph.GateClosed() {
			hold = ph.ReviewLabel()
		}
		review := ""
		if ph.ReviewRequired() {
			review = " / レビューはマージリクエストで"
		}
		if at, ok := ph.ReviewAt(); ph.Deferred() {
			review = fmt.Sprintf(" / レビューは %s と一緒に", optionalInt(at, ok))
		} else if covers := ph.Covers(); len(covers) > 0 {
			review += fmt.Sprintf(" / %s の分も見る", joinInts(covers, ", "))
		}
		if line := ph.RiskLine(); line != "" {
			review += " / " + line
			if ph.RiskEscalates() {
				review += "（実績でレビュー必須）"
			}
		}
		if ph.Planned() {
			if waits := phases.WaitsOf(parent, ph.Number); len(waits) > 0 {
				review += " / 先行 " + joinInts(waits, ", ")
			} else {
				review += " / 先行フェーズなし"
			}
		}
		p("  %s フェーズ %s: %s / %s / %s%s\n", parent.ID, ph.Label(), state, shownMarks, hold, review)
	}
}

func optionalInt(n int, ok bool) string {
	if !ok {
		return "None"
	}
	return strconv.Itoa(n)
}

func joinInts(ns []int, sep string) string {
	parts := make([]string, len(ns))
	for i, n := range ns {
		parts[i] = strconv.Itoa(n)
	}
	return strings.Join(parts, sep)
}

// explainRejects は上位の設定に却下された要求を全件見せる（SessionStart は数件に絞る）。見せるだけで判定は変えない。
// 一覧の足しなので、走査に失敗しても --explain は止めない。
func explainRejects(w io.Writer, conf *config.Settings, root string, copies []*ticket.Ticket) {
	found, err := notice.FindRejects(conf, root, notice.RejectOptions{Budget: explainRejectsBudget, Copies: copies})
	if err != nil || found == nil || len(found.Rejects) == 0 {
		return
	}
	fmt.Fprintf(w, "\n■ 上位の設定に却下された要求\n")
	for _, line := range notice.ExplainLines(found) {
		fmt.Fprintf(w, "%s\n", line)
	}
}
