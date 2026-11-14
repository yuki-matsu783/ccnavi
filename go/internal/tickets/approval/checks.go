package approval

import (
	"sort"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

func problem(t *ticket.Ticket, detail string) rules.Problem {
	return rules.Problem{Severity: rules.Error, Rule: t.ID, Detail: detail}
}

func treeLabel(t *ticket.Ticket) string {
	if t.Tree == "" {
		return workspaceLabel
	}
	return t.Tree
}

// ByID は識別子ごとのチケット（同じ識別子は後のもの）。判定が parent を引く索引。
func ByID(tickets []*ticket.Ticket) map[string]*ticket.Ticket {
	out := make(map[string]*ticket.Ticket, len(tickets))
	for _, t := range tickets {
		out[t.ID] = t
	}
	return out
}

// ChildrenOf はこの親の子（並びはそのまま）。
func ChildrenOf(tickets []*ticket.Ticket, parentID string) []*ticket.Ticket {
	var out []*ticket.Ticket
	for _, t := range tickets {
		if t.Parent == parentID {
			out = append(out, t)
		}
	}
	return out
}

// FamilyStanding はこのチケットが属する親子のチケットの立ち位置（parent、無ければ自分の識別子で引く）。
// fams が nil なら作る。
func FamilyStanding(conf *config.Settings, root string, t *ticket.Ticket, fams *standing.Families) standing.Standing {
	if fams == nil {
		fams = standing.NewFamilies(conf, root)
	}
	return fams.Standing(homeOf(t.ID, t.Parent), t.Project)
}

// markImported は、親子のチケットのうち、信頼できないものに理由を足す。
// 正とするのは、親のブランチ（親のワークツリー）の上のチケットだけ。
// 親子のチケットが決まらないものと、親のワークツリーの外にしか無いものは、読むが信頼しない。
// リストからは外さない。外すと、「在る」ことで止まっていたもの（承認待ちの重複、開いた子のある親を閉じない）が通ってしまう。
func markImported(conf *config.Settings, root string, kept []*ticket.Ticket) {
	fams := standing.NewFamilies(conf, root)
	for _, t := range kept {
		st := FamilyStanding(conf, root, t, fams)
		why := st.Stop
		if why == "" && st.Home != nil && !standing.SameTree(t.TreeRoot, st.Home.Root) {
			why = outsideReason(st, t)
		}
		if why != "" && !strings.Contains(t.Blocked, why) {
			if t.Blocked != "" {
				t.Blocked += " / " + why
			} else {
				t.Blocked = why
			}
		}
	}
}

// outsideReason は親のワークツリーの外にしか無いチケットを信頼しない理由と、ユーザが打つ手順。
func outsideReason(st standing.Standing, t *ticket.Ticket) string {
	return "親のブランチ " + st.BranchName() + " のワークツリーの外" +
		"（" + treeLabel(t) + "）にしか無いチケット。" +
		"取り込み済みの親子のチケットでは、親のブランチ上のチケットだけが正。ユーザがそのチケットを親のワークツリー" +
		"（.claude/worktrees/" + st.Family + "）の同じ置き場へ移してコミットと push をし、" +
		"元のチケットを削除します。"
}

// FamilyProblems は親子のチケットの提案を承認しない理由。親子のチケットが決まらない・閉じているとき、提案が
// 親のワークツリーの外にあるとき（提案は親のブランチの上で書き、approval-request で送ってから承認を頼む決まり）。
func FamilyProblems(conf *config.Settings, root string, t *ticket.Ticket, fams *standing.Families) []rules.Problem {
	st := FamilyStanding(conf, root, t, fams)
	if st.Stop != "" {
		return []rules.Problem{problem(t, familyStopText(root, st))}
	}
	if st.Home != nil && !standing.SameTree(t.TreeRoot, st.Home.Root) {
		return []rules.Problem{problem(t, "提案が親のブランチ "+st.BranchName()+" のワークツリーの外"+
			"（"+treeLabel(t)+"）にある。親子のチケットの提案は親のブランチの"+
			"ワークツリーで書く決まり。\n"+
			texts.Say(root, "NEXT_APPROVAL_PROPOSAL_OUTSIDE", texts.Values{"family": st.Family}, nil))}
	}
	return nil
}

// familyStopText は親子のチケットが決まらないので承認しない理由と、ユーザが打つ手順。
func familyStopText(root string, st standing.Standing) string {
	return st.Stop + "。承認しない。" + strings.Join(standing.Guidance(root, st), " / ")
}

// MRProblems は親チケットに issue: と mr: が記録されているか（親の作成と承認の前提。子には要らない）。
func MRProblems(t *ticket.Ticket) []rules.Problem {
	if t.IsChild() {
		return nil
	}
	var out []rules.Problem
	if t.Issue == 0 {
		out = append(out, problem(t, "親チケット "+t.ID+" に issue: が記録されていない。`ccnavi-start.sh` が出した "+
			"issue を issue: に書いてください（例 `issue: 12`、別のリポジトリなら "+
			"`issue: owner/repo#12`）。チケット作業は issue と MR のセットで進める"))
	}
	if t.MR == 0 {
		out = append(out, problem(t, "親チケット "+t.ID+" に mr: が記録されていない。`ccnavi-start.sh` が出したマージ"+
			"リクエストの番号を mr: に書いてください（例 `mr: 12`）。"+
			"親のブランチのマージリクエストが"+
			"無いと、レビューの依頼も正の特定もできない"))
	}
	return out
}

// BranchProblems は親の識別子を承認してよいか。2 つの親子のチケットが大文字小文字だけ違う識別子なら
// ワークツリーとブランチの名前がぶつかるので承認しない。others は比べるチケット（承認済みと承認待ち）。
func BranchProblems(t *ticket.Ticket, others []*ticket.Ticket) []rules.Problem {
	if t.IsChild() {
		return nil
	}
	seen := map[string]bool{}
	var clash []string
	for _, o := range others {
		if o.ID != t.ID && homeOf(o.ID, o.Parent) != t.ID && o.Project == t.Project &&
			pyfmt.Casefold(o.ID) == pyfmt.Casefold(t.ID) && !seen[o.ID] {
			seen[o.ID] = true
			clash = append(clash, o.ID)
		}
	}
	if len(clash) == 0 {
		return nil
	}
	sort.Strings(clash)
	return []rules.Problem{problem(t, "親の識別子 "+t.ID+" と大文字小文字だけが違う識別子（"+strings.Join(clash, ", ")+"）が"+
		"ある。ワークツリーとブランチの名前がぶつかるので承認しない")}
}

// MisplacedHomeText は承認済みチケットが親のワークツリーに無いときの理由と直し方（HomeProblem と ready が使う）。
// push と取り込みの sh はツリーの間でチケットを動かさないので、承認済みチケットは手で動かさず承認し直す。
func MisplacedHomeText(root, family, shown string) string {
	worktreeSh := config.ScriptCommand(root, config.WorktreeWrapper)
	return "親 " + family + " の承認済みチケットが親のワークツリー" +
		"（" + tree.WorktreesDir + "/" + family + "）ではなく " + shown + " に在る。" +
		"ccnavi は親のワークツリーを「名前が親の識別子のもの」として探すので、" +
		"別名のツリーで承認したチケットは、取り込み・クローズ済みのチケットの退避・" +
		"Draft を解除する ready の対象にならない。承認済みチケットは手で動かさない。直す手順: " +
		"1) 親のワークツリーが無ければ '" + worktreeSh + " " + family + "' で切る " +
		"2) " + shown + " にある " + family + " の提案（wip/proposals/ の todo/ か review/）を" +
		"親のワークツリーの同じ相対位置に移す " +
		"3) ユーザに承認を取り直してもらう（承認済みチケットが親のワークツリーに置かれる） " +
		"4) " + shown + " に残った " + family + " の承認済みチケットの片付けをユーザに聞く。" +
		"ccnavi-user-push-approved.sh と ccnavi-sync.sh は各ツリーの置き場を送る・取り込むだけで、" +
		"ツリーの間でチケットを動かさない"
}

// misplacedTree はチケットのツリーが、識別子と違う名前のワークツリーならその名前と親の識別子。
// ワークスペースルートとプロジェクトの元ツリーは言わない。
func misplacedTree(root string, conf *config.Settings, t *ticket.Ticket) (name, family string, ok bool) {
	if t.TreeRoot == "" {
		return "", "", false
	}
	home, found := tree.TreeOf(root, t.TreeRoot, conf.Projects)
	if !found || home.IsMain() {
		return "", "", false
	}
	family = homeOf(t.ID, t.Parent)
	if home.Name == family {
		return "", "", false
	}
	return home.Name, family, true
}

// HomeProblem は承認済みチケットが識別子と違う名前のワークツリーに在るときの理由。無ければ空。
func HomeProblem(root string, conf *config.Settings, t *ticket.Ticket) string {
	name, family, ok := misplacedTree(root, conf, t)
	if !ok {
		return ""
	}
	return MisplacedHomeText(root, family, name)
}

// PlaceProblems は提案を識別子と違う名前のワークツリーに書いていれば承認しない。承認済みチケットは提案を
// 書いたツリーに置かれ、別名のままだと親が取り込み済みにならず ready で止まる。承認のあとには取り返せない。
func PlaceProblems(root string, conf *config.Settings, t *ticket.Ticket) []rules.Problem {
	name, family, ok := misplacedTree(root, conf, t)
	if !ok {
		return nil
	}
	what := "提案 " + t.ID
	if t.IsChild() {
		what = "子の提案 " + t.ID
	}
	return []rules.Problem{problem(t, what+" を識別子と違う名前のワークツリー "+name+" に書いている。"+
		"親のワークツリーは識別子と同じ名前（"+family+"）にする。承認済みチケットは提案を書いた"+
		"ツリーに置かれるので、別名のままだと親が取り込み済みにならず、承認済みチケットの自動"+
		"コミットが動かず、`ready` で止まる。承認のあとには取り返せないので、承認の前に直す。\n"+
		texts.Say(root, "NEXT_APPROVAL_PLACE", texts.Values{"family": family}, nil))}
}

// reservedProject は project: がレイヤーの名前に予約してある表記なら error。
func reservedProject(t *ticket.Ticket) []rules.Problem {
	if t.Project == "" || !config.IsReservedLayerName(t.Project) {
		return nil
	}
	names := make([]string, 0, len(config.ReservedLayerNames()))
	for _, n := range config.ReservedLayerNames() {
		names = append(names, "`"+n+"`")
	}
	return []rules.Problem{problem(t, "`project: "+t.Project+"` はレイヤーの名前として予約してある表記（"+strings.Join(names, " と ")+"）。"+
		"その名前のプロジェクトはレイヤーとして数えないので、このチケットのレイヤーが決まらない。"+
		"ワークスペース自身の提案は `wip/proposals/` に置いてください。プロジェクトの提案なら、"+
		"そのプロジェクトの名前を変えてから置いてください")}
}

func projectLabel(project string) string {
	if project == "" {
		return "ワークスペース"
	}
	return project
}

// ProjectProblems は、project が置き場と合っているかを返す。
// プロジェクトを決めるのは、提案を置いた場所で、frontmatter の project: は照合に使う。
// 予約名（common・self）は指せない。レイヤーとして数えない名前なので、指せると「レイヤーが決まらないチケット」を承認することになる。
func ProjectProblems(t *ticket.Ticket, pool map[string]*ticket.Ticket, conf *config.Settings) []rules.Problem {
	if t.DeclaredProject != "" && t.DeclaredProject != t.Project {
		return []rules.Problem{problem(t, "`project: "+t.DeclaredProject+"` が置き場"+
			"（"+projectLabel(t.Project)+"）と違う。"+
			t.DeclaredProject+" の提案はワークスペースの "+conf.Tickets+"/ に置いてください")}
	}
	if reserved := reservedProject(t); reserved != nil {
		return reserved
	}
	if t.IsChild() {
		parent := pool[t.Parent]
		if parent == nil || t.Project == parent.Project {
			return nil
		}
		return []rules.Problem{problem(t, "置き場（"+projectLabel(t.Project)+"）が親 "+parent.ID+" の"+
			"置き場（"+projectLabel(parent.Project)+"）と違う。"+
			"子は親と同じ置き場に置いてください")}
	}
	if t.Project == "" {
		return nil
	}
	// 予約名は known から外す。置き場に projects/self/ が在っても、それはレイヤーではない。
	for _, p := range tree.Projects(conf.Projects) {
		if p.Name == t.Project && !config.IsReservedLayerName(p.Name) {
			return nil
		}
	}
	where := conf.Projects
	if where == "" {
		where = "(無し)"
	}
	return []rules.Problem{problem(t, "`project: "+t.Project+"` は置き場 "+where+" にありません。")}
}

// ChildProblems は、子と親の構造を検査する。
// どれも「子の範囲をどの親で切り詰めるか」が決まらない形なので、承認（validate）でも判定（blockingProblems）でも通さない。
// 検査を 1 か所に置くのは、置き場を動かして承認する進め方で判定の側だけが古くなり、承認を通ったチケットと通らないチケットで
// 答えが分かれるのを防ぐため。
// 親の計画か固定したフェーズ定義が壊れていれば、子も止める。子の番号の先行・レビュー・範囲の上限が決まらないため。
// 定義は親のコピーから読み、phases.yml は読まない。
//
// missing は、親が無いときの文（空なら「承認されていない」）。検査は承認でも判定でも同じで、読む人の次の手だけが違う。
func ChildProblems(t, parent *ticket.Ticket, missing string) []rules.Problem {
	if mark := t.MaintenanceProblem(); mark != "" {
		return []rules.Problem{problem(t, mark)}
	}
	if parent == nil {
		if missing == "" {
			missing = "親 " + t.Parent + " が承認されていない"
		}
		return []rules.Problem{problem(t, missing)}
	}
	if parent.IsChild() {
		return []rules.Problem{problem(t, "親 "+t.Parent+" 自身が子。深さは 2 階層まで")}
	}
	if parent.HasPlan() && !t.IsConsolidate() && parent.ItemAt(t.Phase) == nil {
		return []rules.Problem{problem(t, strconv.Itoa(t.Phase)+" 番目のフェーズは親 "+parent.ID+" の計画にありません"+
			"（計画は "+strconv.Itoa(len(parent.Numbered()))+" 番目まで）")}
	}
	if broken := phases.Errors(parent); len(broken) > 0 {
		return []rules.Problem{problem(t, "親 "+parent.ID+" の計画が不正なので、子も止める（"+broken[0].Detail+"）。"+
			"ユーザが親の承認済みチケットの計画か、固定した定義（phases:）を直してください")}
	}
	return nil
}

// blockingProblems は、判定がこの承認済みチケットを信頼できない理由を返す（error だけ）。空なら信頼してよい。
// 承認のときにしか当たらなかった検査のうち、当たらないと「範囲をどこで切り詰めるか」が決まらないものを置く。
// 中身だけで分かる矛盾（started_at の無い base_sha、workflow: 欄、計画と固定した定義の誤り）も置く。
// 置き場を動かして承認する進め方は --agree を通らないので、判定の側でもここを通す。
//
// 入れないものは次のとおり。
//   - 親のコピーと今の phases.yml の食い違い（進行中の親には効かない決まり）
//   - フェーズの順序（held_phase が別に止める）
//   - 範囲の超過（判定が切り詰める）
//   - 再開で残った欄（ResumedFields。--lint が warn で言う）
func blockingProblems(conf *config.Settings, t *ticket.Ticket, pool map[string]*ticket.Ticket) []rules.Problem {
	problems := ProjectProblems(t, pool, conf)
	problems = append(problems, contentProblems(t)...)
	if !t.IsChild() {
		// 子の印の誤りは ChildProblems が言う。親に印がある形はここで止める。
		if mark := t.MaintenanceProblem(); mark != "" {
			problems = append(problems, problem(t, mark))
		}
	} else {
		missing := "親 " + t.Parent + " の承認済みチケットが作業中にありません（未承認か、閉じている）"
		problems = append(problems, ChildProblems(t, pool[t.Parent], missing)...)
	}
	var out []rules.Problem
	for _, p := range problems {
		if p.Severity == rules.Error {
			out = append(out, p)
		}
	}
	return out
}

// contentProblems は作業中の承認済みチケットの、中身だけで分かる欄の矛盾。
func contentProblems(t *ticket.Ticket) []rules.Problem {
	var out []rules.Problem
	if t.BaseSHA != "" && t.StartedAt == "" {
		out = append(out, problem(t, "`started_at` が無いのに `base_sha` がある。2 つは着手（`start`）が一緒に書く欄で、"+
			"片方だけの状態は `start` を通して書かれたものではない。"+
			"ユーザが承認済みチケットを確かめて直してください"))
	}
	if t.Raw.Has(ticket.WorkflowKey) {
		out = append(out, problem(t, "`"+ticket.WorkflowKey+":` の欄がある。"+
			"欄は読まない（先行関係は計画のステップの `after` から決まる）。"+
			"ユーザが欄を削除してください"))
	}
	return append(out, phases.Errors(t)...)
}

// ResumedFields は作業中（doing/）なのに値が残っている、閉じるときに書く欄の名前。道具を通る限り残らない
// （done/ から doing/ へ手で戻した再開の形）。判定では止めず、--lint が warn で言う。
func ResumedFields(t *ticket.Ticket) []string {
	var out []string
	for _, f := range []struct{ name, value string }{
		{"completed_at", t.CompletedAt},
		{"merged_sha", t.MergedSHA},
		{"cancelled_at", t.CancelledAt},
		{"cancel_reason", t.CancelReason},
		{"finished_to", t.FinishedTo},
		{"settled_by", t.SettledBy},
	} {
		if f.value != "" {
			out = append(out, f.name)
		}
	}
	return out
}

// markBlocked は、判定が読む承認済みチケットに、信頼できない理由（blockingProblems の最初）を付ける。
// 実行前・実行後・サブエージェント終了時の 3 か所が、この理由を同じ答えで読む。別々に検査すると、実行前は通って実行後に
// 範囲外と報告される食い違いが起きる。
// 親を引く対応表は kept そのものを使う。判定が parent を引く索引と同じにするため。閉じた親まで引ける対応表にすると、
// 判定の側では親が見つからず、子の宣言だけで範囲が決まってしまう。
func markBlocked(conf *config.Settings, kept []*ticket.Ticket) {
	pool := ByID(kept)
	for _, t := range kept {
		t.Blocked = ""
		if problems := blockingProblems(conf, t, pool); len(problems) > 0 {
			t.Blocked = problems[0].Detail
		}
	}
}
