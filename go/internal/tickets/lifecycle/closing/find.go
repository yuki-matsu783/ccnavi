package closing

import (
	"fmt"
	"io"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

// GitTimeout は git を読むときの待ち時間。Stop の促しと状態の操作の git の読み取りも使う。
const GitTimeout = 5 * time.Second

// ticketScript は案内に出す状態の操作の sh。
const ticketScript = "ccnavi-ticket.sh"

// workspaceLabel は在り処を並べるときのワークスペースルートの呼び名。
const workspaceLabel = "(ワークスペースルート)"

func gitOptions() git.Options { return git.Options{Timeout: GitTimeout} }

// UnmergedFix は finish の後に子のブランチへ積まれ、親（treeRoot の HEAD）に入っていないコミットの先頭。
// 無ければ空。子のワークツリーが無い PC（別の PC で finish した子など）では判定しない。
func UnmergedFix(root, treeRoot string, child *ticket.Ticket, timeout time.Duration) string {
	if !ospath.IsDir(tree.WorktreePath(root, child.ID)) {
		return ""
	}
	o := git.Options{Timeout: timeout}
	rc, out := git.Output(treeRoot, []string{"rev-parse", "--verify", "--quiet", "refs/heads/" + child.ID}, o)
	head := ""
	if rc == 0 {
		head = ospath.Strip(out)
	}
	if head == "" || head == child.MergedSHA {
		return ""
	}
	if rc, _ := git.Output(treeRoot, []string{"merge-base", "--is-ancestor", head, "HEAD"}, o); rc == 0 {
		return ""
	}
	return head
}

// found はこの識別子のチケットが在る置き場の全部と、読めなかった理由・提案の不備。
type found struct {
	hits     []*ticket.Ticket
	notes    []string
	problems []rules.Problem
}

// places は正とするツリーの側だけを読む。承認済みチケットが在れば、同じ識別子の todo/ は改版の候補か
// 書き損じで、状態の操作の相手ではない。
func places(conf *config.Settings, root, id string) found {
	raw := approval.ReadRaw(conf, root)
	var f found
	pick := func(ts []*ticket.Ticket) {
		for _, t := range ts {
			if t.ID == id {
				f.hits = append(f.hits, t)
			}
		}
	}
	copies, notes := approval.Scan(conf, root, false, raw)
	pick(copies)
	review, more := approval.ScanReview(conf, root, raw)
	pick(review)
	closed, _ := approval.Scan(conf, root, true, raw)
	pick(closed)
	proposals, problems := ticket.Scan(root, conf.Tickets, conf.Projects, nil)
	if len(f.hits) == 0 {
		for _, t := range proposals {
			if t.ID == id && t.State == place.Todo {
				f.hits = append(f.hits, t)
			}
		}
	}
	f.notes = append(notes, more...)
	f.problems = problems
	return f
}

func where(hits []*ticket.Ticket) string {
	parts := make([]string, len(hits))
	for i, t := range hits {
		name := t.Tree
		if name == "" {
			name = workspaceLabel
		}
		parts[i] = name + ":" + string(t.State)
	}
	return strings.Join(parts, ", ")
}

// undecided はどれが正か決まらないときの文。正とする側の決まり方と、決まらない理由の解き方まで言う。
func undecided(stderr io.Writer, conf *config.Settings, root, head string, hits []*ticket.Ticket) {
	home := hits[0].Parent
	if home == "" {
		home = hits[0].ID
	}
	fmt.Fprintf(stderr, "%sが複数の場所にある: %s。1 つに決まるまで動かさない\n", head, where(hits))
	fmt.Fprintf(stderr, "  正は、親のブランチ %s のワークツリー（.claude/worktrees/%s）上のチケットだけ\n", home, home)
	if st := approval.FamilyStanding(conf, root, hits[0], nil); st.Stop != "" {
		for _, line := range standing.Guidance(root, st) {
			fmt.Fprintf(stderr, "  %s\n", line)
		}
		return
	}
	io.WriteString(stderr, "  親のワークツリーの外のチケットは、"+
		"親のブランチへ移してコミットしてから削除するか、"+
		"残ったワークツリーを片付けてから再実行してください\n")
}

// Absent はこの識別子のチケットが、どの置き場にも無いか（`--check` が通す）。2 か所以上に在る・
// 親子が止まっているのは「無い」ではない（Find が断る）。
func Absent(conf *config.Settings, root, id string) bool {
	return len(places(conf, root, id).hits) == 0
}

// Find はこの識別子のチケットを、どの置き場に在っても 1 つ引く（State に置き場が入る）。引けなければ理由を
// stderr に書いて nil。
func Find(stderr io.Writer, conf *config.Settings, root, id string) *ticket.Ticket {
	f := places(conf, root, id)
	if len(f.hits) == 0 {
		fmt.Fprintf(stderr, "ccnavi: チケット %s が見つからない（%s/ と %s/ の下を全ツリーで探した）\n",
			id, conf.Approved, conf.Tickets)
		for _, p := range f.problems {
			fmt.Fprintf(stderr, "  %s\n", p)
		}
		for _, n := range f.notes {
			fmt.Fprintf(stderr, "  %s\n", n)
		}
		return nil
	}
	if len(f.hits) > 1 {
		undecided(stderr, conf, root, "ccnavi: "+id+" ", f.hits)
		return nil
	}
	if FamilyStopped(stderr, conf, root, f.hits[0]) {
		return nil
	}
	return f.hits[0]
}

// MisplacedHome は承認済みチケットが識別子と違う名前のワークツリーに在れば、理由を書いて真。
// ready まで進むと止まるので、start と finish の段で言う。
func MisplacedHome(stderr io.Writer, conf *config.Settings, root string, t *ticket.Ticket) bool {
	reason := approval.HomeProblem(root, conf, t)
	if reason != "" {
		fmt.Fprintf(stderr, "ccnavi: %s: %s\n", t.ID, reason)
	}
	return reason != ""
}

// FamilyStopped は親子のチケットが決まらない（親のワークツリーが無いなど）か、引いたチケットが親の
// ワークツリーの外にしか無ければ、言って真。信頼しないチケットの状態は動かさない。
func FamilyStopped(stderr io.Writer, conf *config.Settings, root string, t *ticket.Ticket) bool {
	st := approval.FamilyStanding(conf, root, t, nil)
	if st.Stop != "" {
		fmt.Fprintf(stderr, "ccnavi: %s: %s。この親子のチケットの状態は動かさない\n", t.ID, st.Stop)
		for _, line := range standing.Guidance(root, st) {
			fmt.Fprintf(stderr, "  %s\n", line)
		}
		return true
	}
	if st.Home != nil && !standing.SameTree(t.TreeRoot, st.Home.Root) {
		shown := t.Tree
		if shown == "" {
			shown = "ワークスペースルート"
		}
		fmt.Fprintf(stderr, "ccnavi: %s: チケットが親のブランチ %s のワークツリーの外（%s）にしか無い。"+
			"取り込み済みの親子のチケットでは親のブランチ上のチケットだけが正なので、このチケットは動かさない。\n"+
			"  ユーザがそのチケットを親のワークツリー（.claude/worktrees/%s）へ移してコミットと push をしてから再実行します。\n",
			t.ID, st.Family, shown, st.Family)
		return true
	}
	return false
}

// ParentNotStarted は子に着手してはいけないとき（親が承認されていない・作業中でない・未着手）に言って真。
// 親の start を飛ばすと親を閉じるときまで気付けないので、最初の子の着手で止める。
func ParentNotStarted(stderr io.Writer, conf *config.Settings, root string, t *ticket.Ticket) bool {
	if !t.IsChild() {
		return false
	}
	f := places(conf, root, t.Parent)
	ticketSh := config.ScriptCommand(root, ticketScript)
	head := fmt.Sprintf("ccnavi: %s の親 %s ", t.ID, t.Parent)
	if len(f.hits) == 0 {
		fmt.Fprintf(stderr, "%sが見つからない（%s/ と %s/ を全ツリーで探した）\n", head, conf.Approved, conf.Tickets)
		for _, n := range f.notes {
			fmt.Fprintf(stderr, "  %s\n", n)
		}
		return true
	}
	if len(f.hits) > 1 {
		undecided(stderr, conf, root, head, f.hits)
		return true
	}
	parent := f.hits[0]
	switch {
	case parent.State == place.Todo:
		fmt.Fprintf(stderr, "%sがまだ承認されていない（todo/）。先にユーザに 'ccnavi-user-agree.sh' を通してもらい、"+
			"'%s start %s' で開始してください\n", head, ticketSh, t.Parent)
	case parent.State != place.Doing:
		// review/ に親があるのは不整合なデータのときだけで、「閉じた」と言うと事実と違う。置き場だけを言う。
		fmt.Fprintf(stderr, "%sは作業中ではない（いまは %s/）。子を足せる親ではない\n", head, parent.State)
	case parent.StartedAt == "":
		fmt.Fprintf(stderr, "%sが未着手（%s/）。子より先に親を開始してください。\n  '%s start %s'\n",
			head, parent.State, ticketSh, t.Parent)
	default:
		return false
	}
	return true
}

// PredecessorsUnmet は子の先行が欠けていれば、先行ごとの今とすることを言って真。承認のあとに先行が
// 動くことがあるので、着手の手前でもう一度見る。
func PredecessorsUnmet(stderr io.Writer, conf *config.Settings, root string, t *ticket.Ticket) bool {
	unmet := approval.UnmetPredecessors(t, approval.PredecessorPool(conf, root))
	if len(unmet) == 0 {
		return false
	}
	ticketSh := config.ScriptCommand(root, ticketScript)
	fmt.Fprintf(stderr, "ccnavi: %s の先行が満たされていないので開始しない（先行は %s/%s/ に在って取り消しでないこと）:\n",
		t.ID, conf.Approved, place.Done)
	waiting, stuck := false, false
	for _, p := range unmet {
		fmt.Fprintf(stderr, "  - 先行 %s: %s\n", p.Ticket, p.Label())
		if p.Waiting() {
			waiting = true
		} else {
			stuck = true
		}
	}
	if waiting {
		fmt.Fprintf(stderr, "  先行を先に閉じてください（作業中なら '%s finish <先行>'。"+
			"レビューが必要ならユーザのレビューが済んで done/ に入るまで待つ）\n", ticketSh)
	}
	if stuck {
		io.WriteString(stderr, "  取り消した・どこにも無い・自分自身や自分の親・循環した先行は、待っても満たせない。"+
			"複数の場所にある先行は、先に 1 つに決めてください\n")
	}
	fmt.Fprintf(stderr, "  先行が不要なら、ユーザに承認済みチケットの predecessors から削除してもらうか、"+
		"'%s cancel %s --reason <理由>' で取り消し、先行を外した提案を出し直して承認を受けてください\n", ticketSh, t.ID)
	return true
}
