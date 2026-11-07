package stop

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// CodeFeedbackNudge はフィードバック計画を促すときの理由コード。
const CodeFeedbackNudge = "NUDGE_TICKET_FEEDBACK"

// feedbackKey は記録のキーの頭。
const feedbackKey = "feedback:"

// Waiting はフィードバック計画待ちで促す相手。todo は提案の置き場の絶対パス、proposal は改版の提案（無ければ空）。
type Waiting struct {
	Parent   *ticket.Ticket
	head     string
	todo     string
	proposal string
}

// Key は `feedback:<親>`。
func (w *Waiting) Key() string { return feedbackKey + w.Parent.ID }

// Digest は親・HEAD・改版を提案済みか。HEAD が進むか、提案済みに変われば、また促す。
func (w *Waiting) Digest() string {
	state := "none"
	if w.proposal != "" {
		state = "proposed"
	}
	return w.Parent.ID + "@" + w.head + ":" + state
}

// parentOf は cwd のツリーに結び付く承認済みチケットの、作業中で着手済みの親。子のツリーならその親。
func parentOf(index map[string]*ticket.Ticket, bound *ticket.Ticket) *ticket.Ticket {
	parent := bound
	if bound.IsChild() {
		parent = index[bound.Parent]
	}
	if parent == nil || parent.IsChild() || parent.Blocked != "" || !parent.InProgress() {
		return nil
	}
	return parent
}

// closedEarly はユーザが早めに閉じた親か。
func closedEarly(home, parentID string) bool {
	_, ok := marks.ReadFilledParent(home, parentID, marks.CloseEarly)
	return ok
}

// WaitingAtStop は cwd のツリーが結び付く親が、フィードバック計画待ちの局面で止まっているなら返す。
// 親のワークツリーが決まらない・早めに閉じた親は対象にしない。
func WaitingAtStop(conf *config.Settings, root, cwd string, raw *approval.Raw) *Waiting {
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
	st := standing.NewFamilies(conf, root).Standing(parent.ID, parent.Project)
	if st.Home == nil || st.Stop != "" {
		return nil
	}
	if closedEarly(conf.ApprovedDir(st.Home.Root), parent.ID) {
		return nil
	}
	if phase.Stage(conf, root, parent, raw) != phase.StageFeedbackWaiting {
		return nil
	}
	head := git.Head(at.Root, git.Options{})
	if head == "" {
		return nil
	}
	todo := place.Dir(st.Home.Root, conf.Tickets, place.Todo)
	return &Waiting{parent, head, todo, proposed(todo, parent.ID)}
}

// proposed は提案の置き場に、親と同じ識別子で feedback: がある提案があればそのファイル。
func proposed(todo, id string) string {
	names, err := ospath.DiskListdir(todo)
	if err != nil {
		return ""
	}
	for _, name := range names {
		if !strings.HasSuffix(name, config.MdExt) {
			continue
		}
		path := ospath.Join(todo, name)
		if t, _ := ticket.Load(path); t != nil && t.ID == id && t.FeedbackPlanned {
			return path
		}
	}
	return ""
}

// FeedbackNudge は Stop を止めて渡す文。改版が提案済みかで言うことを分ける。
func FeedbackNudge(root string, w *Waiting) string {
	next := "drafting"
	if w.proposal != "" {
		next = "proposed"
	}
	said := texts.Say(root, "NUDGE_TICKET_FEEDBACK", texts.Values{
		"parent":         w.Parent.ID,
		"stage":          phase.StageFeedbackWaiting,
		"proposal":       w.proposal,
		"todo":           w.todo,
		"user_stop_note": userStopNote,
	}, texts.Variants{"next": next})
	return hookio.Tagged(CodeFeedbackNudge, "parent: "+w.Parent.ID) + "\n" + said
}
