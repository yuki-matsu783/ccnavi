package stop

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// 依頼までの道（PendingAtStop）で促さない親と、促しが同じ状態で 1 回だけになる条件。stop.json の見本が持たない場面を表で見る。
//
//	j0001 子は合流済みで依頼が無い（促す。以降の場面の比べ元）
//	j0002 子は合流済みで依頼済み（requested）。マーカーを外すと j0001 と同じになる
//	j0003 j0001 と同じで、早めに閉じた親（close-early）
//	j0004 レビューが要らないフェーズだけの計画。子は閉じている
//	j0005 子が着手していない
func requestCasesWorkspace(t *testing.T) string {
	root := clock.Workspace(t, "j0001", "j0002", "j0003", "j0004", "j0005")
	base := strings.TrimSpace(clock.Git(t, root, "rev-parse", "HEAD"))
	baseLine := "base_sha: " + base
	w := func(name string) string { return testkit.Worktree(root, name) }
	write := func(parent, state, id, text string) {
		testkit.Put(t, filepath.Join(approvedDir(w(parent)), state, id+".md"), text)
	}
	// 子 1 本が設計のフェーズを閉じて親へ合流済みの形にする。
	mergedFamily := func(id string) {
		write(id, "doing", id, testkit.ClosingParent(id, []string{testkit.ClosingStarted, baseLine}, "  - design"))
		child := id + "-01-01"
		dir := testkit.AddWorktree(t, root, child, id)
		testkit.Put(t, filepath.Join(dir, "src", "a"), "a\n")
		commitAll(t, dir, "a")
		merged := strings.TrimSpace(clock.Git(t, dir, "rev-parse", "HEAD"))
		clock.Git(t, w(id), "merge", "-q", "--ff-only", child)
		write(id, "done", child, testkit.ClosingChild(child, id, "1", testkit.ClosingStarted, testkit.ClosingDone, "merged_sha: "+merged))
	}
	mergedFamily("j0001")
	mergedFamily("j0002")
	testkit.Put(t, filepath.Join(approvedDir(w("j0002")), "phases", "j0002", "1.requested"), `{"at": "2026-01-02T00:00:00+0900"}`)
	mergedFamily("j0003")
	testkit.Put(t, filepath.Join(approvedDir(w("j0003")), "phases", "j0003", "close-early.json"), `{"at": "2026-01-02T00:00:00+0900"}`)

	write("j0004", "doing", "j0004", testkit.ClosingParent("j0004", []string{testkit.ClosingStarted, baseLine}, "  - research"))
	// レビューが要るかは、フェーズの子の human_review.required で決まる。
	noReview := testkit.ClosingChild("j0004-01-01", "j0004", "1", testkit.ClosingStarted, testkit.ClosingDone)
	write("j0004", "done", "j0004-01-01", strings.Replace(noReview, "required: true", "required: false", 1))

	write("j0005", "doing", "j0005", testkit.ClosingParent("j0005", []string{testkit.ClosingStarted, baseLine}, "  - design"))
	write("j0005", "doing", "j0005-01-01", testkit.ClosingChild("j0005-01-01", "j0005", "1"))
	return root
}

func TestPendingAtStopLeavesOutWhatIsNotOnTheWayToARequest(t *testing.T) {
	root := requestCasesWorkspace(t)
	conf := testkit.Settings(t, root)
	pending := func(parent string) *Pending {
		return PendingAtStop(conf, root, testkit.Worktree(root, parent), nil)
	}
	if p := pending("j0001"); p == nil || p.Step != stepRequest || p.Number != 1 {
		t.Fatalf("比べ元（子は合流済みで依頼が無い）は依頼を促す: %+v", p)
	}
	for parent, why := range map[string]string{
		"j0002": "依頼済み",
		"j0003": "早めに閉じた親",
		"j0004": "レビューが要らないフェーズ",
		"j0005": "着手していない子",
	} {
		if p := pending(parent); p != nil {
			t.Errorf("%s（%s）を促した: %+v", parent, why, p)
		}
	}
	if w := WaitingAtStop(conf, root, testkit.Worktree(root, "j0004"), nil); w == nil {
		t.Error("レビューが要らないフェーズだけの親は、依頼を飛ばしてフィードバック計画へ進む")
	}
	// 子を足すと依頼のマーカーが外れる。外れたあとの依頼し直しの前は、促す。
	marker := filepath.Join(approvedDir(testkit.Worktree(root, "j0002")), "phases", "j0002", "1.requested")
	if err := os.Remove(marker); err != nil {
		t.Fatal(err)
	}
	if p := pending("j0002"); p == nil || p.Step != stepRequest {
		t.Errorf("マーカーが外れた親を促さない: %+v", p)
	}
}

// 同じ状態では 1 回だけ。子を閉じる・コミットを足すなどで状態の要約が変わると、また 1 回だけ促す。別のセッション・別のフェーズは
// 別の記録。
func TestANudgeIsOncePerStateAndSessionAndPhase(t *testing.T) {
	state := t.TempDir()
	at := func(children []string, head string) *Pending {
		return &Pending{Parent: &ticket.Ticket{ID: "i0001"}, Number: 1, Step: stepFinish, head: head, Children: children}
	}
	two := at([]string{"i0001-01-01", "i0001-01-02"}, "h1")
	if NudgedBefore(state, "s1", two) {
		t.Fatal("まだ促していない")
	}
	if why := RememberNudge(state, "s1", two); why != "" {
		t.Fatal(why)
	}
	cases := []struct {
		name    string
		session string
		n       *Pending
		before  bool
	}{
		{"同じ状態", "s1", at([]string{"i0001-01-01", "i0001-01-02"}, "h1"), true},
		{"子を 1 本閉じた", "s1", at([]string{"i0001-01-02"}, "h1"), false},
		{"コミットを足した", "s1", at([]string{"i0001-01-01", "i0001-01-02"}, "h2"), false},
		{"一手の種類が変わった", "s1", &Pending{Parent: &ticket.Ticket{ID: "i0001"}, Number: 1, Step: stepRequest, head: "h1"}, false},
		{"別のセッション", "s2", at([]string{"i0001-01-01", "i0001-01-02"}, "h1"), false},
		{"別のフェーズ", "s1", &Pending{Parent: &ticket.Ticket{ID: "i0001"}, Number: 2, Step: stepFinish, head: "h1",
			Children: []string{"i0001-01-01", "i0001-01-02"}}, false},
	}
	for _, c := range cases {
		if got := NudgedBefore(state, c.session, c.n); got != c.before {
			t.Errorf("%s: 促したことがある=%v、want %v", c.name, got, c.before)
		}
	}
	// 進んだ状態を記録すると、その状態は 1 回で済み、前の状態は同じキーなので上書きされる。
	one := at([]string{"i0001-01-02"}, "h1")
	RememberNudge(state, "s1", one)
	if !NudgedBefore(state, "s1", one) || NudgedBefore(state, "s1", two) {
		t.Error("進んだ状態を記録したあとの見え方が違う")
	}
	keys := []string{two.Key(), one.Key()}
	if !slices.Equal(keys, []string{"request:i0001:1", "request:i0001:1"}) {
		t.Errorf("キー %v", keys)
	}
}
