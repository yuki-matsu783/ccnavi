package phase

import (
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/model/useronly"
)

// フェーズの終わりの知らせの文。
const (
	markFailedText = "ccnavi: フェーズのマーカーを保存できませんでした: %s\n"
	// 延期: 親・ラベル・引き受ける番号。
	deferredText = "[ccnavi] %s のフェーズ %s が終わりました。レビューは %s 番目と一緒に見る計画なので、ここでは止めません。"
	// レビュー要: 親・ラベル・呼び名・誰が・依頼のコマンド・番号・覆う分・ターン・止める案内。
	pendingText = "[ccnavi] %s のフェーズ %s が終わりました（%s）。%s子が合流済み（finish 済み）であることを確かめ、" +
		"'%s review-request --phase %d --body-file <依頼文>' でレビュー%sを頼み、%sを終えてユーザを待ってください。%s"
	holdNoteText = "指摘があれば同じフェーズに子を足せます。レビュー済みになるまで、" +
		"サブエージェントの起動とシェル実行は止まります。"
	coversText       = "（%s 番目の分も含めて）"
	requiredText     = "人間レビュー要の子: %s。"
	escalatedText    = "実績のリスクが高いので、宣言に関わらずレビューが要ります（%s）。"
	skippedText      = "[ccnavi] %s のフェーズ %s が終わりました。%sレビューは不要なので、省略して次へ進めます。省略した事実は記録に残しました。"
	readyHintText    = "（%s の案内: %s）"
	announceSeparate = "\n\n"
)

// typeSource は review: が絡むマーカー（省略と保留）に足す、定義のレイヤー。定義の無いフェーズでは欄を置かない。
func typeSource(o *pyjson.Object, p *Phase, common *phases.Types) *pyjson.Object {
	if p.Type != nil {
		o.Set("source", phases.Origin(p.Type, common))
	}
	return o
}

// Announce は、終わったばかりのフェーズについて 1 度だけ言う文を返す。無ければ空を返す。書くのはマーカーだけである。書けなければ stderr に言う。マーカーを書く置き場が決まらなければ、何も書かずに空を返す。読む側に無い置き場へ書くと、そこに置き場ができてしまうからである。raw は Phases と同じである。
func Announce(stderr io.Writer, conf *config.Settings, root string, parent *ticket.Ticket, raw *approval.Raw) string {
	where, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if errors.Is(err, approval.ErrNoParentWorktree) {
		return ""
	}
	list := Phases(conf, root, parent.ID, raw)
	// source を決めるためだけに読む（判定はコピーを読む）。マーカーを置くフェーズがあるときだけ。
	var common *phases.Types
	for _, p := range list {
		if p.Ended() && len(p.Marks) == 0 && !p.Deferred() {
			common, _ = phases.CommonTypes(conf)
			break
		}
	}
	write := func(n int, kind marks.Kind, data *pyjson.Object) {
		if failed := marks.WriteMark(where, parent.ID, n, kind, data); failed != "" {
			fmt.Fprintf(stderr, markFailedText, failed)
		}
	}
	var said []string
	for _, p := range list {
		if !p.Ended() || len(p.Marks) > 0 {
			continue
		}
		n := p.Number
		switch {
		case p.Deferred():
			var at any
			if to, ok := p.ReviewAt(); ok {
				at = to
			}
			write(n, marks.Skipped, pyjson.NewObject("deferred_to", at))
			said = append(said, fmt.Sprintf(deferredText, parent.ID, p.Label(), pyfmt.Str(at))+
				nextHint(root, parent, list, n))
		case p.ReviewRequired():
			write(n, marks.Pending, typeSource(pyjson.NewObject("review", true), p, common))
			var required []string
			for _, t := range p.Tickets {
				if t.ReviewRequired == ticket.Needed {
					required = append(required, t.ID)
				}
			}
			covers := ""
			if c := p.Covers(); len(c) > 0 {
				covers = fmt.Sprintf(coversText, joinNumbers(c, ", "))
			}
			who := ""
			if len(required) > 0 {
				who = fmt.Sprintf(requiredText, strings.Join(required, ", "))
			}
			if p.RiskEscalates() {
				who += fmt.Sprintf(escalatedText, p.RiskLine())
			} else if line := p.RiskLine(); line != "" {
				who += line + "。"
			}
			said = append(said, fmt.Sprintf(pendingText, parent.ID, p.Label(), labelPreparing, who,
				config.ScriptCommand(root, config.ReviewWrapper), n, covers, useronly.TurnDefined,
				holdNoteText+useronly.ExemptNote))
		default:
			ids := make([]any, 0, len(p.Tickets))
			for _, t := range p.Tickets {
				ids = append(ids, t.ID)
			}
			write(n, marks.Skipped, typeSource(pyjson.NewObject("tickets", ids), p, common))
			note := ""
			if line := p.RiskLine(); line != "" {
				note = line + "。"
			}
			said = append(said, fmt.Sprintf(skippedText, parent.ID, p.Label(), note)+nextHint(root, parent, list, n))
		}
	}
	return strings.Join(said, announceSeparate)
}

func joinNumbers(numbers []int, sep string) string {
	parts := make([]string, len(numbers))
	for i, n := range numbers {
		parts[i] = strconv.Itoa(n)
	}
	return strings.Join(parts, sep)
}

// nextHint は次のフェーズの計画を促す 1 文。計画が無ければ空。並行して始められるもの（番号が前でも、まだ子の
// 無い枝）を、先行が済んだものだけ挙げる。
func nextHint(root string, parent *ticket.Ticket, list []*Phase, number int) string {
	if !parent.HasPlan() {
		return ""
	}
	following := false
	for _, p := range list {
		following = following || p.Number > number
	}
	if !following {
		if !parent.FeedbackPlanned {
			return texts.Say(root, "NOTICE_PHASE_NEXT__FEEDBACK",
				texts.Values{"parent": parent.ID, "launcher": config.Launcher}, nil)
		}
		return texts.Say(root, "NOTICE_PHASE_NEXT__DONE", nil, nil)
	}
	var names []string
	hints := ""
	for _, p := range list {
		if len(p.Tickets) > 0 || !waitsDone(parent, list, p.Number) {
			continue
		}
		names = append(names, p.Label())
		if p.Type != nil && p.Type.When != "" {
			hints += fmt.Sprintf(readyHintText, p.Label(), p.Type.When)
		}
	}
	if len(names) == 0 {
		return texts.Say(root, "NOTICE_PHASE_NEXT__WAITING", nil, nil)
	}
	return texts.Say(root, "NOTICE_PHASE_NEXT__READY",
		texts.Values{"names": strings.Join(names, "、"), "hints": hints}, nil)
}

// waitsDone は N 番目の先行のフェーズが、全部閉じてレビューが済んでいるか。
func waitsDone(parent *ticket.Ticket, list []*Phase, number int) bool {
	for _, m := range phases.WaitsOf(parent, number) {
		p := find(list, m)
		if p == nil || !p.Ended() || !p.ReviewedOrSkipped() {
			return false
		}
	}
	return true
}
