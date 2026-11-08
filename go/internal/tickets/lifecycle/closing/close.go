package closing

import (
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// ParentStillBusy は親を閉じられない理由（CloseProblems）があれば、全部言って真。子なら偽。
func ParentStillBusy(stderr io.Writer, conf *config.Settings, root string, t *ticket.Ticket) bool {
	if t.IsChild() {
		return false
	}
	problems := CloseProblems(conf, root, t.ID, nil)
	for _, p := range problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", p)
	}
	return len(problems) > 0
}

// CloseProblems は、親を閉じられない理由を返す。空なら閉じてよく、マージに進んでもよい。ユーザが早めに閉じていれば、開いている子以外は問わない。
// raw は、呼び手が approval.ReadRaw で読んだ置き場である。読んでから動かしていないときだけ渡す。nil ならここで読む。
func CloseProblems(conf *config.Settings, root, parentID string, raw *approval.Raw) []string {
	// 1. 開いている子（承認済みの doing/ などにある子）が残っていないか。
	copies, _ := approval.Scan(conf, root, false, raw)
	var open []string
	for _, t := range copies {
		if t.Parent == parentID {
			open = append(open, t.ID)
		}
	}
	if len(open) > 0 {
		return []string{fmt.Sprintf("%s には開いている子がある（%s）。子を先に閉じてください", parentID, strings.Join(open, ", "))}
	}
	// 2. 早めに閉じる操作（close-early）の印があれば、残りの検査は通さない（残りを承知で閉じる操作のため）。
	var home string
	if own := approval.ByID(copies)[parentID]; own != nil {
		home = approval.HomeDirRead(conf, root, parentID, "", own.Project)
	} else {
		home = approval.HomeDirReadAny(conf, root, parentID, "")
	}
	if _, ok := marks.ReadFilledParent(home, parentID, marks.CloseEarly); ok {
		return nil
	}
	// 3. 親の finish が求める条件。レビュー待ちのフェーズ、フィードバック計画（無くても `feedback: []` を承認済みにする）、終わっていないフェーズ。
	var problems []string
	if held := phase.HeldPhase(conf, root, parentID, raw); held != nil {
		problems = append(problems, fmt.Sprintf("%s のフェーズ %s は%s。レビューを済ませてから", parentID, held.Label(), held.ReviewLabel()))
	}
	closed, _ := approval.Scan(conf, root, true, raw)
	if c := approval.ByID(slices.Concat(copies, closed))[parentID]; c != nil && c.HasPlan() {
		if !c.FeedbackPlanned {
			revise := config.ScriptCommand(root, config.LauncherScript) + " ticket revise " + parentID + " --feedback"
			problems = append(problems, fmt.Sprintf("%s はフィードバック計画がまだ。対応が無くても `feedback: []` の改版を"+
				"出して承認を受けてから（雛形は '%s' が作る）", parentID, revise))
		}
		var unfinished []string
		for _, p := range phase.Phases(conf, root, parentID, raw) {
			if !p.Ended() {
				unfinished = append(unfinished, p.Label())
			}
		}
		if len(unfinished) > 0 {
			problems = append(problems, fmt.Sprintf("%s には終わっていないフェーズがある（%s）。全部閉じてから", parentID, strings.Join(unfinished, ", ")))
		}
	}
	return problems
}

// PhaseType は子のフェーズの定義。親に固定したコピー（phases:）から読み、phases.yml は読まない（承認の
// あとに直しても進行中のフェーズの成果物は変わらない）。子でない・計画が無い・定義が無いなら nil。
func PhaseType(conf *config.Settings, root string, t *ticket.Ticket) *phases.PhaseType {
	number, child := t.ChildPhase()
	if !child {
		return nil
	}
	copies, _ := approval.Scan(conf, root, false, nil)
	parent := approval.ByID(copies)[t.Parent]
	if parent == nil || !parent.HasPlan() {
		return nil
	}
	item := parent.ItemAt(number)
	if item == nil {
		return nil
	}
	return phases.TypesOf(parent).Get(item.Type)
}

// DeliverablesInWip は定義の成果物のうち wip/ の下に置くもの。ready が wip/ ごと消すので MR に残らない。
// 止めない（調査のように範囲が wip/ だけのフェーズは、結論の置き場がここしか無い）。
func DeliverablesInWip(conf *config.Settings, root string, t *ticket.Ticket) []string {
	pt := PhaseType(conf, root, t)
	if pt == nil {
		return nil
	}
	var out []string
	for _, g := range pt.Deliverables {
		if place.InWip(g) {
			out = append(out, g)
		}
	}
	return out
}

// DeliverablesMissing は、フェーズの最後の子を閉じる前に、定義の成果物が親か子のワークツリーで追跡されていなければ、その名前を言って真を返す。
// 同じフェーズに作業中の別の子があれば、その子が成果物を出すかもしれないので通す。成果物は在って追跡されていることだけを見る。空でも在れば通る。
func DeliverablesMissing(stderr io.Writer, conf *config.Settings, root string, t *ticket.Ticket) bool {
	pt := PhaseType(conf, root, t)
	if pt == nil || len(pt.Deliverables) == 0 {
		return false
	}
	for _, ph := range phase.Phases(conf, root, t.Parent, nil) {
		if ph.Number != t.Phase {
			continue
		}
		for _, other := range ph.Tickets {
			if other.ID != t.ID && ph.States[other.ID] == place.Doing {
				return false
			}
		}
	}
	parentTree := tree.WorktreePath(root, t.Parent)
	childTree := tree.WorktreePath(root, t.ID)
	var missing []string
	for _, g := range pt.Deliverables {
		if !tracked(parentTree, g) && !tracked(childTree, g) {
			missing = append(missing, g)
		}
	}
	if len(missing) == 0 {
		return false
	}
	fmt.Fprintf(stderr, "ccnavi: フェーズ %d（%s）の成果物が無い: %s。"+
		"親か子のワークツリーに置いて追跡（git add）してから閉じてください\n", t.Phase, pt.Title, strings.Join(missing, ", "))
	return true
}

// tracked は、この glob に当たる追跡済みのファイルが、ワークツリーにも 1 つでも在るかを返す。ls-files はインデックスを見るので、消したが git add していないもの（--deleted）は除く。
// 名前は同じ読み方の 2 つの出力どうしで比べ、ファイルシステムには聞かない。非 UTF-8 の名前を置換文字で読んでも、食い違わないようにするため。
func tracked(worktree, glob string) bool {
	rc, out := git.Output(worktree, []string{"ls-files", "-z", "--", glob}, git.Options{})
	if rc != 0 {
		return false
	}
	rc, gone := git.Output(worktree, []string{"ls-files", "-z", "--deleted", "--", glob}, git.Options{})
	if rc != 0 {
		return false
	}
	deleted := map[string]bool{}
	for _, name := range strings.Split(gone, "\x00") {
		deleted[name] = true
	}
	for _, name := range strings.Split(out, "\x00") {
		if name != "" && !deleted[name] {
			return true
		}
	}
	return false
}
