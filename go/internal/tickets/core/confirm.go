package core

import (
	"fmt"
	"io"
	"strconv"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/lifecycle/ops"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/proposal/agree"
	"ccnavi/internal/tickets/review/request"
)

// acceptedByManual は手で動かした子の確定で、受け入れ台帳とレビュー済みのマーカーに書く経路。
const acceptedByManual = "manual"

// checked は confirm の答え。problems は標準エラーに出す行で、通らなければ planned が nil。
type checked struct {
	problems []string
	planned  *agree.Planned
}

func refusedCheck(c code, problems []string) checked {
	return checked{problems: refused(c, problems)}
}

// confirm はレビュー済みにしてよいかを見て、通れば書くもの（子を done/ へ、受け入れ台帳、レビュー済みのマーカー）を
// 溜める段に並べる。result はホストから取得した結果。changed は依頼の後にユーザが見るものが動いたかの説明（空なら
// 動いていない）で、moved は止めたあとの説明にだけ使う。manual が確定してよい形（SettleReady）なら、未解決の指摘を
// 解決せず版つきで受け入れ台帳に足す。account は `--actor` のアカウント（空なら書かない）。
func confirm(conf *config.Settings, root, account, parentID string, number int, result host.Result, changed string, manual *phase.Manual, moved request.Moved) (checked, error) {
	parent := openParent(conf, root, parentID)
	if parent == nil {
		return refusedCheck(codeConfirmNoParent, []string{"ccnavi: 親 " + parentID + " の承認済みチケットが作業中にありません。"}), nil
	}
	p := request.PhaseOf(conf, root, parent, number)
	if p == nil {
		return refusedCheck(codeConfirmNoPhase, []string{fmt.Sprintf("ccnavi: %s にフェーズ %d の子がありません。", parent.ID, number)}), nil
	}
	if stop := precondition(root, p, number, changed, moved); stop != nil {
		return *stop, nil
	}
	if problems := request.MatchingProblems(&result, p.Marks[marks.Requested]); len(problems) > 0 {
		return refusedCheck(codeConfirmResultMismatch, problems), nil
	}
	settled := manual != nil && manual.State == phase.SettleReady
	var ledger []*pyjson.Object
	if settled {
		// 手で動かした子の確定。未解決の指摘は解決せず、版つきで受け入れ台帳に足す。
		if stop := manualRefusal(conf, root, parent, number, result, *manual); stop != nil {
			return *stop, nil
		}
		for _, t := range request.UnresolvedFor(conf, root, parent, number, result) {
			ledger = append(ledger, pyjson.NewObject("key", host.ThreadKey(t), "version", host.ThreadVersion(t),
				"last_comment_id", t.LastCommentID, "accepted_by", acceptedByManual, "phase", number))
		}
	} else if problems := request.ReviewProblems(conf, root, parent, number, result); len(problems) > 0 {
		return refusedCheck(codeConfirmReviewOpen, problems), nil
	}
	route := via.Current()
	if account != "" {
		route = via.CLI
	}
	stage, done, err := fsio.Staging()
	if err != nil {
		return checked{}, err
	}
	defer done()
	restoreClock, err := fsio.FixClock(fsio.Stamp())
	if err != nil {
		return checked{}, err
	}
	defer restoreClock()
	defer via.Session(route, account)()
	routeText := ""
	if account != "" {
		routeText = string(route)
	}
	mark := request.ReviewedMark(result.MR.Number, nil, account, routeText, "")
	if settled {
		mark.Set("via", acceptedByManual)
		mark.Set("moved", append([]string{}, manual.Children...))
	}
	reason, err := request.SettleAndMark(stage, conf, root, parent, p, mark, ledger)
	if err != nil {
		return checked{}, err
	}
	planned := &agree.Planned{Stage: stage}
	if reason != "" {
		planned.Stopped = &agree.Stop{Reason: reason}
	}
	return checked{planned: planned}, nil
}

// precondition はホストの結果を見ずに分かる前提（レビュー済みでない・依頼の記録が在る・依頼のあとに見たものが
// 動いていない）。confirm と confirmGate が同じものを通る。通れば nil。
func precondition(root string, p *phase.Phase, number int, changed string, moved request.Moved) *checked {
	if _, ok := p.Marks[marks.Reviewed]; ok {
		// 重ね打ちでマーカーを書き直さない。
		c := refusedCheck(codeConfirmAlreadyReviewed, []string{fmt.Sprintf("ccnavi: フェーズ %d はレビュー済み", number)})
		return &c
	}
	if p.Marks[marks.Requested] == nil {
		c := refusedCheck(codeConfirmNotRequested, []string{"ccnavi: 依頼の記録がありません。先に review-request してください"})
		return &c
	}
	if changed != "" {
		lines := append([]string{"ccnavi: " + changed + "。ユーザが見たものと今の HEAD が違う。" + request.RedoRequest(root, number)},
			request.MovedExplanation(moved)...)
		c := refusedCheck(codeConfirmMoved, lines)
		return &c
	}
	return nil
}

// manualRefusal は手で動かした移動の確定を止める理由。変更要求のレビューがあれば保留（移動は残す）。受け入れるのは
// 移動のコミットより phase.Margin 以上前にホストで作られたスレッドだけで、後に付いた指摘や時刻が読めない指摘が
// 残れば確定しない（その分は decide へ）。通れば nil。
func manualRefusal(conf *config.Settings, root string, parent *ticket.Ticket, number int, result host.Result, manual phase.Manual) *checked {
	if len(request.ChangesRequested(result)) > 0 {
		c := refusedCheck(codeConfirmReviewOpen, []string{"ccnavi: " + host.ChangesRequestedNote(result.Host) +
			"。手で動かした移動は保持するが、変更要求があるためレビュー済みにはできません（保留）"})
		return &c
	}
	late := lateThreads(request.UnresolvedFor(conf, root, parent, number, result), fsio.UnixSeconds(manual.MovedAt))
	if len(late) == 0 {
		return nil
	}
	lines := []string{fmt.Sprintf("ccnavi: 移動の後に付いた指摘（または時刻を読めない指摘）が %d 件残っていて、"+
		"手で動かした移動を確定しません。decide で行き先を決めてください", len(late))}
	for _, t := range late {
		lines = append(lines, "  - "+request.ThreadLabel(t))
	}
	c := refusedCheck(codeConfirmReviewOpen, lines)
	return &c
}

// lateThreads は移動（movedAt。epoch 秒）の余裕幅より後に作られたか、最後のコメントが付いたスレッドと、作成の時刻を
// 読めないスレッド。最初のコメントだけでなく最後のコメントも見る（移動の後に付いた返信を受け入れない）。
func lateThreads(threads []host.Thread, movedAt float64) []host.Thread {
	var late []host.Thread
	for _, t := range threads {
		created := host.Epoch(t.CreatedAt)
		made := max(created, host.Epoch(t.LastCommentAt))
		if created <= 0 || made > movedAt-phase.Margin.Seconds() {
			late = append(late, t)
		}
	}
	return late
}

// openParent は作業中の親の承認済みチケット（正とする側）。無い・子なら nil。
func openParent(conf *config.Settings, root, parentID string) *ticket.Ticket {
	open, _ := approval.Scan(conf, root, false, nil)
	found, ok := tree.Lookup(approval.ByID(open), parentID)
	if !ok || found.IsChild() {
		return nil
	}
	return found
}

// ConfirmLocal は review confirm。依頼の後を見て、通ればレビュー済みのマーカーを置いて先へ進めるようにする。検査と
// 書くものの並べ方は confirm で、ここは手元の入力（cwd の親、git の差分、--result の取得した結果）を読んで渡し、
// 並べたものを書く。account は ccnavi-review.sh がトークンの持ち主を引いて --actor で渡すアカウントで、あれば
// マーカーに actor と via: cli を書く。
func ConfirmLocal(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, resultPath, account string) (int, error) {
	parent, p, moved, ok := confirmGate(stderr, conf, root, cwd, number)
	if !ok {
		return 1, nil
	}
	// 通った時点で、依頼の記録は在り、依頼のあとに動いておらず、レビュー済みでもない。
	result, ok := request.ReadResult(stderr, resultPath)
	if !ok {
		return 1, nil
	}
	manual := phase.AssessManual(conf, root, parent, p, nil, phase.ManualOptions{})
	if manual.State != phase.SettleNone && manual.State != phase.SettleReady {
		// 手で動かした移動が確定できない形のときの知らせ。
		if line := manual.Line(); line != "" {
			io.WriteString(stdout, linePrefix+line+"\n")
		}
	}
	c, err := confirm(conf, root, account, parent.ID, number, result, "", &manual, moved)
	if err != nil {
		return 1, err
	}
	for _, line := range c.problems {
		io.WriteString(stderr, line+"\n")
	}
	if c.planned == nil {
		return 1, nil
	}
	code := write(stdout, stderr, c.planned).Code
	if code == 0 {
		io.WriteString(stdout, nextAfterConfirm(conf, root, parent.ID)+"\n")
	}
	return code, nil
}

// confirmGate はホストに聞かずに分かる前提の検査（親とフェーズがある・precondition）。ConfirmLocal と CheckConfirm が
// 同じものを通る。断ったときは理由のあとに `次:` を 1 行足す。ホストの結果（未解決の指摘など）は見ない。
func confirmGate(stderr io.Writer, conf *config.Settings, root, cwd string, number int) (*ticket.Ticket, *phase.Phase, request.Moved, bool) {
	parent, p, ok := request.ParentPhase(stderr, conf, root, cwd, number)
	if !ok {
		sayRefusedNext(stderr, root, number)
		return nil, nil, request.Moved{}, false
	}
	var moved request.Moved
	// 依頼の記録が無ければ差分は見ない。レビュー済みなら差分も読まない（precondition が「レビュー済み」で止める）。
	if requested := p.Marks[marks.Requested]; requested != nil {
		if _, reviewed := p.Marks[marks.Reviewed]; !reviewed {
			moved = request.MovedInTree(conf, tree.WorktreePath(root, parent.ID), requested, parent.Project != "")
		}
	}
	if stop := precondition(root, p, number, moved.Text, moved); stop != nil {
		for _, line := range stop.problems {
			io.WriteString(stderr, line+"\n")
		}
		sayRefusedNext(stderr, root, number)
		return nil, nil, request.Moved{}, false
	}
	return parent, p, moved, true
}

// sayRefusedNext は断った理由のあとに、打ち直す 1 手を `次:` で足す。
func sayRefusedNext(stderr io.Writer, root string, number int) {
	io.WriteString(stderr, texts.Say(root, "NEXT_CONFIRM_REFUSED", texts.Values{"phase": strconv.Itoa(number)}, nil)+"\n")
}

// CheckConfirm は review confirm --check。confirmGate を通すだけで何も書かない（sh が取り込みの前に呼ぶ）。
func CheckConfirm(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int) int {
	if _, _, _, ok := confirmGate(stderr, conf, root, cwd, number); !ok {
		return 1
	}
	io.WriteString(stdout, "確認: ok（confirm を打てる状態。何も変えていません）\n")
	return 0
}

// nextAfterConfirm はレビュー済みを書いたあとの出力。親を閉じられる状態（closing.CloseProblems が空）になったら
// 親を閉じる流れを言い、まだなら status を促す（最後のフェーズのあとの手順が、親の finish の出力で初めて出てくるのを
// 避ける）。
func nextAfterConfirm(conf *config.Settings, root, parentID string) string {
	if len(closing.CloseProblems(conf, root, parentID, nil)) > 0 {
		return texts.Say(root, "NEXT_REVIEW_CONFIRMED", texts.Values{"parent": parentID}, nil)
	}
	return "全部のフェーズのレビューが済み、親 " + parentID + " を閉じられる。\n" + texts.Say(root, "NEXT_REVIEW_LAST", texts.Values{
		"parent":        parentID,
		"ready_command": ops.ReadyCommand(root, parentID),
		"wip":           place.WipRoot,
		"flow_agreed":   ops.ReadyFlowAgreed,
	}, nil)
}
