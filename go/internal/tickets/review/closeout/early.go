package closeout

import (
	"bufio"
	"fmt"
	"io"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/lifecycle/ops"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/review/request"
)

// 早めに閉じたときの、残りを書き出す issue の下書きと、マージリクエストへ残すコメントの下書き（sh が拾う）。
const (
	earlyIssueFile = "review-close-early-issue-%s.md"
	earlyNoteFile  = "review-close-early-note-%s.md"
)

// 早めに閉じるときの文。ユーザのターミナルと sh が読むので変えない。
const (
	msgNoReason      = "ccnavi: user close-early には --reason <理由> が必要です。\n"
	msgNoStateDraft  = "ccnavi: state の置き場が空。下書きを置く場所がありません。\n"
	msgChanges       = "ccnavi: %s。ターミナルからも通せない。変更要求があるため、レビュー済みにはできません。修正する場合は同じフェーズに新しい子を提案し、ユーザの承認を受けてください\n"
	msgBusyChildren  = "ccnavi: 作業中の子がいる（%s）。子を閉じるか取り消してから、親を早めに閉じてください\n"
	msgPlanUnread    = "ccnavi: 受け入れの計画 %s を読めない。先に --accept-plan を打つ\n"
	msgPlanChanged   = "ccnavi: 計画のあとで、未解決の指摘が変わった。ホストには何も書いていない。打ち直してください\n"
	msgNotAccepted   = "ccnavi: 受け入れが済んでいない: %s。何も置いていない\n"
	msgDeclined      = "ccnavi: 早めに閉じなかった\n"
	msgEarlyOK       = "OK: %s を早めに閉じた。あとは親に、状態の移動をコミットし、'ticket finish %s' で閉じて push し、'%s' で Draft を解除させる（`%s/` の追跡済みのファイルは ready が削除します）\n"
	msgThreadMissing = "スレッド %s が結果に無い"
	msgThreadOpen    = "スレッド %s がホストで解決済みになっていない"
	msgOthersOpen    = "計画のほかに未解決の指摘が残っている（%s）"
	msgPlanBlocked   = "ccnavi: 受け入れを始められない: %s\n"
	msgPlanSaveFail  = "ccnavi: 受け入れの計画を %s に保存できませんでした（%s）\n"
	acceptWhy        = "ユーザが親を早めに閉じた"
	cancelPrefix     = "close-early: "
	japaneseComma    = "、"
)

// 残りを見せる文。
const (
	showHead       = "%s（%s）を早めに閉じる。残っているもの:\n"
	showTodo       = "  - 未着手の子 %s（%s）→ 取り消す\n"
	showUntouched  = "  - フェーズ %s: 手を付けていない → 残りとして issue に書き出す\n"
	showUnreviewed = "  - フェーズ %s: レビューが済んでいない → 済んだ扱い\n"
	showUnplanned  = "  - フィードバック計画: 未計画 → 対応なしの扱い\n"
	showUnresolved = "  - 未解決 %s → 受け入れる\n"
	showNothing    = "  （何も残っていない。ready で足りる）\n"
	showAsk        = "残りは別の issue に書き出す。早めに閉じてよいなら y、やめるならそれ以外: "
)

// 下書きの文。
const (
	issueTitle      = "%s の残り"
	issueOrigin     = "元のマージリクエスト: %s（チケット `%s`）"
	issueReason     = "早めに閉じた理由: %s"
	issueRestHead   = "## 残した作業"
	issueTodo       = "- %s: %s"
	issueUntouched  = "- フェーズ %s: 手を付けていない"
	issueUnplanned  = "- フィードバック計画は立てていない"
	issueNoRest     = "（残した作業はありません）"
	issueThreadHead = "## 引き継ぐ指摘"
	issueThread     = "- %s"
	issueNoThread   = "（未解決のスレッドは残っていない）"
	noteHead        = "ユーザが早めにクローズした（%s）: %s"
	noteCounts      = "取り消した子: %s / 手を付けなかったフェーズ: %s / 省略のマーカーを置いたフェーズ: %s / 受け入れた指摘: %d 件"
	noteTail        = "残りは別の issue に書き出す。親が片付けて ready を実行すれば Draft が外れる。マージはユーザが行う。"
	none            = "無し"
)

// yes は y/N で「はい」と読む答え（前後の空白を除いて小文字にしたもの）。
var yes = []string{"y", "yes"}

// EarlyOptions は CloseEarly の 3 回の打ち分け（sh が間で結果を取り直す）。どれも空なら残りを見せて y/N を聞き、
// y ならそのまま置く。
type EarlyOptions struct {
	// PlanOut（`--accept-plan`）は y/N のあとに受け入れの計画だけを書いて終わる。何も置かない。
	PlanOut string
	// PlanCheck（`--accept-check`）は聞かずに、取り直した結果の未解決が計画と同じかだけを確かめる。sh は
	// これが通ってから返信と解決を書く。PlanDone と一緒に渡すと計画は PlanDone から読む。
	PlanCheck string
	// PlanDone（`--accept-done`）は返信と解決のあとの結果で、計画のスレッドが全部解決済みでほかに未解決が
	// 無いことを確かめて、聞き直さずに置く。
	PlanDone string
	// NoIssue は残りを issue に書き出さないとき（返信の文で issue に回すと書かない）。
	NoIssue bool
}

// CloseEarly はユーザがターミナルで打つ「まだ残っているが、キリの良いところまでやった」。残り（未着手の子、
// 手を付けていないフェーズ、終わっていないレビュー、未計画のフィードバック、未解決のスレッド）を全部見せて
// y/N を聞き、y なら未着手の子を取り消し、レビューが済んでいないフェーズを済んだ扱いにし、手を付けたが
// 終わっていないフェーズに省略のマーカーを置き、親のマーカー `close-early.json` と issue・コメントの下書きを
// 置く。未解決を受け入れる返信と解決は sh がするので、置く前に済ませるよう 3 回に分けて打つ（EarlyOptions）。
// Draft を外すのは Ready だけ（外れた MR は必ず「片付いて push 済み」になる）。作業中の子がいる間は打てない。
func CloseEarly(stdin *bufio.Reader, stdout, stderr io.Writer, conf *config.Settings, root, cwd, reason, resultPath string, o EarlyOptions) (int, error) {
	reason = ospath.Strip(reason)
	if reason == "" {
		fmt.Fprint(stderr, msgNoReason)
		return config.ExitError, nil
	}
	parent := request.Parent(stderr, conf, root, cwd)
	if parent == nil {
		return config.ExitError, nil
	}
	if conf.State == "" {
		fmt.Fprint(stderr, msgNoStateDraft)
		return config.ExitError, nil
	}
	result, ok := request.ResultWithMR(stderr, resultPath)
	if !ok {
		return config.ExitError, nil
	}
	if len(request.ChangesRequested(result)) > 0 {
		fmt.Fprintf(stderr, msgChanges, host.ChangesRequestedNote(result.Host))
		return config.ExitError, nil
	}
	phases := phase.Phases(conf, root, parent.ID, nil)
	var busy []string
	for _, p := range phases {
		for _, t := range p.Tickets {
			if p.States[t.ID] == place.Doing && t.StartedAt != "" {
				busy = append(busy, t.ID)
			}
		}
	}
	if len(busy) > 0 {
		fmt.Fprintf(stderr, msgBusyChildren, strings.Join(busy, ", "))
		return config.ExitError, nil
	}
	left := leftoversOf(parent, phases, result, request.LedgerOf(conf, root, parent))
	if o.PlanDone != "" || o.PlanCheck != "" {
		planPath := o.PlanDone
		if planPath == "" {
			planPath = o.PlanCheck
		}
		planned := host.ReadAcceptPlan(planPath)
		if planned == nil {
			fmt.Fprintf(stderr, msgPlanUnread, planPath)
			return config.ExitError, nil
		}
		if o.PlanCheck != "" {
			if !slices.Equal(slices.Sorted(slices.Values(planned.Keys)), slices.Sorted(slices.Values(keysOf(left.unresolved)))) {
				fmt.Fprint(stderr, msgPlanChanged)
				return config.ExitError, nil
			}
			return config.ExitOK, nil
		}
		// 返信と解決のあとの、ホストの実状態で確かめる。
		if problem := doneProblem(left, result, planned); problem != "" {
			fmt.Fprintf(stderr, msgNotAccepted, problem)
			return config.ExitError, nil
		}
		byID := threadsByID(result.Threads)
		left.unresolved = nil
		for _, id := range planned.IDs {
			left.unresolved = append(left.unresolved, byID[id])
		}
	} else {
		showLeftovers(stdout, parent, left)
		if !slices.Contains(yes, strings.ToLower(ospath.Strip(fsio.ReadLine(stdin)))) {
			fmt.Fprint(stderr, msgDeclined)
			return config.ExitError, nil
		}
		if o.PlanOut != "" {
			return writeClosePlan(stderr, left, o.PlanOut, !o.NoIssue), nil
		}
	}
	stamp := fsio.Stamp()
	settled, ok, err := settle(stdout, stderr, conf, root, parent, phases, left, result.MR.Number, stamp, reason)
	if err != nil || !ok {
		return config.ExitError, err
	}
	accepted := keysOf(left.unresolved)
	home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if err != nil {
		return config.ExitError, err
	}
	mark := pyjson.NewObject("reason", reason, "at", stamp, "mr", result.MR.Number, "cancelled", settled.cancelled,
		"skipped", numbers(settled.skipped), "settled", numbers(settled.reviewed), "accepted", accepted)
	if !request.ParentMark(stderr, home, parent.ID, marks.CloseEarly, mark) {
		return config.ExitError, nil
	}
	if failed := earlyDrafts(conf, parent, result, reason, stamp, left, settled, accepted); failed != "" {
		fmt.Fprintf(stderr, msgFailed, failed)
		return config.ExitError, nil
	}
	// 下書きのパスは出さない。ここはユーザのターミナルに向いていて、sh は決まった名前で拾う。
	fmt.Fprintf(stdout, msgEarlyOK, parent.ID, parent.ID, ops.ReadyCommand(root, parent.ID), place.WipRoot)
	return config.ExitOK, nil
}

func keysOf(threads []host.Thread) []string {
	keys := make([]string, 0, len(threads))
	for _, t := range threads {
		keys = append(keys, host.ThreadKey(t))
	}
	return keys
}

// threadsByID は id の在るスレッドを id で引く表（同じ id は後のもの）。
func threadsByID(threads []host.Thread) map[string]host.Thread {
	out := map[string]host.Thread{}
	for _, t := range threads {
		if t.ID != "" {
			out[t.ID] = t
		}
	}
	return out
}

func numbers(list []int) []any {
	out := make([]any, 0, len(list))
	for _, n := range list {
		out = append(out, n)
	}
	return out
}

// doneProblem は返信と解決のあとの結果が計画と合わなければ、その説明。
func doneProblem(left *leftovers, result host.Result, planned *host.AcceptPlan) string {
	byID := threadsByID(result.Threads)
	for _, id := range planned.IDs {
		t, ok := byID[id]
		if !ok {
			return fmt.Sprintf(msgThreadMissing, id)
		}
		if !t.Resolved {
			return fmt.Sprintf(msgThreadOpen, host.ThreadKey(t))
		}
	}
	if len(left.unresolved) > 0 {
		return fmt.Sprintf(msgOthersOpen, strings.Join(keysOf(left.unresolved), japaneseComma))
	}
	return ""
}

// writeClosePlan は受け入れの計画を書く。未解決が無ければ空の計画（sh は何もしない）。
func writeClosePlan(stderr io.Writer, left *leftovers, outPath string, issue bool) int {
	if problem := host.AcceptPlanProblem(left.unresolved); problem != "" {
		fmt.Fprintf(stderr, msgPlanBlocked, problem)
		return config.ExitError
	}
	items := make([]host.AcceptItem, 0, len(left.unresolved))
	for _, t := range left.unresolved {
		items = append(items, host.AcceptItem{Thread: t, Reply: host.AcceptReply(acceptWhy, issue, "")})
	}
	if failed := host.WriteAcceptPlan(outPath, items, "", ""); failed != "" {
		fmt.Fprintf(stderr, msgPlanSaveFail, outPath, failed)
		return config.ExitError
	}
	return config.ExitOK
}

// leftovers は早めに閉じるときに残っているもの。見せるものと、閉じたあとに issue へ書き出すもの。
type leftovers struct {
	// todo は未着手の子（承認されたが着手していない）。取り消す。
	todo []*ticket.Ticket
	// notEnded は終わっていないフェーズ。手を付けたもの（取り消すと終わる）には省略のマーカーを置く。
	notEnded []*phase.Phase
	// untouched は notEnded のうち、レビュー待ちも閉じた子も無いもの。マーカーは置かず issue に書き出す。
	untouched []*phase.Phase
	// unreviewed は終わったがレビューが済んでいないフェーズ。済んだ扱いにする。
	unreviewed []*phase.Phase
	// unplanned はフィードバック計画がまだ無い（対応なしの扱いにする）。
	unplanned bool
	// unresolved は未解決のスレッド。受け入れる。
	unresolved []host.Thread
}

func (l *leftovers) nothing() bool {
	return len(l.todo) == 0 && len(l.notEnded) == 0 && len(l.unreviewed) == 0 && len(l.unresolved) == 0 && !l.unplanned
}

func leftoversOf(parent *ticket.Ticket, phases []*phase.Phase, result host.Result, ledger []*pyjson.Object) *leftovers {
	left := &leftovers{
		unplanned: parent.HasPlan() && !parent.FeedbackPlanned,
		// 未解決は依頼の投稿者を見ずに数える（ターミナルの判断で全部を受け入れるので、取りこぼさない側）。
		unresolved: host.Unresolved(result.Threads, "", "", ledger),
	}
	for _, p := range phases {
		for _, t := range p.Tickets {
			if p.States[t.ID] == place.Doing && t.StartedAt == "" {
				left.todo = append(left.todo, t)
			}
		}
		if p.Ended() && !p.ReviewedOrSkipped() {
			left.unreviewed = append(left.unreviewed, p)
		}
		if p.Ended() {
			continue
		}
		left.notEnded = append(left.notEnded, p)
		// 取り消し済みの子が混ざっていても、レビュー待ちか閉じた子が無ければ終わらない。打ち直しでもここに入る。
		touched := false
		for _, s := range p.States {
			if s == place.Review || s == place.Done {
				touched = true
			}
		}
		if !touched {
			left.untouched = append(left.untouched, p)
		}
	}
	return left
}

// showLeftovers は残っているものと、早めに閉じたら何が起きるかを見せて y/N を促す。
func showLeftovers(stdout io.Writer, parent *ticket.Ticket, left *leftovers) {
	fmt.Fprintf(stdout, showHead, parent.ID, parent.Title)
	for _, t := range left.todo {
		fmt.Fprintf(stdout, showTodo, t.ID, t.Title)
	}
	for _, p := range left.untouched {
		fmt.Fprintf(stdout, showUntouched, p.Label())
	}
	for _, p := range left.unreviewed {
		fmt.Fprintf(stdout, showUnreviewed, p.Label())
	}
	if left.unplanned {
		fmt.Fprint(stdout, showUnplanned)
	}
	for _, t := range left.unresolved {
		fmt.Fprintf(stdout, showUnresolved, request.ThreadLabel(t))
	}
	if left.nothing() {
		fmt.Fprint(stdout, showNothing)
	}
	fmt.Fprint(stdout, showAsk)
}

// settled は settle が置いたもの。skipped は省略のマーカーを置いた番号（手を付けていないフェーズは含まない）、
// reviewed は済んだ扱いにした番号。
type settled struct {
	cancelled []string
	skipped   []int
	reviewed  []int
}

// settle は未着手の子を取り消し、フェーズにマーカーを置く。途中で止まったら ok が偽（そこまでの変更は戻さない。
// マーカーは次に打てば重ねられる）。
func settle(stdout, stderr io.Writer, conf *config.Settings, root string, parent *ticket.Ticket, phases []*phase.Phase,
	left *leftovers, mr int, stamp, reason string) (settled, bool, error) {
	var out settled
	for _, t := range left.todo {
		if ops.Cancel(stdout, stderr, conf, root, t.ID, cancelPrefix+reason, false) != config.ExitOK {
			return out, false, nil
		}
		out.cancelled = append(out.cancelled, t.ID)
	}
	// 手を付けていないフェーズは子を取り消しても終わらず、ゲートも告知も動かないのでマーカーを置かない（親を
	// 閉じる判定は close-early.json が迂回する）。閉じた子の居るフェーズは取り消すと終わるので、マーカーが無いと
	// 次の hook が「レビューを頼め」と言い出す。省略のマーカーで抑える。
	untouched := map[int]bool{}
	for _, p := range left.untouched {
		untouched[p.Number] = true
	}
	pending := map[int]bool{}
	for _, p := range left.notEnded {
		if !untouched[p.Number] {
			pending[p.Number] = true
		}
	}
	for _, p := range phases {
		switch {
		case pending[p.Number]:
			if _, ok := p.Marks[marks.Skipped]; ok {
				continue
			}
			home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
			if err != nil {
				return out, false, err
			}
			if !request.Mark(stderr, home, parent.ID, p.Number, marks.Skipped, pyjson.NewObject("by", request.ByCloseEarly, "at", stamp)) {
				return out, false, nil
			}
			out.skipped = append(out.skipped, p.Number)
		case slices.Contains(left.unreviewed, p):
			if ok, err := request.SettleChildren(stdout, stderr, conf, root, parent, p, request.ByCloseEarly); err != nil || !ok {
				return out, false, err
			}
			home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
			if err != nil {
				return out, false, err
			}
			data := pyjson.NewObject("by", request.ByCloseEarly, "at", stamp, "mr", mr, "accepted", []any{})
			if !request.Mark(stderr, home, parent.ID, p.Number, marks.Reviewed, data) {
				return out, false, nil
			}
			out.reviewed = append(out.reviewed, p.Number)
		}
	}
	return out, true, nil
}

// earlyDrafts は残りを書き出す issue の下書き（1 行目がタイトル、空行のあとが本文）と、マージリクエストへ残す
// コメントの下書きを書く。書けなければ理由。
func earlyDrafts(conf *config.Settings, parent *ticket.Ticket, result host.Result, reason, stamp string, left *leftovers,
	done settled, accepted []string) string {
	issue := []string{fmt.Sprintf(issueTitle, parent.Title), "", fmt.Sprintf(issueOrigin, result.MR.URL, parent.ID),
		fmt.Sprintf(issueReason, reason), "", issueRestHead, ""}
	var rest []string
	for _, t := range left.todo {
		rest = append(rest, fmt.Sprintf(issueTodo, t.ID, t.Title))
	}
	var labels []string
	for _, p := range left.untouched {
		rest = append(rest, fmt.Sprintf(issueUntouched, p.Label()))
		labels = append(labels, p.Label())
	}
	if left.unplanned {
		rest = append(rest, issueUnplanned)
	}
	if len(rest) == 0 {
		rest = []string{issueNoRest}
	}
	issue = append(issue, rest...)
	issue = append(issue, "", issueThreadHead, "")
	if len(left.unresolved) == 0 {
		issue = append(issue, issueNoThread)
	}
	for _, t := range left.unresolved {
		issue = append(issue, fmt.Sprintf(issueThread, request.ThreadLabel(t)))
	}
	issue = append(issue, "")
	if failed := request.WriteDraft(ospath.Join(conf.State, fmt.Sprintf(earlyIssueFile, parent.ID)), strings.Join(issue, "\n")); failed != "" {
		return failed
	}
	skipped := make([]string, 0, len(done.skipped))
	for _, n := range done.skipped {
		skipped = append(skipped, strconv.Itoa(n))
	}
	note := []string{host.MarkerCloseEarly, fmt.Sprintf(noteHead, stamp, reason),
		fmt.Sprintf(noteCounts, orNone(done.cancelled), orNone(labels), orNone(skipped), len(accepted)), noteTail, ""}
	return request.WriteDraft(ospath.Join(conf.State, fmt.Sprintf(earlyNoteFile, parent.ID)), strings.Join(note, "\n"))
}

// orNone は Python の `', '.join(x) or '無し'`。
func orNone(list []string) string {
	if joined := strings.Join(list, ", "); joined != "" {
		return joined
	}
	return none
}
