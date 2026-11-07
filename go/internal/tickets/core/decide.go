package core

import (
	"errors"
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
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/proposal/agree"
	"ccnavi/internal/tickets/review/decide"
	"ccnavi/internal/tickets/review/request"
)

// Chrome の decide（残った指摘の行き先を決める）。手元の `--reviewed N --accept-unresolved` と同じ判断を、書くもの
// （Changes）とホストへの書き込みの計画（HostPlan）に並べる。実行ファイルはネットワークに出ないので、ホストへは
// 呼び手が書く。

const (
	// IssueURLSlot は返信の本文の、issue の URL を入れる枠。issue を作った呼び手が置き換える。
	IssueURLSlot = "{issue_url}"
	// IssueLinksSlot は決めた内容のコメントの、作った issue の一覧を入れる枠。issue に回す指摘があるときだけ入る。
	IssueLinksSlot = "{issue_links}"
	// MaxIssues は 1 回の apply で issue にできる指摘の上限（ホストへの書き込みを 1 回の操作に収める）。
	MaxIssues = 40
	// issueTitleLimit は issue のタイトルの字数の上限（GitHub は 256 字）。
	issueTitleLimit = 200
	// 操作ID の頭。続きの子と issue で分ける。
	opDecideFix   = "chrome-decide-fix"
	opDecideIssue = "chrome-decide-issue"
)

// DecidePreview は見せる指摘（decide.JSON の形）。通らなければ Preview が nil で、Code に種別。
type DecidePreview struct {
	Problems []string
	Code     string
	Preview  *pyjson.Object
}

// Decided は Decide の答え。通らなければ Changes と HostPlan が nil で、Code に種別。Reviewed はレビュー済みの
// マーカーを置くか、Followup は起こす続きの子（無ければ空）、PendingExtra は見せたあとに増えた指摘のキー。
type Decided struct {
	Checked
	HostPlan     *pyjson.Object
	Reviewed     bool
	Followup     string
	PendingExtra []string
	Mismatch     *pyjson.Object
}

func refusedDecided(c code, problems []string, mismatch *pyjson.Object) Decided {
	return Decided{Checked: Checked{Problems: refused(c, problems), Code: string(c)}, Mismatch: mismatch, PendingExtra: []string{}}
}

// decision は残った指摘を決められる状態かを見て、見せる材料を組む。止める条件と並びは confirm と同じ（親・フェーズ・
// レビュー済み・依頼の記録・動いたか・結果の照合）に、変更要求を足したもの。未解決のスレッドが残っても止めない
// （それを決めるのが decide）。carry は受け入れ済み（ホストで解決済み）のキーで、見せた指摘の一部として数える。
func decision(conf *config.Settings, root, parentID string, number int, result host.Result, changed string, moved request.Moved, carry []string) (*decide.Decision, *checked) {
	parent := openParent(conf, root, parentID)
	if parent == nil {
		c := refusedCheck(codeConfirmNoParent, []string{"ccnavi: 親 " + parentID + " の承認済みチケットが作業中にありません。"})
		return nil, &c
	}
	p := request.PhaseOf(conf, root, parent, number)
	if p == nil {
		c := refusedCheck(codeConfirmNoPhase, []string{fmt.Sprintf("ccnavi: %s にフェーズ %d の子がありません。", parent.ID, number)})
		return nil, &c
	}
	if stop := precondition(root, p, number, changed, moved); stop != nil {
		return nil, stop
	}
	if problems := request.MatchingProblems(&result, p.Marks[marks.Requested]); len(problems) > 0 {
		c := refusedCheck(codeConfirmResultMismatch, problems)
		return nil, &c
	}
	if len(request.ChangesRequested(result)) > 0 {
		c := refusedCheck(codeConfirmReviewOpen, request.ReviewProblems(conf, root, parent, number, result))
		return nil, &c
	}
	open := request.UnresolvedFor(conf, root, parent, number, result)
	var unresolved []host.Thread
	for _, t := range result.Threads {
		if slices.Contains(open, t) || slices.Contains(carry, host.ThreadKey(t)) {
			unresolved = append(unresolved, t)
		}
	}
	return &decide.Decision{Parent: parent, Phase: p, Result: result, Unresolved: unresolved}, nil
}

// DecidePreviewOf は見せる指摘とダイジェストを返す（何も積まない）。形とダイジェストは手元の
// `--reviewed N --accept-unresolved --preview --json` と同じ。止める条件は Decide と同じ。
func DecidePreviewOf(conf *config.Settings, root, parentID string, number int, result host.Result, changed string, moved request.Moved, carry []string) DecidePreview {
	d, stop := decision(conf, root, parentID, number, result, changed, moved, carry)
	if stop != nil {
		return DecidePreview{Problems: stop.problems, Code: string(stop.code)}
	}
	return DecidePreview{Problems: []string{}, Preview: decide.JSON(d)}
}

// Decide は選んだ行き先（choices。指摘のキー → keep・fix・issue）を、書くものとホストへの書き込みの計画に並べる。
// shownDigest は DecidePreviewOf が返したダイジェストで、無い・読めない・今の指摘と違うなら何も積まない。見せたあとに
// 増えた指摘は食い違いに数えず、未対応として残す。carry はホストで受け入れ済みと確かめたキーで、結果で解決済みで
// なければならず、fix にはできない。HostPlan からは外れる（もう書かない）。
//
// HostPlan は {"issues": [{key, op, title, body, comments}], "replies": [{key, thread_id, body}],
// "resolves": [{key, thread_id}], "comment"}。書く順は issues、replies、resolves、changes のコミット、comment。
// 続きの子（fix）の識別子は親のブランチ上の既存のチケットだけを見て決め、直す指摘も追加分も無ければマーカーを置く。
func Decide(conf *config.Settings, root string, actor Actor, stamp, parentID string, number int, result host.Result, changed string, moved request.Moved, choices *pyjson.Object, shownDigest string, carry []string) (Decided, error) {
	d, stop := decision(conf, root, parentID, number, result, changed, moved, carry)
	if stop != nil {
		return Decided{Checked: Checked{Problems: stop.problems, Code: string(stop.code)}, PendingExtra: []string{}}, nil
	}
	shown, ok := decide.ParseDigest(ospath.Strip(shownDigest))
	if !ok {
		return refusedDecided(codeDecideNoDigest, []string{"ccnavi: 見せた指摘のダイジェストが無いか、読めません。指摘を見せ直してください。何も積んでいません"}, nil), nil
	}
	if !validChoices(choices) {
		names := make([]string, len(decide.Choices))
		for i, c := range decide.Choices {
			names[i] = string(c)
		}
		return refusedDecided(codeDecideChoices, []string{"ccnavi: 選択は {キー: " + strings.Join(names, " / ") + "} の形で渡してください"}, nil), nil
	}
	// 受け入れ済みのキーは、ホストの結果で解決済みでなければならない（取り直した実状態で確かめる）。
	byKey := map[string]host.Thread{}
	for _, t := range result.Threads {
		byKey[host.ThreadKey(t)] = t
	}
	sortedCarry := slices.Sorted(slices.Values(carry))
	for _, key := range sortedCarry {
		t, ok := byKey[key]
		if !ok || !t.Resolved || choiceOf(choices, key) == decide.Fix {
			return refusedDecided(codeDecideChoices, []string{"ccnavi: 受け入れ済みとされたスレッド " + key + " が、ホストで解決済みになっていない"}, nil), nil
		}
	}
	if differs := decide.ChangedSinceShown(d, shown); len(differs) > 0 {
		mismatch := pyjson.NewObject("digest", pyjson.NewObject("expected", shownDigest, "current", decide.Digest(d)), "changed", differs)
		return refusedDecided(codeDecideMismatch, []string{"ccnavi: 見せた指摘と今の指摘が違います。何も積んでいません"}, mismatch), nil
	}
	var kept []host.Thread
	for _, t := range d.Unresolved {
		if _, seen := shown.Versions[host.ThreadKey(t)]; seen {
			kept = append(kept, t)
		} else if !t.Resolved {
			d.Extra = append(d.Extra, t)
		}
	}
	d.Unresolved = kept
	if !sameKeySet(choices.Keys(), shown.Keys) {
		return refusedDecided(codeDecideChoices, []string{"ccnavi: 選択が見せた指摘と揃っていません（足りないか、余分があります）"}, nil), nil
	}
	picked := map[decide.Choice][]host.Thread{}
	for _, t := range d.Unresolved {
		c := choiceOf(choices, host.ThreadKey(t))
		picked[c] = append(picked[c], t)
	}
	if n := len(picked[decide.Issue]); n > MaxIssues {
		return refusedDecided(codeDecideChoices, []string{fmt.Sprintf(
			"ccnavi: issue に回す指摘が %d 件あります。1 回で回せるのは %d 件まで。選び直してください。何も積んでいません", n, MaxIssues)}, nil), nil
	}
	fix := picked[decide.Fix]
	var toWrite []host.Thread
	for _, t := range append(slices.Clone(picked[decide.Keep]), picked[decide.Issue]...) {
		if !slices.Contains(carry, host.ThreadKey(t)) {
			toWrite = append(toWrite, t)
		}
	}
	if problem := host.AcceptPlanProblem(toWrite); problem != "" {
		return refusedDecided(codeDecideThreadID, []string{"ccnavi: 受け入れを始められません: " + problem}, nil), nil
	}
	parts := []string{opDecideFix, d.Parent.ID, strconv.Itoa(d.Phase.Number)}
	var versions []string
	for _, t := range fix {
		versions = append(versions, host.ThreadKey(t)+":"+host.ThreadVersion(t))
	}
	slices.Sort(versions)
	fixOp := oplog.IDFor(append(parts, versions...)...)
	items := decide.Transcribe(d, fix, fixOp)
	children := decide.SettledChildren(d.Phase)
	if len(fix) > 0 {
		candidates := children
		if len(candidates) == 0 {
			candidates = d.Phase.Tickets
		}
		if problem := approval.FollowupProblem(conf, root, d.Parent, d.Phase.Number, candidates, decide.Transcribe(d, fix, "")); problem != "" {
			return refusedDecided(codeDecideFollowup, []string{"ccnavi: 続きの子チケットを起こせない: " + problem}, nil), nil
		}
	}
	stage, done, err := fsio.Staging()
	if err != nil {
		return Decided{}, err
	}
	defer done()
	if stamp == "" {
		stamp = fsio.Stamp()
	}
	restoreClock, err := fsio.FixClock(stamp)
	if err != nil {
		return Decided{}, err
	}
	defer restoreClock()
	defer via.Session(actor.route(), actor.Account)()
	followup, failed, err := stageDecision(stage, conf, root, actor, d, picked, children, items, fixOp)
	if err != nil {
		var noParent *approval.NoParentError
		if !errors.As(err, &noParent) {
			return Decided{}, err
		}
		failed = "親のワークツリーが決まらない（" + noParent.Reason + "）"
	}
	if failed != "" {
		return refusedDecided(codeDecideStopped, []string{"ccnavi: " + failed}, nil), nil
	}
	accepted := make([]accepting, len(toWrite))
	for i, t := range toWrite {
		accepted[i] = accepting{key: host.ThreadKey(t), threadID: t.ID, choice: choiceOf(choices, host.ThreadKey(t))}
	}
	extra := make([]string, len(d.Extra))
	for i, t := range d.Extra {
		extra[i] = host.ThreadKey(t)
	}
	return Decided{
		Checked:      Checked{Problems: []string{}, Changes: &Changes{Planned: &agree.Planned{Stage: stage}, conf: conf, root: root}},
		HostPlan:     hostPlan(d, accepted, picked, followup),
		Reviewed:     followup == "" && len(d.Extra) == 0,
		Followup:     followup,
		PendingExtra: extra,
	}, nil
}

func validChoices(choices *pyjson.Object) bool {
	if choices == nil {
		return false
	}
	for _, key := range choices.Keys() {
		c, ok := choices.Value(key).(string)
		if !ok || !slices.Contains(decide.Choices, decide.Choice(c)) {
			return false
		}
	}
	return true
}

func choiceOf(choices *pyjson.Object, key string) decide.Choice {
	c, _ := choices.Value(key).(string)
	return decide.Choice(c)
}

// sameKeySet は 2 つのキーの集まりが同じか（並びと重なりは問わない）。
func sameKeySet(a, b []string) bool {
	in := func(keys []string, k string) bool { return slices.Contains(keys, k) }
	for _, k := range a {
		if !in(b, k) {
			return false
		}
	}
	for _, k := range b {
		if !in(a, k) {
			return false
		}
	}
	return true
}

// accepting はホストで受け入れる（返信して解決する）指摘。choice は keep か issue だけで、fix（このフェーズで
// 直す）は作らない。直す指摘に返信も解決も付けない。
type accepting struct {
	key, threadID string
	choice        decide.Choice
}

// stageDecision は親のブランチへ書くもの（子を done/ へ、続きの子、レビュー済みのマーカー）を並べる。答えは起こした
// 続きの子と、止まったときの理由。順は手元の decide と同じ。直す指摘があるか、見せたあとに増えた指摘が無いときだけ
// レビュー待ちの子を動かす。直す指摘があれば続きの子を起こし（マーカーは置かない）、無くて追加分も無ければ
// マーカーを置く。
func stageDecision(stage *fsio.Stage, conf *config.Settings, root string, actor Actor, d *decide.Decision, picked map[decide.Choice][]host.Thread, children []*ticket.Ticket, items []string, fixOp string) (string, string, error) {
	restore := fsio.SetPolicy(func(po *fsio.Policy) {
		po.OnFail, po.Ticket, po.Message, po.Prefix, po.Undo, po.Places = fsio.FailStop, "", "{reason}", "", nil, ""
	})
	defer restore()
	parent, p := d.Parent, d.Phase
	fix := picked[decide.Fix]
	var accepted []string
	for _, t := range append(slices.Clone(picked[decide.Keep]), picked[decide.Issue]...) {
		accepted = append(accepted, host.ThreadKey(t))
	}
	if len(fix) > 0 || len(d.Extra) == 0 {
		by := request.ByDecide
		if via.Current() == via.Chrome {
			by = string(via.Chrome)
		}
		moved, failed, err := approval.SettleReview(conf, root, parent.ID, append(slices.Clone(p.Covers()), p.Number), by)
		if err != nil || failed != "" {
			return "", failed, err
		}
		if len(moved) > 0 {
			stage.AddLine(request.MovedLine(conf, moved), 0, fsio.StreamOut)
		}
	}
	if len(fix) > 0 {
		followup, failed := approval.Followup(conf, root, parent, p.Number, children, items, io.Discard, fixOp)
		if failed != "" {
			return "", "続きの子チケットを起こせない: " + failed, nil
		}
		stage.AddLine(decide.FollowupLine(conf, followup, p.Number, len(items)), 0, fsio.StreamOut)
		return followup, "", nil
	}
	if len(d.Extra) > 0 {
		return "", "", nil
	}
	home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if err != nil {
		var noParent *approval.NoParentError
		if errors.As(err, &noParent) {
			return "", "マーカーを置く親のワークツリーが決まらない（" + noParent.Reason + "）", nil
		}
		return "", "", err
	}
	prefixed := fsio.SetPolicy(func(po *fsio.Policy) { po.Prefix = request.MarkPrefix })
	failed := marks.WriteMark(home, parent.ID, p.Number, marks.Reviewed, request.ReviewedMark(d.Result.MR.Number, accepted, actor.Account, string(actor.Via), ""))
	prefixed()
	if failed != "" {
		return "", request.MarkPrefix + failed, nil
	}
	stage.AddLine(request.ReviewedLine(p.Number), 0, fsio.StreamOut)
	return "", "", nil
}

// issueTitle は指摘 1 件の issue のタイトル。出どころのタイトルに、指摘の位置（無ければキー）を添える。
func issueTitle(head string, t host.Thread) string {
	where := t.Path
	if t.Path != "" && t.Line != 0 {
		where = fmt.Sprintf("%s:%d", t.Path, t.Line)
	}
	if where == "" {
		where = host.ThreadKey(t)
	}
	span := []rune(decide.Span(where))
	inner := span[1 : len(span)-1]
	room := max(0, issueTitleLimit-len([]rune(head))-4)
	if len(inner) > room {
		inner = inner[:room]
	}
	return head + ": `" + string(inner) + "`"
}

// issuePlan は指摘 1 件の issue の計画。本文の頭に、操作ID の隠しコメントを入れる。
func issuePlan(d *decide.Decision, t host.Thread) *pyjson.Object {
	key := host.ThreadKey(t)
	op := oplog.IDFor(opDecideIssue, d.Parent.ID, strconv.Itoa(d.Phase.Number), key, host.ThreadVersion(t))
	main, parts := decide.IssueTexts(d, []host.Thread{t}, op)
	head, rest, _ := strings.Cut(main, "\n")
	return pyjson.NewObject(
		"key", key, "op", op, "title", issueTitle(head, t),
		"body", host.IssueMarker(op)+"\n\n"+strings.TrimLeft(rest, "\n"),
		"comments", list(parts),
	)
}

// hostPlan はホストへの書き込みの計画。返信と解決は accepted（ホストにまだ書いていない受け入れ）からだけ組む。
func hostPlan(d *decide.Decision, accepted []accepting, picked map[decide.Choice][]host.Thread, followup string) *pyjson.Object {
	why := decide.AcceptWhy(strconv.Itoa(d.Phase.Number))
	byKey := map[string]host.Thread{}
	for _, t := range d.Unresolved {
		byKey[host.ThreadKey(t)] = t
	}
	issues, replies, resolves := []any{}, []any{}, []any{}
	for _, a := range accepted {
		if a.choice == decide.Issue {
			issues = append(issues, issuePlan(d, byKey[a.key]))
		}
	}
	for _, a := range accepted {
		url := ""
		if a.choice == decide.Issue {
			url = IssueURLSlot
		}
		replies = append(replies, pyjson.NewObject("key", a.key, "thread_id", a.threadID, "body", host.AcceptReply(why, a.choice == decide.Issue, url)))
	}
	for _, a := range accepted {
		resolves = append(resolves, pyjson.NewObject("key", a.key, "thread_id", a.threadID))
	}
	comment := decide.CommentText(d, picked, followup)
	if len(issues) > 0 {
		comment += "\nissue に回した分:\n" + IssueLinksSlot + "\n"
	}
	return pyjson.NewObject("issues", issues, "replies", replies, "resolves", resolves, "comment", comment)
}
