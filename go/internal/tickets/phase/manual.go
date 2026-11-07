package phase

import (
	"fmt"
	"math"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// 手で動かした子の判定は、ユーザがホストの画面などで子を review/ から done/ へ動かしたことを、レビュー完了の意思として読む。
// ccnavi が子を動かす経路は settled_by を書く。そのため、finished_to: review の子が done/ にあって settled_by が無ければ、
// 手で動かしたことになる。
// 判定に使うのは、親の正本の置き場とその git の履歴だけ。ネットワークには出ない。
// origin/<親のブランチ> は、最後に取得した状態を指す。確定そのものは confirm が行う。

// Settle は手動の移動の判定の結果。
type Settle string

const (
	SettleNone         Settle = "none"          // 該当しない（レビューが要らない・済み・手で動かした子が無い）
	SettleReady        Settle = "ready"         // 確定してよい形（confirm が受け入れる）
	settlePartial      Settle = "partial"       // 動かしたのは一部の子だけ
	SettleUnshared     Settle = "unshared"      // 移動が未コミット・未 push
	settleNoRequest    Settle = "no-request"    // 依頼の記録が無い
	settleRouteUnknown Settle = "route-unknown" // 経路が分からない子しか無い（finished_to が無い）
	settleBroken       Settle = "broken"        // 読めない・重複していて確かめられない
)

// Margin は移動のコミットの時刻と、ホストのスレッドの作成時刻の間に置く余裕。ホストはユーザが push した時刻を
// 返さないので、余裕の内側か後に作られたスレッドは受け入れない。
const Margin = 10 * time.Minute

// 手動の移動の 1 行と、確かめられない理由。
const (
	readyLine        = "レビュー完了確認待ち: 子 %s が手で done/ に動かされている。親が confirm を打つと確定する"
	partialLine      = "子 %s はレビュー完了、フェーズは未完了（まだ review/・doing/ に残る子: %s）"
	unsharedLine     = "移動が未共有: 子 %s の done/ への移動が未コミットか未 push。ユーザの commit・push が必要（確定しない）"
	noRequestLine    = "確認待ち（依頼の記録が無い）: 先に review-request を打つ"
	routeUnknownLine = "レビュー完了確認待ち（経路が分からない）: 子が done/ に動いているが、" +
		"動かした経路の記録が無い。confirm か decide で確かめる"
	brokenLine = "手動移動を確かめられない: %s"

	duplicateNote = "同じ識別子の子が重複している"
	badTimeNote   = "移動の時刻を読めない"
	unsharedNote  = "unshared"
	noBranchNote  = "親のブランチを読めない"
	outsideNote   = "%s の done/ のファイルが親のツリーの外にある"
	noStatusNote  = "git status を読めない"
	noLogNote     = "git log を読めない"
	noRemoteNote  = "origin/ のブランチを読めない"
)

// Manual は手動の移動の判定。
type Manual struct {
	State Settle
	// Children は手で動かした子。
	Children []string
	// MovedAt は手で動かした子の移動コミットのうち、いちばん新しい時刻。
	MovedAt time.Time
	// waiting はまだ動いていない子（settlePartial）。
	waiting []string
	note    string
	// NeedTime は外から移動の時刻をもらう形（Chrome）で、まだ時刻が無いとき真。Paths は動かした子の done/ の
	// パスで、呼び手は時刻を読んで渡し直す。
	NeedTime bool
	Paths    []string
}

// Line は status・confirm・Stop が出す 1 行。出すものが無ければ空。
func (m Manual) Line() string {
	who := strings.Join(m.Children, "、")
	switch m.State {
	case SettleReady:
		return fmt.Sprintf(readyLine, who)
	case settlePartial:
		return fmt.Sprintf(partialLine, who, strings.Join(m.waiting, "、"))
	case SettleUnshared:
		return fmt.Sprintf(unsharedLine, who)
	case settleNoRequest:
		return noRequestLine
	case settleRouteUnknown:
		return routeUnknownLine
	case settleBroken:
		return fmt.Sprintf(brokenLine, m.note)
	}
	return ""
}

// ManualOptions は git を使えない呼び手（Chrome）のための指定。External ならホストの親のブランチを読んでいるので
// 共有済みとみなし、移動の時刻（epoch 秒）だけを MovedAt で受ける。
type ManualOptions struct {
	External bool
	MovedAt  *float64
}

// AssessManual は、このフェーズ（延期を引き受けた分を含む）の手動の移動を判定する。list は Phases の答え。nil なら、ここで読む。
// External で時刻が無ければ NeedTime を立てて返す。
// 読めない時刻（0 以下・NaN・無限大）は、確かめられないものとして settleBroken にする。
func AssessManual(conf *config.Settings, root string, parent *ticket.Ticket, p *Phase, list []*Phase, o ManualOptions) Manual {
	if p.has(marks.Reviewed) || !p.ReviewRequired() {
		return Manual{State: SettleNone}
	}
	if list == nil {
		list = Phases(conf, root, parent.ID, nil)
	}
	numbers := slices.Concat(p.Covers(), []int{p.Number})
	var children []*ticket.Ticket
	states := map[string]place.State{}
	ids := map[string]bool{}
	for _, q := range list {
		if !slices.Contains(numbers, q.Number) {
			continue
		}
		for _, t := range q.Tickets {
			if ids[t.ID] {
				return Manual{State: settleBroken, note: duplicateNote}
			}
			ids[t.ID] = true
			children = append(children, t)
			states[t.ID] = q.States[t.ID]
		}
	}
	var hand []*ticket.Ticket
	var unknown, waiting, names, paths []string
	reviewed := false
	for _, t := range children {
		switch states[t.ID] {
		case place.Done:
			switch {
			case t.FinishedTo == string(place.Review):
				reviewed = true
				if t.SettledBy == "" {
					hand = append(hand, t)
					names = append(names, t.ID)
					paths = append(paths, t.Path)
				}
			case t.FinishedTo == "" && t.SettledBy == "":
				unknown = append(unknown, t.ID)
			}
		case place.Review, place.Doing, place.Todo:
			// レビューを待つ側の子。取り消した子は数えない。
			waiting = append(waiting, t.ID)
		}
	}
	if len(hand) == 0 {
		if len(unknown) > 0 && len(waiting) == 0 && !reviewed {
			return Manual{State: settleRouteUnknown, Children: unknown}
		}
		return Manual{State: SettleNone}
	}
	if len(waiting) > 0 {
		return Manual{State: settlePartial, Children: names, waiting: waiting}
	}
	if !p.has(marks.Requested) {
		return Manual{State: settleNoRequest, Children: names}
	}
	if o.External {
		if o.MovedAt == nil {
			return Manual{State: SettleReady, Children: names, NeedTime: true, Paths: paths}
		}
		moved := *o.MovedAt
		if math.IsNaN(moved) || math.IsInf(moved, 0) || moved <= 0 {
			return Manual{State: settleBroken, Children: names, note: badTimeNote}
		}
		// コミットの時刻は書いた側の時計で決まる。未来にずれていれば今に抑える（移動の後の指摘を、移動の前の
		// ものとして受け入れないため）。
		at := fsio.FromUnixSeconds(moved)
		if now := time.Now(); at.After(now) {
			at = now
		}
		return Manual{State: SettleReady, Children: names, MovedAt: at}
	}
	at, why := shared(root, parent, hand)
	switch why {
	case "":
		return Manual{State: SettleReady, Children: names, MovedAt: at}
	case unsharedNote:
		return Manual{State: SettleUnshared, Children: names, note: why}
	}
	return Manual{State: settleBroken, Children: names, note: why}
}

// shared は動かした子の done/ のファイルが HEAD にコミット済みで、origin/<親のブランチ> に入っているか。入って
// いれば移動コミットのいちばん新しい時刻、そうでなければ理由（unsharedNote は未共有、ほかは読めなかった理由）。
func shared(root string, parent *ticket.Ticket, hand []*ticket.Ticket) (time.Time, string) {
	top := tree.WorktreePath(root, parent.ID)
	// 1. 親のワークツリーの先頭のブランチを読む。
	branch, _ := tree.BranchOf(top)
	if branch == "" {
		return time.Time{}, noBranchNote
	}
	// 2. 手で動かした子ごとに、done/ のファイルが（a）ツリーの中にあり、（b）未コミットの変更が無く、（c）追加したコミットが origin/<ブランチ> に入っていることを確かめる。1 つでも外れたら、理由を返して止める。
	newest := 0.0
	for _, t := range hand {
		rel, ok := "", false
		if t.Path != "" {
			rel, ok = ospath.Relpath(t.Path, top)
		}
		if !ok || rel == "" || strings.HasPrefix(rel, "..") {
			return time.Time{}, fmt.Sprintf(outsideNote, t.ID)
		}
		dirty := git.Status(top, git.Porcelain{Paths: []string{rel}}, git.Options{})
		if !dirty.OK() {
			return time.Time{}, noStatusNote
		}
		if strings.TrimSpace(dirty.Out) != "" {
			return time.Time{}, unsharedNote
		}
		log := git.Run(top, []string{"log", "--diff-filter=A", "--format=%ct %H", "-1", "--", rel}, git.Options{})
		if !log.OK() {
			return time.Time{}, noLogNote
		}
		fields := strings.Fields(log.Out)
		if len(fields) != 2 {
			return time.Time{}, unsharedNote
		}
		stamp, err := pyfmt.ParseFloat(fields[0])
		if err != nil {
			return time.Time{}, noLogNote
		}
		// origin に入っていない（祖先でない）ときと、origin を読めないときは、理由が別なので分ける。
		contained := git.IsAncestor(top, fields[1], "origin/"+branch, git.Options{})
		if contained.Code == git.NotAncestorCode {
			return time.Time{}, unsharedNote
		}
		if !contained.OK() {
			return time.Time{}, noRemoteNote
		}
		newest = max(newest, stamp)
	}
	// 3. 全部共有済み。移動コミットのいちばん新しい時刻を返す。
	return fsio.FromUnixSeconds(newest), ""
}
