package scaffold

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
	"ccnavi/internal/tickets/proposal/planedit"
	"ccnavi/internal/tickets/proposal/templates"
)

// Request は ticket new の引数。Issue と MR は親の作成と承認の前提で、nil は渡されていない。Template を渡すと
// Plan（型の列）の代わりにテンプレートから計画と子のフローの下書きを作る（Layer はテンプレートを統合結果で引く
// レイヤー。空なら cwd のツリーのレイヤー）。フィードバック計画は WithFeedback のときだけ書く。Scope は今回の作業の
// 範囲（glob）で、型の scope を集めたものに足して allow に書く。
type Request struct {
	ID           string
	Title        string
	Plan         string
	Issue        *string
	MR           *int
	Template     string
	Layer        string
	WithFeedback bool
	Scope        []string
}

// used はテンプレートから組んだ、書く中身。skipped は書かなかったフィードバック計画のステップ数。
type used struct {
	found    *templates.Found
	plan     []templates.Item
	feedback []templates.Item
	drafts   []templates.Draft
	skipped  int
}

// New は ticket new の本体。書けたら 0、書かなければ 1（理由は標準エラー）。書くのは親の提案と子のフローの下書き
// だけで、どこかで失敗したら書いたものを消して戻す。ネットワークにも git にも触らない。
func New(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, req Request) int {
	refuse := refuser(stderr, "new")
	ident := req.ID
	if why := identProblem(ident); why != "" {
		return refuse(why)
	}
	if isChild(ident) {
		return refuse(ident + " は子の識別子の形（<親>-<NN>-<NN>）。雛形を出すのは親だけ。" +
			"子の提案は手で書く（書式は " + config.Launcher + " --docs --keyword 提案書式）")
	}
	title := ospath.Strip(req.Title)
	if title == "" {
		return refuse("--title <タイトル> が必要です")
	}
	if strings.ContainsFunc(req.Title, lineBreaking) {
		return refuse("--title に改行や制御文字は指定できない")
	}
	if strings.HasPrefix(title, "<") && strings.HasSuffix(title, ">") {
		return refuse("--title " + pyfmt.Repr(title) + " は案内の仮の値。実際のタイトル（issue や MR のタイトル）に置き換える")
	}
	// チケット作業は issue と MR のセット。どちらかが無い親の提案は作らない
	if req.Issue == nil {
		return refuse("--issue <番号> が必要です（別のリポジトリなら owner/repo#番号）。ccnavi-start.sh が「必須」の行で出す")
	}
	if req.MR == nil {
		return refuse("--mr <番号> が必要です。ccnavi-start.sh が「必須」の行で出す")
	}
	issue := ticket.IssueText(*req.Issue)
	if issue == "" {
		return refuse("--issue には正の整数か owner/repo#番号 を渡す")
	}
	mr := *req.MR
	if mr <= 0 {
		return refuse("--mr には正の整数を渡す")
	}
	plan := parsePlan(req.Plan)
	switch {
	case req.Template != "":
		if ospath.Strip(req.Plan) != "" {
			return refuse("--template と --plan は同時に指定できない（計画はテンプレートから作る）")
		}
	case req.Layer != "":
		return refuse("--layer は --template と一緒に使う")
	case req.WithFeedback:
		return refuse("--with-feedback は --template と一緒に使う")
	case len(plan) == 0:
		return refuse(`--plan "design,acceptance,implement" のように型をカンマで区切って渡す`)
	}

	where, why := parentWorktree(conf, root, cwd, ident)
	if why != "" {
		return refuse(why)
	}
	types := phases.LoadTypes(conf, root, where.Project)
	if types == nil {
		return refuse("フェーズ定義（" + layerLabel(where.Project) + " のレイヤーの phases.yml）が無いか読めない。" +
			"plan の型の定義を読めず、承認の検査を通る plan とコピー（phases:）を作れない" +
			"（設計 9.7）。phases.yml を用意してから実行します。")
	}
	var u *used
	var scoped []string
	if req.Template != "" {
		u, why = fromTemplate(conf, root, where, types, ident, req.Template, req.Layer, req.WithFeedback)
		if u == nil {
			return refuse(why)
		}
		for _, item := range append(append([]templates.Item{}, u.plan...), u.feedback...) {
			scoped = append(scoped, item.Type)
		}
	} else {
		var unknown, feedback []string
		for _, n := range unique(plan) {
			pt := types.Get(n)
			switch {
			case pt == nil:
				unknown = append(unknown, n)
			case pt.Kind != phases.Work:
				feedback = append(feedback, n)
			}
		}
		if len(unknown) > 0 {
			var work []string
			for p := range types.All() {
				if p.Kind == phases.Work {
					work = append(work, p.ID)
				}
			}
			return refuse("plan の型 " + strings.Join(unknown, ", ") + " が phases.yml にありません。指定できる型: " +
				strings.Join(work, ", "))
		}
		if len(feedback) > 0 {
			return refuse("plan の型 " + strings.Join(feedback, ", ") + " は feedback の定義。plan には work の定義だけを置く")
		}
		scoped = plan
	}

	allow, inherited := collectAllow(types, scoped)
	extra, why := parseScope(req.Scope)
	if why != "" {
		return refuse(why)
	}
	seen := map[string]bool{}
	for _, g := range allow {
		seen[pyfmt.Lower(g)] = true
	}
	for _, g := range extra {
		if !seen[pyfmt.Lower(g)] {
			allow = append(allow, g)
		}
	}
	if len(allow) == 0 {
		return refuse("plan の型（" + strings.Join(inherited, ", ") + "）は scope: inherit のため、範囲を親の allow から" +
			"継承します。今回の範囲が未指定で、allow が集まりません（allow か ask が 1 件は" +
			"必要です）。ユーザに今回編集するファイルを確認し、--scope <glob> で" +
			"指定して再実行してください" +
			`（例: --plan quick-work --scope "src/ui/error.ts"）。` +
			"範囲を持つ型を追加する必要はありません")
	}
	if len(allow) > ticket.MaxScopeEntries {
		return refuse(fmt.Sprintf("集めた範囲が %d 件で、上限の %d 件を超える。plan の型を減らす", len(allow), ticket.MaxScopeEntries))
	}

	if taken := existing(conf, where.Root, ident); taken != "" {
		return refuse(ident + " の提案か承認済みチケットが既に在る（" + taken + "）。上書きしない")
	}
	if u != nil {
		if clash := flowClash(conf, where.Root, u); clash != "" {
			return refuse(clash)
		}
	}

	path := proposalPath(conf, where.Root, place.Todo, ident)
	var text string
	if u == nil {
		text = render(ident, title, linear(plan), nil, allow, issue, mr)
	} else {
		text = render(ident, title, u.plan, u.feedback, allow, issue, mr)
	}
	if failed := writeNew(path, []byte(text), path); failed != "" {
		return refuse(failed)
	}

	// コピー（phases:）を差し込む。無いと --agree が「ステップの定義がコピーに無い」で落ちる。
	filled := path + " を親の提案として読めない"
	if target, _ := ticket.Load(path); target != nil {
		target.Project = where.Project
		filled = planedit.Fill(conf, root, target, nil).Why
	}
	if filled != "" {
		fsio.Remove(path)
		return refuse("phases: を差し込めなかった（" + filled + "）")
	}

	// コピーを差し込んだあとの提案を読み直して、allow が定義の上限を超えていないかを見る
	var over []rules.Problem
	if again, _ := ticket.Load(path); again != nil {
		over = phases.ParentScopeProblems(again)
	}
	var written []string
	if u != nil {
		// 承認の検査（定義の kind と置き場、順序）を、書いた提案に当てる。通らなければ書き戻す。
		if problem := agreeProblem(conf, root, path, where.Project); problem != "" {
			fsio.Remove(path)
			return refuse("テンプレートから作った提案が承認の検査に合格しない（" + problem + "）")
		}
		if failed := writeFlows(conf, where.Root, u, &written); failed != "" {
			for _, rolled := range written {
				fsio.Remove(rolled)
			}
			fsio.Remove(path)
			return refuse(failed)
		}
	}

	note := "template"
	if u == nil {
		note = "plan"
		fmt.Fprintf(stdout, "%s の雛形を %s に書いた\n", ident, path)
		fmt.Fprintf(stdout, "plan: %s（前のステップが各ステップに先行する一直線）\n", strings.Join(plan, " -> "))
	} else {
		report(stdout, ident, path, u, written)
	}
	fmt.Fprint(stdout, "allow（phases.yml の scope を集めた）:\n")
	for _, g := range allow {
		fmt.Fprintf(stdout, "  %s %s\n", allowMatch, g)
	}
	for _, p := range over {
		fmt.Fprintf(stdout, "案内: 承認しても書けない: %s\n", p.Detail)
	}
	fmt.Fprintln(stdout, texts.Say(root, "NEXT_NEW_REWRITE", texts.Values{"path": path}, nil))
	fmt.Fprintln(stdout, texts.Say(root, "NOTE_NEW_TAIL", nil, texts.Variants{"note": note}))
	// 子が無いと承認の確認と approval-request が警告する。必須の行で独立させる
	fmt.Fprintf(stdout, "必須: 子の提案（'%s-<フェーズ番号 2 桁>-<連番 2 桁>'。例: '%s-01-01'）を、"+
		"すぐ始まるステップごとに 1 つ以上 wip/proposals/todo/ に手で書く。"+
		"親と同じ承認で通すので、先に書く"+
		"（書式は %s --docs --keyword 提案書式）。"+
		"無いと approval-request（--check も）が警告し、子の承認がもう 1 回要る\n", ident, ident, config.Launcher)
	fmt.Fprintln(stdout, texts.Say(root, "NOTE_NEW_SEND", texts.Values{"ident": ident}, nil))
	return config.ExitOK
}

func layerLabel(project string) string {
	if project == "" {
		return "自身"
	}
	return project
}

// unique は初めて出た順の重複なし。
func unique(list []string) []string {
	var out []string
	for _, s := range list {
		if !contains(out, s) {
			out = append(out, s)
		}
	}
	return out
}

// fromTemplate はテンプレートを引いて、書く中身を組む。組めなければ (nil, 理由)。何も書かない。フィードバック
// 計画は全体計画の最後のレビューを見て改版で 1 回だけ足す決まりなので、withFeedback が無ければ書かない。
func fromTemplate(conf *config.Settings, root string, where tree.Tree, types *phases.Types,
	ident, name, layer string, withFeedback bool) (*used, string) {
	if layer == "" {
		layer = where.Project
	}
	if layer == "" {
		layer = config.LayerSelf
	}
	found, why := templates.Resolve(conf, root, layer, name)
	if found == nil {
		return nil, why
	}
	plan, feedback := templates.Absolute(found.Template)
	skipped := 0
	if !withFeedback {
		skipped, feedback = len(feedback), nil
	}
	var names []string
	for _, item := range append(append([]templates.Item{}, plan...), feedback...) {
		names = append(names, item.Type)
	}
	var missing []string
	for _, n := range unique(names) {
		if types.Get(n) == nil {
			missing = append(missing, n)
		}
	}
	if len(missing) > 0 {
		return nil, "テンプレート " + pyfmt.Repr(name) + " のステップの定義 " + strings.Join(missing, ", ") + " が " +
			"phases.yml にありません（" + layerLabel(where.Project) + " のレイヤー）。テンプレートを直すか、" +
			"ユーザに phases.yml へ追加してもらう"
	}
	for _, p := range []struct {
		key   string
		items []templates.Item
		kind  phases.Kind
	}{{"plan", plan, phases.Work}, {"feedback", feedback, phases.Feedback}} {
		var wrong []string
		for _, item := range p.items {
			if types.Get(item.Type).Kind != p.kind {
				wrong = append(wrong, item.Type)
			}
		}
		if len(wrong) > 0 {
			return nil, fmt.Sprintf("テンプレート %s の %s の型 %s は kind が %s でない。%s には %s の定義だけを置く",
				pyfmt.Repr(name), p.key, strings.Join(unique(wrong), ", "), p.kind, p.key, p.kind)
		}
	}
	if why := templates.ChildProblem(ident, found.Template); why != "" {
		return nil, why
	}
	drafts, why := templates.FlowDrafts(ident, found.Template)
	if why != "" {
		return nil, why
	}
	return &used{found, plan, feedback, drafts, skipped}, ""
}

// flowClash は子のフローの下書きを書けない理由（在るものは上書きしない）。書けるなら空。
func flowClash(conf *config.Settings, top string, u *used) string {
	if len(u.drafts) == 0 {
		return ""
	}
	if pathmatch.HasLink(top, ospath.Dirname(flow.DraftFile(conf, top, u.drafts[0].Child))) {
		return "フロー下書きの置き場（" + flow.TicketsRel(conf) + "/" + flow.FlowsDir + "/）の途中にリンクがあるので反映しません"
	}
	for _, d := range u.drafts {
		if ospath.DiskLexists(flow.DraftFile(conf, top, d.Child)) {
			return "フロー下書き " + flow.DraftRel(conf, d.Child) + " が既に在る。上書きしない"
		}
		if ospath.DiskLexists(flow.FlowFile(conf, top, d.Child)) {
			return "子 " + d.Child + " のフロー（" + flow.FlowRel(conf, d.Child) + "）が既に在る。上書きしない"
		}
		if taken := existing(conf, top, d.Child); taken != "" {
			return d.Child + " の提案か承認済みチケットが既に在る（" + taken + "）。上書きしない"
		}
	}
	return ""
}

// writeFlows は子のフローの下書きを書く。書いたパスを written に足す。書けなければ理由。
func writeFlows(conf *config.Settings, top string, u *used, written *[]string) string {
	for _, d := range u.drafts {
		at := flow.DraftFile(conf, top, d.Child)
		if failed := writeNew(at, []byte(d.Text), "フロー下書き "+flow.DraftRel(conf, d.Child)); failed != "" {
			return failed
		}
		*written = append(*written, at)
	}
	return ""
}

// agreeProblem は書いた提案に承認の検査を当てた error。無ければ空。
func agreeProblem(conf *config.Settings, root, path, project string) string {
	target, _ := ticket.Load(path)
	if target == nil {
		return "親の提案として読めない"
	}
	target.Project = project
	var errs []string
	for _, p := range candidates.PlanProblems(target, phases.LoadTypes(conf, root, project), nil, false) {
		if p.Severity == rules.Error {
			errs = append(errs, p.Detail)
		}
	}
	return strings.Join(errs, " / ")
}

func planText(items []templates.Item, first int) string {
	parts := make([]string, len(items))
	for i, item := range items {
		var extra []string
		if len(item.After) > 0 {
			nums := make([]string, len(item.After))
			for k, n := range item.After {
				nums[k] = fmt.Sprint(n)
			}
			extra = append(extra, "先行 "+strings.Join(nums, ","))
		}
		if item.Review.Said() {
			if item.Review == ticket.Needed {
				extra = append(extra, "review")
			} else {
				extra = append(extra, "review なし")
			}
		}
		if item.Deferred {
			extra = append(extra, "deferred")
		}
		parts[i] = fmt.Sprintf("%d %s", first+i, item.Type)
		if len(extra) > 0 {
			parts[i] += "（" + strings.Join(extra, "・") + "）"
		}
	}
	return strings.Join(parts, " / ")
}

// report はテンプレートから作ったときの出力。何を書いたか、フローの下書きの効力。
func report(stdout io.Writer, ident, path string, u *used, written []string) {
	f := u.found
	fmt.Fprintf(stdout, "%s の提案を %s に書いた（テンプレート %s、%s の %s）\n", ident, path, f.Template.Name, f.Entry.Layer, f.Entry.Rel)
	fmt.Fprintf(stdout, "plan（%d ステップ）: %s\n", len(u.plan), planText(u.plan, 1))
	if len(u.feedback) > 0 {
		fmt.Fprintf(stdout, "feedback（%d ステップ）: %s\n", len(u.feedback), planText(u.feedback, len(u.plan)+1))
	} else if u.skipped > 0 {
		fmt.Fprintf(stdout, "テンプレートにはフィードバック計画（%d ステップ）がある。"+
			"全体計画のレビューを見てから追加するなら、`--with-feedback` を付けて作り直すか、"+
			"提案を改版で追加します。\n", u.skipped)
	}
	for _, note := range f.Notes {
		fmt.Fprintf(stdout, "note: %s\n", note)
	}
	for _, warn := range f.Warns {
		fmt.Fprintf(stdout, "warn: %s\n", warn)
	}
	if len(written) == 0 {
		fmt.Fprint(stdout, "フローの下書きはありません（テンプレートに flows がありません）\n")
		return
	}
	fmt.Fprintf(stdout, "フローの下書き %d 件:\n", len(written))
	for i, at := range written {
		fmt.Fprintf(stdout, "  計画の %d 番目 -> %s: %s\n", u.drafts[i].Index, u.drafts[i].Child, at)
	}
	fmt.Fprintf(stdout, "下書きには効力がありません。フローの文は子への案内文になるので、ユーザがフロー編集画面で"+
		"読んで取り込むまで効かない。子の識別子は各フェーズの最初の子"+
		"（%s-<NN>-01）と仮定した。別の連番で子を起こすなら下書きの名前を合わせる\n", ident)
}

// existing は同じ識別子の提案か承認済みチケットがこのツリーに在れば、そのパス。無ければ空。
func existing(conf *config.Settings, top, ident string) string {
	approved := conf.ApprovedDir(top)
	for _, c := range []struct {
		base   string
		states []place.State
	}{{ticketsDir(conf, top), []place.State{place.Todo, place.Review}}, {approved, []place.State{place.Doing, place.Done}}} {
		for _, state := range c.states {
			candidate := ospath.Join(c.base, string(state), ident+config.MdExt)
			if ospath.DiskLexists(candidate) {
				return candidate
			}
		}
	}
	return ""
}
