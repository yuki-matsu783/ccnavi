package request

import (
	"fmt"
	"io"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/phase"
)

// レビューの状態を読むときの文。sh とボードが読むので変えない。
const (
	msgNotParentTree = "ccnavi: ここは親チケットのワークツリーではない（cwd から親を引けない）\n"
	msgNoPhase       = "ccnavi: %s にフェーズ %d の子がありません。\n"
	msgNoMR          = "ccnavi: 結果にマージリクエストがありません。"
	msgNoResultPath  = "ccnavi: --result <json> を指定してください。リモートから取得した結果は sh が渡します。\n"
	msgUnreadRemote  = "ccnavi: リモートを読めていない: %s\n"
	msgNoMarkTarget  = "ccnavi: 依頼の記録にホストかマージリクエストの番号がありません。依頼し直してください"
	msgOtherHost     = "ccnavi: 依頼したホスト（%s）と結果のホスト（%s）が違う"
	msgOtherMR       = "ccnavi: 依頼したマージリクエスト（%s）と結果のもの（%d）が違う"
	msgMarkFailed    = "ccnavi: マーカーを置けない: %s\n"
	msgUnresolved    = "ccnavi: 未解決のスレッドが %d 件残っている"
	msgMovedToDone   = "レビュー待ちの子を %s/%s/ へ動かした: %s"
	msgLedgerFailed  = "受け入れ台帳を書けない: "
	msgReviewedOK    = "OK: フェーズ %d はレビュー済み。先へ進める"
	msgDraftFailed   = "下書きを書き出せない (%s: %s)"
	msgRedo          = "push してから '%s review-request --phase %d --body-file <依頼文>' で依頼を出し直してください"
	firstLineChars   = 120
)

// lastFeedbackLines はフィードバック対応の最後のレビューで指摘が残ったときの行。新しいフィードバック作業
// フェーズは足せないので、同じフェーズでやり直すか、別の issue に回すかの 2 つだけを言う。
const (
	lastFeedbackHead = "フィードバック対応の最後のレビューです。選べるのは 2 つ。"
	lastFeedbackRedo = "  - 同じフェーズ %d に子を追加して承認を受け、やり直す（差し戻し）"
	lastFeedbackUser = "  - ユーザが '%s decide %d'（ボードの「決める」）で残りを受け入れるか、別の issue に回す"
	lastFeedbackTail = "新しいフィードバック作業フェーズは足せません。"
)

// 子を done/ へ動かした経路（子の settled_by に書く）。
const (
	byConfirm    = "confirm"
	byChrome     = "chrome"
	ByDecide     = "decide"
	ByCloseEarly = "close-early"
)

// Parent は cwd の親。引けない・親子のチケットが決まらない（FamilyStopped）なら言って nil。
// 取り込み済みの親子が決まらないなら、依頼・確認・行き先・早めに閉じたことのマーカーも置かない。
func Parent(stderr io.Writer, conf *config.Settings, root, cwd string) *ticket.Ticket {
	parent, _ := phase.ParentAt(conf, root, cwd, nil)
	if parent == nil {
		fmt.Fprint(stderr, msgNotParentTree)
		return nil
	}
	if closing.FamilyStopped(stderr, conf, root, parent) {
		return nil
	}
	return parent
}

// ParentAny は Parent と同じで、閉じた承認済みチケットも引く（親を閉じたあとに Draft を外す ready のため）。
func ParentAny(stderr io.Writer, conf *config.Settings, root, cwd string) *ticket.Ticket {
	if parent, _ := phase.ParentAt(conf, root, cwd, nil); parent != nil {
		if closing.FamilyStopped(stderr, conf, root, parent) {
			return nil
		}
		return parent
	}
	if cwd == "" {
		cwd, _ = ospath.Getwd()
	}
	if t, ok := tree.TreeOf(root, cwd, conf.Projects); ok && !t.IsMain() {
		closed, _ := approval.Scan(conf, root, true, nil)
		if found, ok := tree.Lookup(approval.ByID(closed), t.Name); ok && !found.IsChild() {
			if closing.FamilyStopped(stderr, conf, root, found) {
				return nil
			}
			return found
		}
	}
	fmt.Fprint(stderr, msgNotParentTree)
	return nil
}

// PhaseOf はこの親の、この番号のフェーズ。無ければ nil。
func PhaseOf(conf *config.Settings, root string, parent *ticket.Ticket, number int) *phase.Phase {
	for _, p := range phase.Phases(conf, root, parent.ID, nil) {
		if p.Number == number {
			return p
		}
	}
	return nil
}

// ParentPhase は cwd の親と、その番号のフェーズ。どちらか無ければ言って ok が偽。
func ParentPhase(stderr io.Writer, conf *config.Settings, root, cwd string, number int) (*ticket.Ticket, *phase.Phase, bool) {
	parent := Parent(stderr, conf, root, cwd)
	if parent == nil {
		return nil, nil, false
	}
	p := PhaseOf(conf, root, parent, number)
	if p == nil {
		fmt.Fprintf(stderr, msgNoPhase, parent.ID, number)
		return nil, nil, false
	}
	return parent, p, true
}

// ReadResult は sh が取ってきた結果（`--result`）を読む。読めなければ理由を 1 行だけ言って ok が偽。
func ReadResult(stderr io.Writer, path string) (host.Result, bool) {
	if path == "" {
		fmt.Fprint(stderr, msgNoResultPath)
		return host.Result{}, false
	}
	result := host.LoadResult(path)
	if result.Error != "" {
		fmt.Fprintf(stderr, msgUnreadRemote, result.Error)
		return host.Result{}, false
	}
	return result, true
}

// ResultWithMR は ReadResult に加えて、マージリクエストが入っていることまで確かめる。
func ResultWithMR(stderr io.Writer, path string) (host.Result, bool) {
	result, ok := ReadResult(stderr, path)
	if !ok || result.MR == nil {
		fmt.Fprintln(stderr, msgNoMR)
		return host.Result{}, false
	}
	return result, true
}

// Matching は依頼したのと同じマージリクエストを取得した結果か。違うもので先へ進めない。
func Matching(stderr io.Writer, path string, requested *pyjson.Object) (host.Result, bool) {
	result, ok := ReadResult(stderr, path)
	if !ok {
		return host.Result{}, false
	}
	problems := MatchingProblems(&result, requested)
	for _, line := range problems {
		fmt.Fprintln(stderr, line)
	}
	return result, len(problems) == 0
}

// MatchingProblems は依頼したのと同じマージリクエストを取得した結果か。違えばその説明（標準エラーの行）。
// 照合できない依頼の記録（ホストか番号が無い）で通すと、別の MR のスレッドでレビュー済みにできる。
func MatchingProblems(result *host.Result, requested *pyjson.Object) []string {
	if result == nil || result.MR == nil {
		return []string{msgNoMR}
	}
	if !pyfmt.Truthy(requested.Value("host")) || !pyfmt.Truthy(requested.Value("mr")) {
		return []string{msgNoMarkTarget}
	}
	if markHost, ok := requested.Value("host").(string); !ok || markHost != string(result.Host) {
		return []string{fmt.Sprintf(msgOtherHost, pyfmt.Str(requested.Value("host")), result.Host)}
	}
	if mr := pyfmt.Str(requested.Value("mr")); mr != strconv.Itoa(result.MR.Number) {
		return []string{fmt.Sprintf(msgOtherMR, mr, result.MR.Number)}
	}
	return nil
}

// ChangesRequested は有効な変更要求のレビュー（レビュアーごとの最新）。
func ChangesRequested(result host.Result) []host.Review {
	var out []host.Review
	for _, r := range host.Effective(result.Reviews) {
		if pyfmt.Upper(r.State) == host.ChangesRequested {
			out = append(out, r)
		}
	}
	return out
}

// UnresolvedFor は数える未解決の指摘（機構の投稿と、受け入れ台帳が受け入れ済みとするものを除く）。
func UnresolvedFor(conf *config.Settings, root string, parent *ticket.Ticket, number int, result host.Result) []host.Thread {
	return host.Unresolved(result.Threads, result.Host, poster(conf, root, parent, number), LedgerOf(conf, root, parent))
}

// ReviewProblems はレビュー済みにできない理由（変更要求のレビュー、未解決のスレッド）。標準エラーの行。
func ReviewProblems(conf *config.Settings, root string, parent *ticket.Ticket, number int, result host.Result) []string {
	if changes := ChangesRequested(result); len(changes) > 0 {
		lines := []string{texts.Say(root, "NOTICE_REVIEW_CHANGES_REQUESTED",
			texts.Values{"note": host.ChangesRequestedNote(result.Host)}, nil)}
		for _, r := range changes {
			lines = append(lines, "  - "+r.URL)
		}
		return lines
	}
	unresolved := UnresolvedFor(conf, root, parent, number, result)
	if len(unresolved) == 0 {
		return nil
	}
	lines := []string{fmt.Sprintf(msgUnresolved, len(unresolved))}
	for _, t := range unresolved {
		lines = append(lines, "  - "+ThreadLabel(t))
	}
	if lastFeedbackReview(parent, number) {
		return append(lines, lastFeedbackHead, fmt.Sprintf(lastFeedbackRedo, number),
			fmt.Sprintf(lastFeedbackUser, config.ScriptCommand(root, config.ReviewWrapper), number), lastFeedbackTail)
	}
	return append(lines, texts.Say(root, "NOTICE_REVIEW_UNRESOLVED__DECIDE",
		texts.Values{"phase": strconv.Itoa(number)}, nil))
}

// lastFeedbackReview はフィードバック作業フェーズの最後のレビューか。
func lastFeedbackReview(parent *ticket.Ticket, number int) bool {
	if !parent.HasPlan() || len(parent.Feedback) == 0 {
		return false
	}
	at, ok := parent.ReviewAt(len(parent.Plan) + len(parent.Feedback))
	return ok && at == number
}

// LedgerOf は親の受け入れ台帳の項目。読めなければ空（読めない台帳で未解決を隠さない）。
func LedgerOf(conf *config.Settings, root string, parent *ticket.Ticket) []*pyjson.Object {
	home := approval.HomeDirRead(conf, root, parent.ID, "", parent.Project)
	entries, problem := marks.ReadLedger(home, parent.ID)
	if problem != "" {
		return nil
	}
	return entries
}

// poster はそのフェーズの依頼を投稿したアカウント（依頼の記録の poster）。無ければ空。
func poster(conf *config.Settings, root string, parent *ticket.Ticket, number int) string {
	p := PhaseOf(conf, root, parent, number)
	if p == nil {
		return ""
	}
	return markText(p.Marks[marks.Requested], "poster")
}

// markText は記録の欄を Python の str(mark.get(key) or "") と同じに読む。
func markText(mark *pyjson.Object, key string) string {
	v := mark.Value(key)
	if !pyfmt.Truthy(v) {
		return ""
	}
	return pyfmt.Str(v)
}

// Mark はフェーズのマーカーを置く。置けなければ言って偽。
func Mark(stderr io.Writer, approvedDir, parentID string, number int, kind marks.Kind, data *pyjson.Object) bool {
	if failed := marks.WriteMark(approvedDir, parentID, number, kind, data); failed != "" {
		fmt.Fprintf(stderr, msgMarkFailed, failed)
		return false
	}
	return true
}

// ParentMark は親のマーカーを置く。置けなければ言って偽。
func ParentMark(stderr io.Writer, approvedDir, parentID string, name marks.Parent, data *pyjson.Object) bool {
	if failed := marks.WriteParent(approvedDir, parentID, name, data); failed != "" {
		fmt.Fprintf(stderr, msgMarkFailed, failed)
		return false
	}
	return true
}

// ReviewedMark は、レビュー済みのマーカーの中身を組む（欄の順は mr・accepted・actor・via・at）。actor と via は分かるときだけ書く。at は、stamp が空なら書かない（marks.WriteMark が今の時刻を入れる）。accepted は、受け入れた指摘のキーの記録である。確認はホストの状態だけを数える。受け入れた指摘は、ホストで解決済みにしてあるからである。
func ReviewedMark(mr int, accepted []string, account, route, stamp string) *pyjson.Object {
	mark := pyjson.NewObject("mr", mr, "accepted", append([]string{}, accepted...))
	if account != "" {
		mark.Set("actor", account)
	}
	if route != "" {
		mark.Set("via", route)
	}
	if stamp != "" {
		mark.Set("at", stamp)
	}
	return mark
}

// reviewNumbers はこのフェーズのレビューが含む番号（延期を引き受けた分と自分）。
func reviewNumbers(p *phase.Phase) []int {
	return append(slices.Clone(p.Covers()), p.Number)
}

// SettleChildren は、レビューが済んだフェーズ（延期を引き受けた分を含む）のレビュー待ちの子を done/ へ動かす。by は動かした経路（By* の定数）である。動かせなければ、理由を言って偽を返す。
//
// 呼ぶ側は、これをレビュー済みのマーカーより先に呼ぶ。マーカーを先に置くと、動かせなかったときに「レビュー済みなのに子が review/ に残る」形になる。confirm はレビュー済みを拒むので、取り出す操作が無くなってしまう。逆の順なら、子は動いたがマーカーだけ置けなかった場合でも、次の confirm が置き直す。
func SettleChildren(stdout, stderr io.Writer, conf *config.Settings, root string, parent *ticket.Ticket, p *phase.Phase, by string) (bool, error) {
	moved, failed, err := approval.SettleReview(conf, root, parent.ID, reviewNumbers(p), by)
	if err != nil {
		return false, err
	}
	if failed != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", failed)
		return false, nil
	}
	if len(moved) > 0 {
		fmt.Fprintln(stdout, MovedLine(conf, moved))
	}
	return true, nil
}

// MarkPrefix はマーカーを置けなかったときの理由の頭。
const MarkPrefix = "マーカーを置けない: "

// MovedLine は子を done/ へ動かしたときに見せる行。
func MovedLine(conf *config.Settings, moved []string) string {
	return fmt.Sprintf(msgMovedToDone, conf.Approved, place.Done, strings.Join(moved, ", "))
}

// settledByConfirm は confirm が子を動かした経路。Chrome のレビュー済みも同じコアを通るので、経路で見分ける。
func settledByConfirm() string {
	if via.Current() == via.Chrome {
		return byChrome
	}
	return byConfirm
}

// SettleAndMark は、レビュー済みで書くもの（子を done/ へ、受け入れ台帳、レビュー済みのマーカー）を溜める段に並べる。順番は SettleChildren、マーカーである。子を先に動かし、台帳はマーカーより先に書く。マーカーだけが置かれて、受け入れた指摘が数えられる形を作らないためである。止まったら、その理由を返す。書き手は `ccnavi: <理由>` を言って止まる。
func SettleAndMark(stage *fsio.Stage, conf *config.Settings, root string, parent *ticket.Ticket, p *phase.Phase, mark *pyjson.Object, ledger []*pyjson.Object) (string, error) {
	restore := fsio.SetPolicy(func(po *fsio.Policy) {
		po.OnFail, po.Ticket, po.Message, po.Prefix, po.Undo, po.Places = fsio.FailStop, "", "{reason}", "", nil, ""
	})
	defer restore()
	moved, failed, err := approval.SettleReview(conf, root, parent.ID, reviewNumbers(p), settledByConfirm())
	if err != nil {
		return "", err
	}
	if failed != "" {
		return failed, nil
	}
	if len(moved) > 0 {
		stage.AddLine(MovedLine(conf, moved), 0, fsio.StreamOut)
	}
	home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if err != nil {
		return "", err
	}
	if len(ledger) > 0 {
		if failed := marks.AddLedger(home, parent.ID, ledger); failed != "" {
			return msgLedgerFailed + failed, nil
		}
	}
	prefixed := fsio.SetPolicy(func(po *fsio.Policy) { po.Prefix = MarkPrefix })
	failed = marks.WriteMark(home, parent.ID, p.Number, marks.Reviewed, mark)
	prefixed()
	if failed != "" {
		return MarkPrefix + failed, nil
	}
	stage.AddLine(ReviewedLine(p.Number), 0, fsio.StreamOut)
	return "", nil
}

// ReviewedLine はレビュー済みのマーカーを置いたときに見せる行。
func ReviewedLine(number int) string { return fmt.Sprintf(msgReviewedOK, number) }

// ThreadLabel は一覧に出す 1 スレッドの表示（`<url> <ファイル>:<行> <最初の行>`）。位置の無いスレッド
// （PR 全体へのコメント）は位置を省き、行だけ無いときはファイル名だけを出す。
func ThreadLabel(t host.Thread) string {
	where := ""
	if t.Path != "" {
		where = t.Path
		if t.Line != 0 {
			where = fmt.Sprintf("%s:%d", t.Path, t.Line)
		}
	}
	first := ""
	if body := ospath.Strip(t.Body); body != "" {
		first = pyfmt.Head(ospath.Splitlines(body)[0], firstLineChars)
	}
	var parts []string
	for _, part := range []string{t.URL, where, first} {
		if part != "" {
			parts = append(parts, part)
		}
	}
	return strings.Join(parts, " ")
}

// RedoRequest は依頼の後に HEAD が動いたときの案内。push 済みの今の HEAD で依頼を出し直す。
func RedoRequest(root string, number int) string {
	return fmt.Sprintf(msgRedo, config.ScriptCommand(root, config.ReviewWrapper), number)
}

// WriteDraft は sh が投稿する下書きを書く。書けなければ理由。
func WriteDraft(path, text string) string {
	if failed := fsio.WriteText(path, text, fsio.LF, fsio.Plain); failed != "" {
		return fmt.Sprintf(msgDraftFailed, path, failed)
	}
	return ""
}
