package phase

import (
	"bytes"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

func phaseWith(number int, states []place.State, kinds ...marks.Kind) *Phase {
	p := newPhase("p", number)
	for i, s := range states {
		id := "p-" + string(rune('0'+number)) + "-" + string(rune('a'+i))
		p.Tickets = append(p.Tickets, &ticket.Ticket{ID: id, Parent: "p", Phase: number})
		p.States[id] = s
	}
	for _, k := range kinds {
		p.Marks[k] = pyjson.NewObject()
	}
	p.owner = &ticket.Ticket{ID: "p"}
	return p
}

// 開き直したフェーズの後続で、着手済みかレビュー済みのものだけを挙げる。
func TestDownstreamTouched(t *testing.T) {
	waits := func(list []*Phase, w map[int][]int) []*Phase {
		owner := &ticket.Ticket{ID: "p", Workflow: &ticket.Workflow{Waits: w}}
		for _, p := range list {
			p.owner = owner
		}
		return list
	}
	list := waits([]*Phase{
		phaseWith(1, []place.State{place.Done}, marks.Reviewed),
		phaseWith(2, []place.State{place.Done}, marks.Reviewed),
		phaseWith(3, []place.State{place.Doing}),
		phaseWith(4, nil),
	}, map[int][]int{2: {1}, 3: {1, 2}, 4: {1, 2, 3}})
	got := strings.Join(downstreamTouched(list, 1), "\n")
	if got != "フェーズ 2: 着手済み・レビュー済み\nフェーズ 3: 着手済み" {
		t.Errorf("後続 = %q", got)
	}
	notWaiting := waits([]*Phase{phaseWith(1, []place.State{place.Done}), phaseWith(2, []place.State{place.Doing})},
		map[int][]int{2: {}})
	if got := downstreamTouched(notWaiting, 1); len(got) != 0 {
		t.Errorf("先行にしていない後続を挙げた: %q", got)
	}
	untouched := waits([]*Phase{phaseWith(1, []place.State{place.Done}), phaseWith(2, nil)}, map[int][]int{2: {1}})
	if got := downstreamTouched(untouched, 1); len(got) != 0 {
		t.Errorf("着手していない後続を挙げた: %q", got)
	}
}

func item(kind string, after ...int) ticket.PlanItem {
	return ticket.PlanItem{Type: kind, After: after}
}

// 全体計画が終わったときの案内は、合流の書き方と phases: の差し込みを言う。
func TestNextHintAfterTheLastPhaseAsksForTheFeedbackPlan(t *testing.T) {
	root := testkit.RepoRoot(t)
	parent := &ticket.Ticket{ID: "i0001", Plan: []ticket.PlanItem{item("design")}}
	text := nextHint(root, parent, nil, 1)
	for _, want := range []string{"フィードバック計画", "after", "最後のステップ", "1 ステップで受ける",
		"--plan-order i0001 --fill-phases"} {
		if !strings.Contains(text, want) {
			t.Errorf("%q が無い: %s", want, text)
		}
	}
	parent.FeedbackPlanned = true
	if got := nextHint(root, parent, nil, 1); got == text || got == "" {
		t.Errorf("フィードバック計画があっても同じ案内: %q", got)
	}
	if got := nextHint(root, &ticket.Ticket{ID: "i0001"}, nil, 1); got != "" {
		t.Errorf("計画の無い親に案内した: %q", got)
	}
}

// 3 が先に閉じても、まだ子の無い 2 を次に始められるものとして挙げる。
func TestNextHintOffersAnEarlierBranchWithoutChildren(t *testing.T) {
	owner := &ticket.Ticket{ID: "i0001", Plan: []ticket.PlanItem{item("design"), item("acceptance", 1),
		item("implement", 1), item("docs", 2, 3)}}
	owner.Workflow = phases.Compute(owner)
	var list []*Phase
	for n := 1; n <= 4; n++ {
		p := newPhase("i0001", n)
		p.Item, p.owner = &owner.Plan[n-1], owner
		if n == 1 || n == 3 {
			c := &ticket.Ticket{ID: "c", Parent: "i0001", ReviewRequired: ticket.Unneeded}
			p.Tickets, p.States = []*ticket.Ticket{c}, map[string]place.State{"c": place.Done}
			p.Marks[marks.Skipped] = pyjson.NewObject()
		}
		list = append(list, p)
	}
	if hint := nextHint(testkit.RepoRoot(t), owner, list, 3); !strings.Contains(hint, "次に始められるのは 2") {
		t.Errorf("案内 = %q", hint)
	}
}

func TestLater(t *testing.T) {
	tests := []struct {
		a, b string
		want bool
	}{
		{"2026-01-03T00:00:00+09:00", "2026-01-02T00:00:00+09:00", true},
		{"2026-01-02T00:00:00+09:00", "2026-01-03T00:00:00+09:00", false},
		{"2026-01-02T00:00:00+0900", "2026-01-02T00:00:00+0900", false},
		// 同じ瞬間を違う時差で書いたものは後ではない。
		{"2026-01-02T00:00:00+09:00", "2026-01-01T15:00:00Z", false},
		{"2026-01-02T00:00:01+09:00", "2026-01-01T15:00:00Z", true},
		// 時差の有無が食い違う（比べられない）・読めないなら言わない側。
		{"2026-01-03T00:00:00", "2026-01-02T00:00:00+09:00", false},
		{"2026-01-03T00:00:00", "2026-01-02T00:00:00", true},
		{"", "2026-01-02T00:00:00+09:00", false},
		{"2026-01-03T00:00:00+09:00", "", false},
		{"昨日", "2026-01-02T00:00:00+09:00", false},
	}
	for _, tt := range tests {
		if got := later(tt.a, tt.b); got != tt.want {
			t.Errorf("later(%q, %q) = %v", tt.a, tt.b, got)
		}
	}
}

func TestManualLine(t *testing.T) {
	tests := []struct {
		m    Manual
		want string
	}{
		{Manual{State: SettleNone}, ""},
		{Manual{State: SettleReady, Children: []string{"a", "b"}}, "レビュー完了確認待ち: 子 a、b が手で done/ に動かされている。親が confirm を打つと確定する"},
		{Manual{State: settlePartial, Children: []string{"a"}, waiting: []string{"b", "c"}},
			"子 a はレビュー完了、フェーズは未完了（まだ review/・doing/ に残る子: b、c）"},
		{Manual{State: SettleUnshared, Children: []string{"a"}}, "移動が未共有: 子 a の done/ への移動が未コミットか未 push。ユーザの commit・push が必要（確定しない）"},
		{Manual{State: settleNoRequest}, "確認待ち（依頼の記録が無い）: 先に review-request を打つ"},
		{Manual{State: settleBroken, note: "x"}, "手動移動を確かめられない: x"},
	}
	for _, tt := range tests {
		if got := tt.m.Line(); got != tt.want {
			t.Errorf("%s: %q", tt.m.State, got)
		}
	}
	if !strings.Contains(Manual{State: settleRouteUnknown}.Line(), "経路が分からない") {
		t.Error("経路が分からない形の行")
	}
}

// 手で動かした子の判定は、閉じた子の経路の記録と、置き場の重なりで分ける（git を使わない形だけ。git の形は
// TestMatchesGolden が見る）。
func TestAssessManualWithoutGit(t *testing.T) {
	yes := true
	reviewPhase := func(children ...*ticket.Ticket) *Phase {
		p := gateCase{Type: &yes}.phase()
		p.Marks[marks.Requested] = pyjson.NewObject()
		for _, c := range children {
			p.Tickets = append(p.Tickets, c)
			p.States[c.ID] = c.State
		}
		return p
	}
	child := func(id string, state place.State, finished, settled string) *ticket.Ticket {
		return &ticket.Ticket{ID: id, Parent: "i0001", Phase: 2, State: state, FinishedTo: finished, SettledBy: settled,
			Path: "/t/" + id + ".md"}
	}
	parent := &ticket.Ticket{ID: "i0001"}
	moved := 1700000000.0
	tests := []struct {
		name     string
		children []*ticket.Ticket
		opts     ManualOptions
		state    Settle
		who      []string
	}{
		{"手で動かした子だけ", []*ticket.Ticket{child("a", place.Done, "review", "")},
			ManualOptions{External: true}, SettleReady, []string{"a"}},
		{"ccnavi が動かした子は手動ではない", []*ticket.Ticket{child("a", place.Done, "review", "confirm")},
			ManualOptions{External: true}, SettleNone, nil},
		{"done へ直に閉じた子は手動ではない", []*ticket.Ticket{child("a", place.Done, "done", "")},
			ManualOptions{External: true}, SettleNone, nil},
		{"経路の記録が無い子だけ", []*ticket.Ticket{child("a", place.Done, "", "")},
			ManualOptions{External: true}, settleRouteUnknown, []string{"a"}},
		{"経路の無い子とレビュー待ちの子", []*ticket.Ticket{child("a", place.Done, "", ""), child("b", place.Review, "review", "")},
			ManualOptions{External: true}, SettleNone, nil},
		{"一部だけ", []*ticket.Ticket{child("a", place.Done, "review", ""), child("b", place.Review, "review", "")},
			ManualOptions{External: true}, settlePartial, []string{"a"}},
		{"取り消した子は待たない", []*ticket.Ticket{child("a", place.Done, "review", ""), child("b", place.Cancelled, "", "")},
			ManualOptions{External: true, MovedAt: &moved}, SettleReady, []string{"a"}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			p := reviewPhase(tt.children...)
			got := AssessManual(nil, "", parent, p, []*Phase{p}, tt.opts)
			if got.State != tt.state || strings.Join(got.Children, ",") != strings.Join(tt.who, ",") {
				t.Errorf("AssessManual = %+v", got)
			}
		})
	}

	p := reviewPhase(child("a", place.Done, "review", ""))
	q := reviewPhase(child("a", place.Done, "review", ""))
	p.owner = &ticket.Ticket{ID: "i0001", Plan: []ticket.PlanItem{{Type: "design", Deferred: true}, {Type: "design"}}}
	p.Item, p.Number = &p.owner.Plan[1], 2
	q.Number = 1
	if got := AssessManual(nil, "", parent, p, []*Phase{q, p}, ManualOptions{External: true}); got.State != settleBroken {
		t.Errorf("延期分と引き受けた分に同じ子があるのに %+v", got)
	}

	future := float64(time.Now().Add(24 * time.Hour).Unix())
	one := reviewPhase(child("a", place.Done, "review", ""))
	got := AssessManual(nil, "", parent, one, []*Phase{one}, ManualOptions{External: true, MovedAt: &future})
	if got.State != SettleReady || got.MovedAt.After(time.Now()) {
		t.Errorf("未来の時刻を今に抑えない: %+v", got)
	}
	for _, bad := range []float64{0, -1, math.NaN(), math.Inf(1)} {
		got := AssessManual(nil, "", parent, one, []*Phase{one}, ManualOptions{External: true, MovedAt: &bad})
		if got.State != settleBroken || got.note != badTimeNote {
			t.Errorf("時刻 %v: %+v", bad, got)
		}
	}
}

// 親のワークツリーが別のブランチなら、そこにあるマーカーは読まず、終わりのマーカーも書かない。
func TestUnsettledParentFolderIsNeitherReadNorMarked(t *testing.T) {
	root := testkit.Workspace(t, "i0001")
	home := testkit.Worktree(root, "i0001")
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, "i0001"), "doing", "i0001.md"), parentText("i0001", "  - research"))
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, "i0001"), "done", "i0001-01-01.md"),
		childText("i0001-01-01", "i0001", "1", "false", []string{"wip/research/*"}, `completed_at: "2026-01-01T00:00:00+0900"`))
	testkit.Git(t, home, "checkout", "-q", "-b", "other")
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, "i0001"), "phases", "i0001", "1.reviewed"), `{"at": "2026-01-01"}`)
	conf := testkit.Settings(t, root)
	list := Phases(conf, root, "i0001", nil)
	if len(list) != 1 || len(list[0].Marks) != 0 {
		t.Fatalf("別のブランチのマーカーを読んだ: %+v", list)
	}
	opened, _ := approval.Scan(conf, root, false, nil)
	parent := approval.ByID(opened)["i0001"]
	var stderr bytes.Buffer
	if said := Announce(&stderr, conf, root, parent, nil); said != "" || stderr.Len() != 0 {
		t.Errorf("決まらない置き場に知らせた: %q %q", said, stderr.String())
	}
	if _, err := os.Stat(filepath.Join(testkit.ApprovedDir(root, "i0001"), "phases", "i0001", "1.skipped")); err == nil {
		t.Error("決まらない置き場にマーカーを書いた")
	}
}

// プロジェクト（projects/<名前>/）の親のワークツリーでは、プロジェクトの履歴と origin/<親のブランチ> で共有済みかを
// 見る。
func TestSharedReadsTheProjectHistory(t *testing.T) {
	const parentID, childID = "feature-1-web-x", "feature-1-web-x-01-01"
	root := testkit.Workspace(t)
	project := filepath.Join(root, "projects", "web")
	testkit.Put(t, filepath.Join(project, "a.txt"), "a\n")
	testkit.Git(t, project, "init", "-q", "-b", "main")
	testkit.Git(t, project, "add", "-A")
	testkit.Git(t, project, "commit", "-q", "-m", "init")
	remote := filepath.Join(root, "remote.git")
	testkit.Git(t, root, "init", "-q", "--bare", remote)
	testkit.Git(t, project, "remote", "add", "origin", remote)
	testkit.Git(t, project, "push", "-q", "origin", "main")
	home := testkit.Worktree(root, parentID)
	testkit.Git(t, project, "worktree", "add", "-q", home, "-b", parentID, "main")
	testkit.Git(t, home, "push", "-q", "-u", "origin", parentID)
	moved := &ticket.Ticket{ID: childID, Path: testkit.Put(t, filepath.Join(home, ".ccnavi", "approved", "done", childID+".md"), "x\n")}
	parent := &ticket.Ticket{ID: parentID}
	if _, why := shared(root, parent, []*ticket.Ticket{moved}); why != unsharedNote {
		t.Errorf("未コミット: %q", why)
	}
	testkit.Git(t, home, "add", "-A", "-f")
	testkit.Git(t, home, "commit", "-q", "-m", "move")
	if _, why := shared(root, parent, []*ticket.Ticket{moved}); why != unsharedNote {
		t.Errorf("未 push: %q", why)
	}
	testkit.Git(t, home, "push", "-q", "origin", parentID)
	at, why := shared(root, parent, []*ticket.Ticket{moved})
	if want, _ := time.Parse(time.RFC3339, "2026-01-05T00:00:00Z"); why != "" || !at.Equal(want) {
		t.Errorf("共有済み: %v %q", at, why)
	}
	outside := &ticket.Ticket{ID: "x", Path: filepath.Join(root, "x.md")}
	if _, why := shared(root, parent, []*ticket.Ticket{outside}); !strings.Contains(why, "親のツリーの外") {
		t.Errorf("ツリーの外: %q", why)
	}
}
