package stop

import (
	"fmt"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// CodeRequestNudge はレビューの依頼までの道を促すときの理由コード。
const CodeRequestNudge = "NUDGE_TICKET_REQUEST"

// Step は依頼までの道の次の一手。値は文面ファイルの場合の名前（"-" は "_" に読み替える）。
type Step string

const (
	StepFinish  Step = "finish"
	StepMerge   Step = "merge"
	StepLateFix Step = "late-fix"
	StepRequest Step = "request"
	// StepConfirm はユーザが子を手で done/ に動かした段。親が confirm を打てば確定する。
	StepConfirm Step = "confirm"
)

// requestKey は記録のキーの頭（finish の促しのキーはチケットの識別子なので重ならない）。
const requestKey = "request:"

// Pending は依頼までの道の途中で促す相手。Children は一手の相手（finish を打つ子、合流を確かめる子）。
type Pending struct {
	Parent   *ticket.Ticket
	Number   int
	Step     Step
	Worktree string
	Head     string
	Children []string
	Title    string
}

// Key は親とフェーズ。
func (p *Pending) Key() string { return requestKey + p.Parent.ID + ":" + strconv.Itoa(p.Number) }

// Digest は一手の種類・相手・HEAD。進めば（子を閉じる、合流する、コミットを足す）変わる。
func (p *Pending) Digest() string {
	return string(p.Step) + ":" + strings.Join(p.Children, ",") + "@" + p.Head
}

// finishNudged は子のツリーで finish の促し（キーは子、値は HEAD）を同じ状態で出したか。同じ状態を 2 つの
// 促しが 1 回ずつ止めないため。
func (p *Pending) finishNudged(data *pyjson.Object) bool {
	return p.Step == StepFinish && len(p.Children) == 1 && seen(data, p.Children[0], p.Head)
}

// PendingAtStop は cwd のツリーが結び付く親が、依頼までの道の途中にいるなら返す。子のツリーなら自分の子が
// 作業中のとき、親のツリーなら作業中の子が残るか、子が全部閉じてレビューの要るフェーズに依頼がまだ無いとき。
// 依頼済み（requested が在る）のフェーズは、手で done/ に動かした子を確定できるときだけ confirm を促す。
// 親のワークツリーが決まらない・早めに閉じた親は対象にしない。raw は UnfinishedAtStop と同じ。
func PendingAtStop(conf *config.Settings, root, cwd string, raw *approval.Raw) *Pending {
	at, ok := here(conf, root, cwd)
	if !ok {
		return nil
	}
	copies, _ := approval.Scan(conf, root, false, raw)
	index := approval.ByID(copies)
	bound, ok := treeLookup(index, at.Name)
	if !ok {
		return nil
	}
	parent := parentOf(index, bound)
	if parent == nil {
		return nil
	}
	home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if err != nil || closedEarly(home, parent.ID) {
		return nil
	}
	head := HeadSHA(at.Root, zeroTime)
	if head == "" {
		return nil
	}
	// 子のツリーでは自分の子だけ（並行して動く別の子は別のセッションの作業）。
	var doing []*ticket.Ticket
	if bound.IsChild() {
		if bound.InProgress() {
			doing = append(doing, bound)
		}
	} else {
		for _, c := range copies {
			if c.Parent == parent.ID && c.InProgress() && c.Blocked == "" {
				doing = append(doing, c)
			}
		}
	}
	if len(doing) > 0 {
		slices.SortStableFunc(doing, func(a, b *ticket.Ticket) int { return strings.Compare(a.ID, b.ID) })
		number, first := 0, true
		ids := make([]string, len(doing))
		for i, t := range doing {
			ids[i] = t.ID
			if n, child := t.ChildPhase(); child && (first || n < number) {
				number, first = n, false
			}
		}
		return &Pending{parent, number, StepFinish, at.Root, head, ids, ""}
	}
	if bound.IsChild() {
		return nil
	}
	list := phase.Phases(conf, root, parent.ID, raw)
	for _, ph := range list {
		// 終わっていて、レビューが要り、まだ済んでいないフェーズ。
		if !ph.GateClosed() {
			continue
		}
		if _, requested := ph.Marks[marks.Requested]; requested {
			// 依頼のあとにユーザが子を手で done/ に動かして共有した。confirm で確定する。
			if m := phase.AssessManual(conf, root, parent, ph, list, phase.ManualOptions{}); m.State == phase.SettleReady {
				return &Pending{parent, ph.Number, StepConfirm, at.Root, head, m.Children, ph.Title()}
			}
			continue
		}
		step, children := nextStep(root, at.Root, ph)
		return &Pending{parent, ph.Number, step, at.Root, head, children, ph.Title()}
	}
	return nil
}

// nextStep は依頼の前の道のどこにいるか。合流済みでない子があれば合流の確認、合流後に子のブランチへ積んだ
// 修正が親に無ければその確認、無ければ依頼。合流済みは finish が書いた merged_sha が親の HEAD の祖先であること
// （依頼の前提の検査と同じ確認）。
func nextStep(root, worktree string, ph *phase.Phase) (Step, []string) {
	var unmerged, late []string
	children := slices.Clone(ph.Tickets)
	slices.SortStableFunc(children, func(a, b *ticket.Ticket) int { return strings.Compare(a.ID, b.ID) })
	for _, child := range children {
		if s := ph.States[child.ID]; s != place.Review && s != place.Done {
			continue
		}
		merged := child.MergedSHA != "" && git.Run(worktree,
			[]string{"merge-base", "--is-ancestor", child.MergedSHA, "HEAD"}, git.Options{Timeout: closing.GitTimeout}).OK()
		if !merged {
			unmerged = append(unmerged, child.ID)
		} else if closing.UnmergedFix(root, worktree, child, closing.GitTimeout) != "" {
			late = append(late, child.ID)
		}
	}
	switch {
	case len(unmerged) > 0:
		return StepMerge, unmerged
	case len(late) > 0:
		return StepLateFix, late
	}
	return StepRequest, nil
}

// RequestNudge は Stop を止めて渡す文。段ごとの文は文面ファイルが持ち、子の finish だけは子ごとのコマンドを並べて渡す。
func RequestNudge(root string, p *Pending) string {
	n := strconv.Itoa(p.Number)
	ticketSh := config.ScriptCommand(root, "ccnavi-ticket.sh")
	name := "フェーズ " + n
	if p.Title != "" {
		name += "（" + p.Title + "）"
	}
	cmds := make([]string, len(p.Children))
	for i, c := range p.Children {
		cmds[i] = "`" + ticketSh + " finish " + c + "`"
	}
	step := p.Step
	switch step {
	case StepFinish, StepMerge, StepLateFix, StepRequest, StepConfirm:
	default:
		step = StepRequest
	}
	which := strings.ReplaceAll(string(step), "-", "_")
	said := texts.Say(root, "NUDGE_TICKET_REQUEST", texts.Values{
		"parent":         p.Parent.ID,
		"phase":          n,
		"phase_name":     name,
		"children":       strings.Join(p.Children, ", "),
		"finish_cmds_q":  strings.Join(cmds, "、"),
		"user_stop_note": UserStopNote,
	}, texts.Variants{"what": which, "next": which})
	return fmt.Sprintf("[ccnavi] %s (parent: %s, phase: %s)\n%s", CodeRequestNudge, p.Parent.ID, n, said)
}
