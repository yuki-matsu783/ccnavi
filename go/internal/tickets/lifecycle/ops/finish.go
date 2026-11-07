package ops

import (
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/learning"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/sync/drafts"
)

// ReadyFlowAgreed は最後のレビューのあと、Draft を外すまでの手順がユーザの合意の範囲に入ることの言い切り。
// confirm の知らせと親の finish の出力が同じ文を使う。ユーザがレビュー済みにした時点でここまでは合意済みで、
// 外へ出る操作の前に確認を取り直すと、同じ流れの中で何度も許可を求めることになる。
const ReadyFlowAgreed = "ここから Draft を解除するまで（finish → push → ready。`wip/` の削除は ready に入っている）は、" +
	"ユーザがレビューして合意した範囲に入る。" +
	"push と Draft を解除する前に、ユーザへ確認を取り直さずに進める" +
	"（Claude Code の自動モードの分類器に止められたときだけ、ユーザに許可を頼む）"

const (
	reviewScript = "ccnavi-review.sh"

	textFinishWipDeliverables = "NOTE_OPS_FINISH_WIP_DELIVERABLES"
	textCloseMR               = "NEXT_OPS_CLOSE_MR"
	textChildDone             = "NEXT_OPS_CHILD_DONE"
)

// noDeadline は期限なし（stop.HeadSHA に渡す）。
var noDeadline time.Time

func gitOptions() git.Options { return git.Options{Timeout: closing.GitTimeout} }

// ReadyCommand は Draft を外す 1 本。親のワークツリーの外から打つ形（`--parent`）で、最後に親のワークツリーも消える。
func ReadyCommand(root, parentID string) string {
	return config.ScriptCommand(root, reviewScript) + " ready --parent " + parentID
}

// Finish は doing/ から review/（レビュー要）か done/（不要）へ動かし、完了の時刻を書く。子は閉じる前に実績の
// リスクを数え、定性の判定が揃わなければ閉じない。成果物の追跡と親の学びの置き場は、子の合流のあとでないと
// 分からないので finishGate の後に見る。
func Finish(stdout, stderr io.Writer, conf *config.Settings, root, id string) int {
	found := finishGate(stderr, conf, root, id)
	if found == nil {
		return 1
	}
	if closing.DeliverablesMissing(stderr, conf, root, found) {
		return 1
	}
	// 親は、学び以外の検査をすべて通したあとに、親の今の HEAD で気づきの処理の証跡を照合する。
	if learning.ParentBlocked(stderr, conf, root, found) {
		return 1
	}
	scored, ok := scoreChild(stderr, conf, root, found)
	if !ok {
		return 1
	}
	completed := fsio.Stamp()
	fields := [][2]string{{"completed_at", completed}}
	if merged := mergedSHA(root, found); merged != "" {
		fields = append(fields, [2]string{"merged_sha", merged})
	}
	state := place.Done
	if needsReview(conf, root, found) {
		state = place.Review
	}
	// 置き場の状態だけでは、あとから「review 行きだったか」は求まらない（review_required は実績のリスクなどで
	// 変わる）。置いた先を残し、手で動かしたかどうかの判定（settled_by の有無）の根拠にする。
	fields = append(fields, [2]string{"finished_to", string(state)})
	code := move(stdout, stderr, conf, root, found, state, fields, "完了 "+completed, true)
	if code != 0 {
		return code
	}
	for _, line := range scored {
		io.WriteString(stdout, line+"\n")
	}
	if inWip := closing.DeliverablesInWip(conf, root, found); len(inWip) > 0 {
		io.WriteString(stdout, texts.Say(root, textFinishWipDeliverables, texts.Values{"paths": strings.Join(inWip, "、")}, nil)+"\n")
	}
	if !found.IsChild() {
		closeParent(stdout, stderr, conf, root, found)
	}
	return 0
}

// mergedSHA はこの finish で親のブランチに入る子の先頭。合流の途中なら MERGE_HEAD（finish の検査が読んだ子の
// 先頭。検査のあとに子が積んだコミットは合流に載らない）。合流の途中でなくても、子の先頭が親の先頭の祖先なら
// （子が先に親へ入っている）その先頭。どちらでもなければ親のブランチに入らない子なので空。
func mergedSHA(root string, found *ticket.Ticket) string {
	if !found.IsChild() {
		return ""
	}
	if merging := mergingHead(root, found); merging != "" {
		return merging
	}
	childTree := tree.WorktreePath(root, found.ID)
	head := git.Head(childTree, 0)
	if head == "" {
		return ""
	}
	parentHead := git.Head(tree.WorktreePath(root, found.Parent), 0)
	if parentHead == "" {
		return ""
	}
	if git.Run(childTree, []string{"merge-base", "--is-ancestor", head, parentHead}, gitOptions()).OK() {
		return head
	}
	return ""
}

// mergingHead は親のワークツリーが合流している子の先頭（MERGE_HEAD）。合流の途中でなければ空。MERGE_HEAD が
// 子のブランチの先頭かその祖先のときだけ採る。親のワークツリーで別のものを合流している途中（write-and-push の
// 外の手作業）を、この子の合流と取り違えないため。
func mergingHead(root string, found *ticket.Ticket) string {
	if !found.IsChild() {
		return ""
	}
	parentTree := tree.WorktreePath(root, found.Parent)
	rc, out := git.Output(parentTree, []string{"rev-parse", "--verify", "--quiet", "MERGE_HEAD"}, gitOptions())
	merging := ""
	if rc == 0 {
		merging = ospath.Strip(out)
	}
	head := git.Head(tree.WorktreePath(root, found.ID), 0)
	if merging == "" || head == "" {
		return ""
	}
	if git.Run(parentTree, []string{"merge-base", "--is-ancestor", merging, head}, gitOptions()).OK() {
		return merging
	}
	return ""
}

// needsReview はこの子を閉じたとき、ユーザが見る対象になるか。フェーズのレビューを、この子を閉じたものとして
// 数え直す（実績のリスクは scoreChild が記録した直後なので、宣言に関わらず上がった分も入る）。延期したフェーズの
// 子もレビュー待ちに置く。見るのは次にレビューがあるフェーズの番だが、ユーザが見るまでは見られていない。
func needsReview(conf *config.Settings, root string, found *ticket.Ticket) bool {
	number, child := found.ChildPhase()
	if !child || found.IsConsolidate() {
		// 統合の子は必ずレビュー不要。
		return false
	}
	for _, ph := range phase.Phases(conf, root, found.Parent, nil) {
		if ph.Number != number {
			continue
		}
		ph.States[found.ID] = place.Done
		return ph.Deferred() || ph.ReviewRequired()
	}
	return found.ReviewRequired != ticket.Unneeded
}

// closeParent は親を閉じたあとの記録と案内。記録（closed.json）はどのフェーズをどこで見たかを親のブランチに残す
// （提案は統合先に取り込む前に wip/ ごと消えるので、閉じた事実の残る先がここしか無い）。案内は Draft を外す合図
// まで。ready は閉じたチケットとこの記録を手元の退避へ移してから外す。
func closeParent(stdout, stderr io.Writer, conf *config.Settings, root string, found *ticket.Ticket) {
	where, err := approval.HomeDir(conf, root, found.ID, "", found.Project)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: クローズ済みの記録を保存できませんでした: %s\n", err)
		return
	}
	reviews := pyjson.NewObject()
	for _, ph := range phase.Phases(conf, root, found.ID, nil) {
		reviews.Set(strconv.Itoa(ph.Number), ph.ReviewRequired())
	}
	if failed := marks.WriteParent(where, found.ID, marks.Closed, pyjson.NewObject("reviews", reviews)); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: クローズ済みの記録を保存できませんでした: %s\n", failed)
	}
	if mark, ok := marks.ReadParent(where, found.ID, marks.Ready); ok && mark.Len() > 0 {
		// ready は印を置いたあとに done/ を退避するので、材料は拾えず案内も空になる。ここに来るのは、退避が途中で
		// 止まって ready の印が残ったまま、作業中の親を finish したときだけ。
		io.WriteString(stdout, "Draft は外してある。マージはユーザが行う\n")
		return
	}
	io.WriteString(stdout, texts.Say(root, textCloseMR, texts.Values{
		"ready_command": ReadyCommand(root, found.ID),
		"approved":      conf.Approved,
		"wip":           place.WipRoot,
		"flow_agreed":   ReadyFlowAgreed,
	}, nil)+"\n")
	// 材料は ready で退避されるので、ready の前の今しか拾えない。出力に足すだけで、判定は変えない。
	io.WriteString(stdout, drafts.CloseNotice(conf, root, found.ID))
}

// move は doing/ の承認済みチケットに欄を書き、review/ か done/ へ動かす。advise が偽なら、子が done/ に入った
// あとの `次:` を出さない（close-early が未着手の子をまとめて取り消すとき、子の数だけ並ぶため）。
func move(stdout, stderr io.Writer, conf *config.Settings, root string, found *ticket.Ticket, state place.State, fields [][2]string, said string, advise bool) int {
	if failed := approval.UpdateFields(found.Path, fields); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: %s に欄を保存できませんでした: %s\n", found.ID, failed)
		return 1
	}
	where := ospath.Dirname(ospath.Dirname(found.Path))
	var failed, at string
	if state == place.Review {
		failed = approval.ToReview(where, found.TreeRoot, conf.Tickets, found.ID)
		at = conf.Tickets + "/" + string(place.Review) + "/"
	} else {
		failed = approval.CloseCopy(where, found.ID)
		at = conf.Approved + "/" + string(place.Done) + "/"
	}
	if failed != "" {
		fmt.Fprintf(stderr, "ccnavi: %s: %s\n", found.ID, failed)
		return 1
	}
	fmt.Fprintf(stdout, "OK: %s を %s へ動かした（%s）\n", found.ID, at, said)
	switch {
	case state == place.Review:
		// 依頼の前に「ユーザのレビューを待つ」と言わない。次に誰が何をするかを依頼の有無で言い分ける。
		moved := *found
		moved.CompletedAt = fieldOf(fields, "completed_at")
		next := phase.ReviewNext(conf, root, &moved, nil)
		if next == "" {
			next = "まだレビュー準備中。親がレビューを依頼するまで、ユーザには回らない。" +
				"依頼の後、ユーザがレビューを終えて confirm / decide / --reviewed か " +
				"Chrome 拡張のレビュー済みを実行すると " + conf.Approved + "/" + string(place.Done) + "/ へ動く"
		}
		io.WriteString(stdout, next+"\n")
	case state == place.Done && found.IsChild() && advise:
		io.WriteString(stdout, texts.Say(root, textChildDone, texts.Values{"parent": found.Parent}, nil)+"\n")
	}
	return 0
}

func fieldOf(fields [][2]string, key string) string {
	for _, f := range fields {
		if f[0] == key {
			return f[1]
		}
	}
	return ""
}
