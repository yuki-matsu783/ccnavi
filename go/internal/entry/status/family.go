package status

import (
	"cmp"
	"fmt"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/lifecycle/stop"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// parentOnly は状態を動かすコマンドの行に添える（サブエージェントも status を打てるため）。
const parentOnly = "（メインエージェントのみ実行可能）"

const workspaceLabel = "ワークスペースルート"

var stateLabels = map[place.State]string{
	place.Todo:      "承認待ち（todo/）",
	place.Doing:     "作業中（doing/）",
	place.Review:    "レビュー待ち（review/）",
	place.Done:      "クローズ済み（done/）",
	place.Cancelled: "クローズ済み（done/）",
}

// familyView は 1 つの親子のチケットの中で、チケットごとの行を組む。name と wap* は親子ごとに入れ替える。
type familyView struct {
	root    string
	conf    *config.Settings
	entries map[string]*entry
	pool    approval.Pool
	times   map[string]approval.ApprovedTime
	files   map[string]fileState
	fams    *standing.Families
	// raw は Run の頭で読んだ置き場。status は置き場を動かさないので、子ごとに読み直さずに渡す。
	raw      *approval.Raw
	ticketSh string
	pushSh   string

	name      string
	wapDone   bool
	wapTarget wap.Target
	wapWhy    string
}

// writeAndPush はこの親子が write-and-push の対象か（`ccnavi write-and-push family` と同じ）と理由。1 度だけ引く。
// writeAndPush は親子のチケットが write-and-push の対象かと、その理由。git を呼ぶので、親ごとに 1 回だけ引く。
func (f *familyView) writeAndPush() (wap.Target, string) {
	if !f.wapDone {
		f.wapTarget, f.wapWhy, _ = wap.TargetOf(f.conf, f.root, f.name)
		f.wapDone = true
	}
	return f.wapTarget, f.wapWhy
}

// treeLabel はチケットのあるツリーの名前。ワークスペースルートなら workspaceLabel。
func treeLabel(t *ticket.Ticket) string { return cmp.Or(t.Tree, workspaceLabel) }

// describe はチケット 1 件の状態の行を組む。
//
// 処理フロー:
//  1. 複数の場所にあるものは、正しい 1 件を特定できないので、状態を変えられないと言って終える
//  2. 置き場から決まる行（承認待ち・閉じた）を先に出して終える
//  3. 着手の状態を出す。レビュー待ちは依頼の前後で次の操作が違う。作業中は doing が止まる理由と次の一手を決める
func (f *familyView) describe(e *entry, revision bool) []string {
	t := e.t()
	role := "親"
	if t.IsChild() {
		role = fmt.Sprintf("子（フェーズ %d）", t.Phase)
	}
	headLine := ospath.Rstrip(fmt.Sprintf("- %s  %s  %s", e.ident, role, t.Title))
	if len(e.hits) > 1 {
		var where []string
		for _, h := range e.hits {
			where = append(where, treeLabel(h)+":"+string(h.State))
		}
		return []string{
			headLine,
			"    置き場: 複数の場所にある（" + strings.Join(where, ", ") + "）",
			"    止まっている理由: 正しいチケットを特定できないため、状態を変更できません。",
			"    次の操作: ユーザにチケットの重複を解消してもらってください。" +
				"進行した側の変更をマージするか、不要なワークツリーを整理します。",
		}
	}
	label, ok := stateLabels[t.State]
	if !ok {
		label = string(t.State)
	}
	placed := "    置き場: " + label + "  ツリー: " + treeLabel(t)
	if t.Project != "" {
		placed += "  プロジェクト: " + t.Project
	}
	lines := []string{headLine, placed}
	file := f.files[t.Path]
	if t.State == place.Todo {
		return append(lines, "    置き場のファイル: "+file.label(),
			"    次の操作: ユーザの承認を待つ（VS Code のボード、Chrome のボード、ターミナルの "+
				"ccnavi-user-agree.sh、GitLab や GitHub の画面などで doing/ へ動かす、のどれか）")
	}
	when := "不明"
	if at, ok := f.times[t.Path]; ok {
		when = at.Label()
	}
	lines = append(lines, "    承認: "+when)
	if revision {
		lines = append(lines, "    改版: 同じ識別子の提案が todo/ にある（承認待ちの改版）")
	}
	if closedState(t.State) {
		if t.CancelledAt != "" {
			why := ""
			if t.CancelReason != "" {
				why = "。理由: " + t.CancelReason
			}
			lines = append(lines, "    閉じ方: 取り消し（"+t.CancelledAt+why+"）")
		} else {
			// 合流（finish が親のブランチへ取り込んだ子の先頭）。子だけが持つ。
			merged := ""
			if t.MergedSHA != "" {
				merged = "、合流 " + pyfmt.Head(t.MergedSHA, git.ShortSHA)
			}
			lines = append(lines, "    閉じ方: 完了（"+cmp.Or(t.CompletedAt, "時刻なし")+merged+"）")
		}
		return append(lines, "    置き場のファイル: "+file.label())
	}
	if t.StartedAt != "" {
		lines = append(lines, "    着手: 着手済み（"+t.StartedAt+"、基準点 "+cmp.Or(t.BaseSHA, "なし")+"）")
	} else {
		lines = append(lines, "    着手: 未着手")
	}
	lines = append(lines, "    置き場のファイル: "+file.label())
	// レビュー待ち。
	if t.State == place.Review {
		// 依頼の前は「レビュー準備中」で、動くのは親（合流・review-request）。依頼の後に初めてユーザを待つ。
		next := phase.ReviewNext(f.conf, f.root, t, f.raw)
		if next != "" && strings.Contains(next, "review-request --phase") {
			next += parentOnly
		}
		// 子のフェーズが読めないと、依頼済みかが分からない。分からないまま「待つ」と言うと、依頼の前でもユーザに
		// 回ったと読める（依頼の打ち忘れ）。
		if next == "" {
			next = "フェーズを読めないので、レビュー依頼済みかどうかを確認できません。ユーザに回るのは、" +
				"親が合流を済ませて ccnavi-review.sh review-request で依頼した後" +
				"（依頼済みなら、ユーザが confirm / decide か Chrome 拡張のレビュー済みで done/ へ動かすのを待つ）" +
				parentOnly
		}
		return append(lines, "    次の操作: "+next)
	}
	return append(lines, f.doing(t, file)...)
}

// doing は作業中のチケットの、止まっている理由・注意・次の一手。
// 止まっている理由（stops）は次の操作を妨げるもの、注意（warns）は妨げないもの。stops があれば、次の操作は原因の解消だけにする。
func (f *familyView) doing(t *ticket.Ticket, file fileState) []string {
	// 1. 止まっている理由を集める。親子のチケットの立場、チケット自身の記録、write-and-push の対象かどうか。
	var stops, warns []string
	st := approval.FamilyStanding(f.conf, f.root, t, f.fams)
	if st.Stop != "" {
		stops = append(stops, st.Stop+"（この親子のチケットの状態は動かさない）")
		stops = append(stops, standing.Guidance(f.root, st)...)
	}
	if t.Blocked != "" {
		stops = append(stops, t.Blocked)
	}
	uncommitted := file.kind == fileUncommitted
	verdict, why := f.writeAndPush()
	if verdict == wap.Stop && st.Stop == "" {
		// write-and-push の sh（ccnavi-ticket.sh）が状態の操作を断る。
		stops = append(stops, "write-and-push では状態の操作を実行できません: "+cmp.Or(why, "理由が分からない"))
	}
	// 未着手なら、着手できない理由（先行・親が未着手・承認済みチケットが未コミット）も加える。
	if t.StartedAt == "" {
		for _, p := range approval.UnmetPredecessors(t, f.pool) {
			stops = append(stops, "先行チケット "+p.Ticket+" が開始条件を満たしていません（"+p.Label()+"）")
		}
		if parentWhy := f.parentNotStarted(t); parentWhy != "" {
			stops = append(stops, parentWhy)
		}
		if uncommitted && verdict == wap.Yes {
			stops = append(stops, "取り込み済みの親子（write-and-push の対象）で、承認済みチケットが親のブランチに"+
				"未コミット。ユーザがコミットして push するまで start は実行できません")
		}
	}
	// 基準点が HEAD の祖先でないのは warn。start は止めない（基準点を HEAD で書き直す）。
	if off := stop.BaseOffHead(f.conf, f.root, t); off != "" {
		warns = append(warns, off)
	}
	if left := approval.ResumedFields(t); len(left) > 0 {
		warns = append(warns, "作業中なのに "+strings.Join(left, ", ")+" に値が残っている（done/ から手で戻した再開）。"+
			"ユーザにその欄を空にしてもらう")
	}
	if left := phase.ResumedReview(f.conf, f.root, t, f.raw); left != "" {
		warns = append(warns, left)
	}
	// 2. 理由と注意を行にして、次の操作を決める。
	var lines []string
	for _, s := range stops {
		lines = append(lines, "    止まっている理由: "+s)
	}
	for _, w := range warns {
		lines = append(lines, "    注意: "+w)
	}
	var nexts []string
	if uncommitted {
		nexts = append(nexts, "承認済みチケットが未コミット。ユーザに '"+f.pushSh+" "+f.name+"' を"+
			"実行してもらう（エージェントはコミットも push もしない）")
	}
	switch {
	case len(stops) > 0:
		nexts = append(nexts, "止まっている原因を解消する（ユーザに確かめる）")
	case t.StartedAt == "":
		if uncommitted && verdict == wap.No {
			nexts = append(nexts, "コミットと push を待たずに start へ進んでよい（この親子は write-and-push の対象ではない）")
		}
		nexts = append(nexts, f.startLines(t)...)
	case t.IsChild():
		nexts = append(nexts, "作業を進め、終えたら '"+f.ticketSh+" finish "+t.ID+"' を実行する"+parentOnly)
	default:
		nexts = append(nexts, "子を進める。全部のフェーズを閉じたら '"+f.ticketSh+" finish "+t.ID+"' を実行する"+parentOnly)
	}
	for _, n := range nexts {
		lines = append(lines, "    次の操作: "+n)
	}
	return lines
}

// startLines は未着手のチケットの着手の手順。ワークツリーが無ければ、先に作る手順を付ける。
func (f *familyView) startLines(t *ticket.Ticket) []string {
	owner := cmp.Or(tree.ProjectRoot(f.conf.Projects, t.Project), f.root)
	worktree := tree.WorktreePath(f.root, t.ID)
	var out []string
	if !tree.IsWorktreeOf(owner, worktree) {
		if t.IsChild() {
			where := ""
			if t.Project != "" {
				where = "projects/" + t.Project + " の中で "
			}
			out = append(out, "ワークツリーがありません。"+where+"'"+config.ScriptCommand(f.root, config.GitWrapper)+
				` worktree add "`+worktree+`" -b `+t.ID+" "+t.Parent+"' で作る"+parentOnly)
		} else {
			// 親のワークツリーは親のブランチ（MR の元ブランチ）を直接チェックアウトする。識別子の名前のブランチは作らない。
			out = append(out, "親のワークツリーがありません。'"+config.ScriptCommand(f.root, config.StartWrapper)+
				" --mr <MR の番号>'（MR が無ければ '--issue <issue の番号>'）で、親のブランチを直接チェックアウトして作る"+parentOnly)
		}
	}
	return append(out, "'"+f.ticketSh+" start "+t.ID+"' で開始する"+parentOnly)
}

// parentNotStarted は子が着手できない原因になる、親の状態の文。問題が無い（または親そのもの）なら空。
func (f *familyView) parentNotStarted(t *ticket.Ticket) string {
	if !t.IsChild() {
		return ""
	}
	e, ok := f.entries[t.Parent]
	switch {
	case !ok:
		return "親 " + t.Parent + " が見つからない"
	case len(e.hits) > 1:
		return "親 " + t.Parent + " が複数の場所にある"
	}
	parent := e.t()
	switch {
	case parent.State == place.Todo:
		return "親 " + t.Parent + " がまだ承認されていない（todo/）"
	case parent.State != place.Doing:
		return "親 " + t.Parent + " は作業中ではない（" + string(parent.State) + "/）"
	case parent.StartedAt == "":
		return "親チケット " + t.Parent + " が未着手です。子チケットより先に、親チケットを開始してください。"
	}
	return ""
}
