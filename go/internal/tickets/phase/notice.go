package phase

import (
	"fmt"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// 開き直し・再開・レビュー待ちの子について言う文。
const (
	reopenLead = "  このフェーズを開き直したことが、後続の作業に影響する可能性がある。" +
		"継続・追加修正・取り消しの方針をユーザと決める（自動では取り消さない）:"
	reopenItem     = "    - "
	startedNote    = "着手済み"
	reviewedNote   = "レビュー済み"
	reviewTogether = "フェーズ %s のレビューは %s と一緒に見る計画。"

	resumedDeferred = "作業中（doing/）ですが、延期したフェーズ %s は一度終わっています。" +
		"延期フェーズの子なので、マーカーは引き受けたフェーズの番号のものです。" +
		"%s/ に *.%s が残っていれば、再開後の作業はそのレビューに含まれていません。"
	resumedReviewed = "作業中（doing/）ですが、フェーズ %s の reviewed マーカーが残っています。"
	eraseSteps      = "レビューをやり直すならマーカーを削除してください" +
		"（消し方: ユーザが%sで '%s rm %s' を実行し、" +
		"削除をコミットする。取り込み済みで origin がある親子なら " +
		"'%s %s' が取り込んでから送る。それ以外は何もしないので、" +
		"ユーザが自分でコミットする）。" +
		"続きの作業だけなら、そのままで構いません。" +
		"運用の基本は、新しいチケットを作り直すことです"
	unnumbered     = "<番号>"
	pushScript     = "ccnavi-user-push-approved.sh"
	markerTreeAny  = "マーカーがあるツリー"
	markerTreeRoot = "ワークスペースルート"
	markerTreeName = "ツリー "
)

// ReopenImpact は、フェーズを開き直したときに、後続の着手済み・レビュー済みのフェーズを挙げて方針を促す行を返す。無ければ空を返す。
// 後続とは、計画の先行で N 番目が先行するフェーズ。
// 消えるのは N 番目のマーカーだけで、後続の子とレビューの状態は残る。続けるか直すかは、ユーザが決める。
func ReopenImpact(conf *config.Settings, root, parentID string, number int) []string {
	touched := downstreamTouched(Phases(conf, root, parentID, nil), number)
	if len(touched) == 0 {
		return nil
	}
	out := []string{reopenLead}
	for _, line := range touched {
		out = append(out, reopenItem+line)
	}
	return out
}

// downstreamTouched は「フェーズ 3（実装とテスト）: 着手済み・レビュー済み」の形の行。
func downstreamTouched(list []*Phase, number int) []string {
	var owner *ticket.Ticket
	for _, p := range list {
		if p.owner != nil {
			owner = p.owner
			break
		}
	}
	if owner == nil {
		return nil
	}
	var out []string
	for _, p := range list {
		if p.Number <= number || !slices.Contains(phases.WaitsOf(owner, p.Number), number) {
			continue
		}
		began := false
		for _, s := range p.States {
			began = began || s == place.Doing || slices.Contains(place.Finished(), s)
		}
		reviewed := p.has(marks.Reviewed)
		var notes []string
		if began {
			notes = append(notes, startedNote)
		}
		if reviewed {
			notes = append(notes, reviewedNote)
		}
		if len(notes) > 0 {
			out = append(out, "フェーズ "+p.Label()+": "+strings.Join(notes, "・"))
		}
	}
	return out
}

// ReviewNext は、レビュー待ち（review/）の子について、ユーザに回るまでに誰が何をするかを 1 文で返す。
// 子を review/ に置いただけでは、ユーザに回らない。親が合流と review-request を済ませて初めて、レビュー待ちになる。
// 依頼の前に「ユーザのレビューを待つ」と言うと、依頼を打ち忘れてしまう。そのため、見るのはレビューを引き受けるフェーズの
// マーカーである。
func ReviewNext(conf *config.Settings, root string, child *ticket.Ticket, raw *approval.Raw) string {
	n, isChild := child.ChildPhase()
	if !isChild {
		return ""
	}
	// 1. 子のフェーズを探す。延期したフェーズなら、レビューを引き受けるフェーズ（owner）を見る。
	list := Phases(conf, root, child.Parent, raw)
	p := find(list, n)
	if p == nil {
		return ""
	}
	owner := p
	if at, ok := p.ReviewAt(); p.Deferred() && ok {
		if found := find(list, at); found != nil {
			owner = found
		}
	}
	lead := ""
	if owner != p {
		lead = fmt.Sprintf(reviewTogether, p.Label(), owner.Label())
	}
	// 2. owner のマーカーで、いまの段階を決める。レビュー済み→依頼済み（子の完了より前なら依頼し直し）→フェーズが終わっていない→依頼の準備中、の順。
	label, number, done := owner.Label(), strconv.Itoa(owner.Number), conf.Approved+"/"+string(place.Done)+"/"
	if owner.has(marks.Reviewed) {
		return texts.Say(root, "NOTICE_REVIEW_NEXT__REVIEWED",
			texts.Values{"lead": lead, "label": label, "done": done}, nil)
	}
	if requested, ok := owner.Marks[marks.Requested]; ok {
		at := ""
		if v := requested.Value("at"); pyfmt.Truthy(v) {
			at = pyfmt.Str(v)
		}
		if later(child.CompletedAt, at) {
			return texts.Say(root, "NOTICE_REVIEW_NEXT__REREQUEST",
				texts.Values{"lead": lead, "label": label, "phase": number, "done": done}, nil)
		}
		return texts.Say(root, "NOTICE_REVIEW_NEXT__REQUESTED",
			texts.Values{"lead": lead, "label": label, "waiting": labelWaiting}, nil)
	}
	if !p.Ended() || !owner.Ended() {
		left := "このフェーズ"
		if owner != p {
			left = "フェーズ " + owner.Label()
		}
		return texts.Say(root, "NOTICE_REVIEW_NEXT__PREPARING_OPEN", texts.Values{
			"lead": lead, "preparing": labelPreparing, "left": left, "phase": number, "done": done,
		}, nil)
	}
	return texts.Say(root, "NOTICE_REVIEW_NEXT__PREPARING",
		texts.Values{"lead": lead, "preparing": labelPreparing, "phase": number, "done": done}, nil)
}

// later は時刻 a が b より後か。どちらかが読めない、時差の有無が食い違うなら偽（依頼し直しを言わない側）。
func later(a, b string) bool {
	ta, za, oka := pyfmt.FromISOFormat(a)
	tb, zb, okb := pyfmt.FromISOFormat(b)
	return oka && okb && za == zb && ta.After(tb)
}

// ResumedReview は、作業中（doing/）の子のフェーズに、レビュー済みのマーカーが残っている形の警告文を返す。無ければ空を返す。文には子の識別子を入れない（呼び手が前に付ける）。
//
// 閉じたチケットを手で doing/ へ戻すと、reviewed が残る。もう一度 finish しても止まらない。
// マーカーを消すかどうかは、レビューをやり直すかでユーザが決める。
// 延期したフェーズの子は、一度終わった印（skipped）で言い、引き受け手は辿らない。
// review_required は doing/ の子の要求を数えない。そのため、閉じ直したときに要る分（この子の要求）をここでだけ足す。
// ゲートと告知の判定は動かさない。
func ResumedReview(conf *config.Settings, root string, child *ticket.Ticket, raw *approval.Raw) string {
	n, isChild := child.ChildPhase()
	if child.State != place.Doing || !isChild {
		return ""
	}
	p := find(Phases(conf, root, child.Parent, raw), n)
	if p == nil {
		return ""
	}
	if p.Deferred() {
		if !p.has(marks.Skipped) {
			return ""
		}
		return fmt.Sprintf(resumedDeferred, p.Label(), marksDir(conf, child), string(marks.Reviewed)) +
			eraseText(conf, root, child, unnumbered)
	}
	if !(p.ReviewRequired() || child.ReviewRequired == ticket.Needed) || !p.has(marks.Reviewed) {
		return ""
	}
	return fmt.Sprintf(resumedReviewed, p.Label()) + eraseText(conf, root, child, strconv.Itoa(p.Number))
}

func approvedRel(conf *config.Settings) string {
	if conf.Approved != "" {
		return conf.Approved
	}
	return config.DefaultApproved
}

// marksDir は子の親のマーカーの置き場（ツリーのルートからの相対）。
func marksDir(conf *config.Settings, child *ticket.Ticket) string {
	return approvedRel(conf) + "/" + place.PhasesDir + "/" + child.Parent
}

// eraseText は shown 番の reviewed マーカーを消す手順と、残してよい場合の言い添え。
func eraseText(conf *config.Settings, root string, child *ticket.Ticket, shown string) string {
	mark := marksDir(conf, child) + "/" + shown + "." + string(marks.Reviewed)
	return fmt.Sprintf(eraseSteps, markerTree(conf, root, child), config.ScriptCommand(root, config.GitWrapper), mark,
		config.ScriptCommand(root, pushScript), child.Parent)
}

// markerTree はマーカーを置くツリーを、ワークスペースルートからの相対で言う。置き場の設定が絶対パスなどで
// ツリーのルートを引けなければ「マーカーがあるツリー」。
func markerTree(conf *config.Settings, root string, child *ticket.Ticket) string {
	where := approval.HomeDirReadAny(conf, root, child.Parent, "")
	suffix := ospath.Sep + strings.ReplaceAll(approvedRel(conf), "/", ospath.Sep)
	treeRoot, ok := strings.CutSuffix(where, suffix)
	if !ok {
		return markerTreeAny
	}
	shown, ok := ospath.Relpath(treeRoot, root)
	if !ok {
		return markerTreeName + treeRoot
	}
	shown = strings.ReplaceAll(shown, ospath.Sep, "/")
	switch {
	case shown == ".":
		return markerTreeRoot
	case strings.HasPrefix(shown, ".."):
		return markerTreeName + treeRoot
	}
	return markerTreeName + shown
}
