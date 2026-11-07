package handoff

import (
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/tickets/flow"
)

// shortList は名指しする件数（error の対象）。
const shortList = 10

const (
	msgNothing      = "取り込む対象がありません"
	msgBoardKind    = "%s の種類で"
	msgBoardDefense = "%sので"
	msgBoardRefused = "%s は %s、ボードからは取り込めない。" +
		"ターミナルで `ccnavi-user-handoff.sh <パス> --yes --expect <ハッシュ>` を打つ"
	msgNotOK        = "選んだ対象に error がある: %s。何も書いていません"
	msgNoExpect     = "--yes には --expect <ハッシュ>（見るだけの形が示す値）を付けてください"
	msgExpectDiffer = "--expect が今の対象のハッシュと違う（見せたあとに、下書きか取り込み先が変わった）。" +
		"何も書いていません。今のハッシュ: %s"
	msgRolledBack   = "%s。書いたものは元に戻しました。何も取り込んでいません"
	msgNotRolled    = "%s。書いたもののうち、元に戻せなかったものがある: %s。手で確かめてください"
	msgWayLinked    = "%s: 取り込み先の途中がリンクに変わった"
	msgTargetNoGo   = "%s: 取り込み先には書かない（%s）"
	msgTargetMoved  = "%s: 見せたあとに取り込み先が変わった"
	msgDraftMoved   = "%s: 見せたあとに下書きが変わった"
	msgWriteFailed  = "%s を書けない（%s）"
	msgReadBackBad  = "%s を書いたが、読み直すと中身が違う（%s）"
	msgReadBackWhat = "内容"
	msgKeptUnread   = "下書きを読めなかったので消していない（%s）"
	msgKeptRewrote  = "取り込んだあとに下書きが書き直されているので消していない"
	msgKeptNoUnlink = "下書きを消せない（%s）"
)

// Applied は取り込みの結果。OK が偽なら Error が理由で、何も書いていない（戻せなかったものは Error が名指しする）。
type Applied struct {
	OK            bool
	Error         string
	Applied       []*Entry
	RemovedDrafts []string
	KeptDrafts    []Kept
}

// Kept は消さずに残した下書き（ツリーからの相対パス）と理由。
type Kept struct{ Path, Reason string }

func failed(text string) *Applied { return &Applied{Error: text} }

// boardRefused はボードの経路で取り込めない対象を選んでいれば、その断り文。
func boardRefused(plan *Plan) string {
	var blocked []*Entry
	for _, e := range plan.Selected() {
		if !e.Board() {
			blocked = append(blocked, e)
		}
	}
	if len(blocked) == 0 {
		return ""
	}
	paths := make([]string, len(blocked))
	for i, e := range blocked {
		paths[i] = e.Rel
	}
	first := blocked[0]
	why := sprintf(msgBoardDefense, first.Defense)
	if !first.Kind.onBoard {
		why = sprintf(msgBoardKind, first.Kind.ID)
	}
	return sprintf(msgBoardRefused, strings.Join(paths, ", "), why)
}

// notOKReason は選んだ対象に error があるときの、名指しの文。
func notOKReason(plan *Plan) string {
	if plan.Error != "" {
		return plan.Error
	}
	var named []string
	for _, e := range plan.Selected() {
		if e.State() == stateRejected && len(named) < shortList {
			named = append(named, e.Rel+"（"+e.Errors[0]+"）")
		}
	}
	return sprintf(msgNotOK, strings.Join(named, "、"))
}

// Apply は選んだ対象を書く。plan が通り、expect が今の照合の値と同じときだけ書く。途中で失敗したら書いたものを
// 書く前のバイト列へ戻す。書けたら、今のバイト列が取り込んだものと同じ下書きだけを消す。
func Apply(plan *Plan, expect string, viaBoard bool) *Applied {
	if plan.Error != "" {
		return failed(plan.Error)
	}
	chosen := sortedByKey(plan.Selected())
	if len(chosen) == 0 {
		return failed(msgNothing)
	}
	if viaBoard {
		if refused := boardRefused(plan); refused != "" {
			return failed(refused)
		}
	}
	if !plan.OK() {
		return failed(notOKReason(plan))
	}
	if expect == "" {
		return failed(msgNoExpect)
	}
	if now := plan.Expect(); expect != now {
		return failed(sprintf(msgExpectDiffer, now))
	}
	var written []*Entry
	failure := ""
	for _, e := range chosen {
		if e.State() == stateUnchanged {
			continue
		}
		var wrote bool
		failure, wrote = writeOne(plan.Tree, e)
		if wrote {
			written = append(written, e)
		}
		if failure != "" {
			break
		}
	}
	if failure != "" {
		if undone := rollBack(written); len(undone) > 0 {
			return failed(sprintf(msgNotRolled, failure, strings.Join(undone, ", ")))
		}
		return failed(sprintf(msgRolledBack, failure))
	}
	done := &Applied{OK: true, Applied: written}
	for _, e := range chosen {
		cleanDraft(plan.Tree, e, done)
	}
	return done
}

// readBack は書いたあとの読み直し（テストが失敗を作るために差し替える）。
var readBack = flow.ReadBytes

// writeOne は 1 本を書く。書く直前にリンクと取り込み先のハッシュを確かめ直す。失敗なら理由。wrote は取り込み先を
// 書き換えたか。読み直しで失敗したときも真で、呼び手はその 1 本も元に戻す。
func writeOne(root string, e *Entry) (failure string, wrote bool) {
	if pathmatch.HasLink(root, e.target) || !inside(root, e.target) {
		return sprintf(msgWayLinked, e.Rel), false
	}
	var now []byte
	if ospath.DiskLexists(e.target) {
		var why string
		if now, why = flow.ReadBytes(e.target, root); now == nil {
			return sprintf(msgTargetNoGo, e.Rel, why), false
		}
	}
	if contentDigest(now) != contentDigest(e.current) {
		return sprintf(msgTargetMoved, e.Rel), false
	}
	if draft, _ := flow.ReadBytes(e.draft, root); !same(draft, e.raw) {
		return sprintf(msgDraftMoved, e.Rel), false
	}
	how := fsio.Durable
	if e.current == nil {
		how = fsio.NewDurable
	}
	if err := fsio.WriteFile(e.target, e.raw, how); err != "" {
		return sprintf(msgWriteFailed, e.Rel, line(err)), false
	}
	if back, why := readBack(e.target, root); !same(back, e.raw) {
		if why == "" {
			why = msgReadBackWhat
		}
		return sprintf(msgReadBackBad, e.Rel, why), true
	}
	return "", true
}

// rollBack は書いたものを書く前のバイト列に戻す。戻せなかったものの相対パス。
func rollBack(written []*Entry) []string {
	var undone []string
	for i := len(written) - 1; i >= 0; i-- {
		e := written[i]
		var err string
		if e.current == nil {
			err = fsio.Unlink(e.target)
		} else {
			err = fsio.WriteFile(e.target, e.current, fsio.Durable)
		}
		if err != "" {
			undone = append(undone, e.Rel)
		}
	}
	return undone
}

// cleanDraft は下書きの今のバイト列が取り込んだものと同じときだけ、下書きを消す。
func cleanDraft(root string, e *Entry, done *Applied) {
	now, why := flow.ReadBytes(e.draft, root)
	switch {
	case now == nil:
		done.KeptDrafts = append(done.KeptDrafts, Kept{e.draftRel(), sprintf(msgKeptUnread, why)})
	case !same(now, e.raw):
		done.KeptDrafts = append(done.KeptDrafts, Kept{e.draftRel(), msgKeptRewrote})
	default:
		if err := fsio.Unlink(e.draft); err != "" {
			done.KeptDrafts = append(done.KeptDrafts, Kept{e.draftRel(), sprintf(msgKeptNoUnlink, line(err))})
			return
		}
		done.RemovedDrafts = append(done.RemovedDrafts, e.draftRel())
	}
}
