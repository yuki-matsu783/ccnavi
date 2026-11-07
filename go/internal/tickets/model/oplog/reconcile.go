package oplog

import (
	"fmt"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
)

// Verdict は送信の照合の結論。
type Verdict string

const (
	// verdictSent は送ったと確かめた（URL を保存する）。
	verdictSent Verdict = "sent"
	// verdictSend は送っていないと確定した（同じ段階 ID で送る）。
	verdictSend Verdict = "send"
	// verdictUnknown は送ったか確定できない（自動では送らない）。
	verdictUnknown Verdict = "unknown"
	// verdictStop は複数ある・読めない（止めて、確かめ方を表示する）。
	verdictStop Verdict = "stop"
)

// Listing はホストの一覧をどこまで読めたか。
type Listing int

const (
	// ListedAll は上限で打ち切らずに最後まで読めた。
	ListedAll Listing = iota
	// ListedPart は上限で打ち切った。
	ListedPart
	// ListFailed は読めなかった。
	ListFailed
)

// recentWindow は、作成直後は一覧への反映が遅れうるので 0 件でも未送信と確定しない長さ。
const recentWindow = 120 * time.Second

// atChars は段階の時刻（StampLayout）のうち読む先頭 19 字（LocalStampLayout）。時差は読まず手元の時刻として読む
// （Python の mktime）。
const (
	atChars = 19
)

const (
	whyNoStage   = "段階 %s が記録に無い"
	whyNoList    = "ホストの一覧を読めなかった"
	whyMany      = "目印の一致が %d 件ある（重複して送られている）"
	whyOtherUser = "送ったアカウント（%s）と今のトークンの持ち主（%s）が違う"
	whyPartial   = "一覧を上限で打ち切ったので、0 件とは言い切れない"
	whyRecent    = "送ってから間がなく、ホストの一覧に反映されていないかもしれない"
)

// Reconcile は送信の照合。found は目印（`ccnavi:op=<操作ID> stage=<段階ID>`）が一致し、送ったアカウントの
// 作成だったものの URL、account は今のトークンの持ち主。結論と理由を返す。
//
// 0 件を「送っていない」と言い切るのは、最後まで読めて、送ったアカウントと同じで、作成直後でないときだけ。
func Reconcile(r *Record, stageID string, found []string, listing Listing, account string, at time.Time) (Verdict, string) {
	s := r.stage(stageID)
	if s == nil {
		return verdictStop, fmt.Sprintf(whyNoStage, stageID)
	}
	if listing == ListFailed {
		return verdictStop, whyNoList
	}
	if len(found) > 1 {
		return verdictStop, fmt.Sprintf(whyMany, len(found))
	}
	if len(found) == 1 {
		return verdictSent, ""
	}
	if s.account != "" && account != "" && s.account != account {
		return verdictUnknown, fmt.Sprintf(whyOtherUser, s.account, account)
	}
	if listing == ListedPart {
		return verdictUnknown, whyPartial
	}
	tried, err := time.ParseInLocation(fsio.LocalStampLayout, pyfmt.Head(s.at, atChars), time.Local)
	if s.tries != 0 && err == nil && at.Sub(tried) < recentWindow {
		return verdictUnknown, whyRecent
	}
	return verdictSend, ""
}
