package place

import (
	"path/filepath"
	"testing"
)

const tickets, approved = "wip/proposals", ".ccnavi/approved"

func TestPlaces(t *testing.T) {
	cases := []struct {
		rel                       string
		ticket, unscoped, handoff bool
		leaves, lands, wip        bool
	}{
		{"wip/proposals/todo/a.md", true, true, false, false, false, true},
		{"WIP/Proposals/Review/a.md", true, true, false, true, true, true},
		{"wip/proposalsX/a.md", false, false, false, false, false, true},
		{"wip/proposals", false, false, false, false, false, true},
		{".ccnavi/approved/doing/a.md", true, true, false, true, false, false},
		{".ccnavi/approved/done/a.md", true, true, false, false, true, false},
		{"wip/handoff/a.py", false, true, true, false, false, true},
		{"WIP/Handoff/a.py", false, true, true, false, false, true},
		{"wip/handoffX/a", false, false, false, false, false, true},
		// "\" は区切りに直さない（Linux・macOS では 1 つの名前）。InWip だけは git の出したパスとして両方を見る。
		{`wip\proposals\todo\x.py`, false, false, false, false, false, true},
		{`wip\handoff\x.py`, false, false, false, false, false, true},
		{"src/a.go", false, false, false, false, false, false},
		{"wip", false, false, false, false, false, true},
		{"wipe/a", false, false, false, false, false, false},
	}
	for _, c := range cases {
		got := [6]bool{
			IsTicketPlace(c.rel, tickets, approved), IsUnscoped(c.rel, tickets, approved), IsHandoffPlace(c.rel),
			LeavesOpenState(c.rel, tickets, approved), LandsInFinishedState(c.rel, tickets, approved), InWip(c.rel),
		}
		if want := [6]bool{c.ticket, c.unscoped, c.handoff, c.leaves, c.lands, c.wip}; got != want {
			t.Errorf("%q: %v, want %v", c.rel, got, want)
		}
	}
	// 置き場のパス（設定の値）の "\" と前後の "/" は直す。空の置き場は何も含まない。
	if !IsTicketPlace("p/q/a", `\p\q\`, "") || IsTicketPlace("a", "", "") {
		t.Error("置き場のパスの読み")
	}
}

func TestProgress(t *testing.T) {
	order := []State{Todo, Doing, "", Review, Done, Cancelled, "unknown"}
	want := []int{0, 1, 1, 2, 3, 3, 0}
	for i, s := range order {
		if got := s.Progress(); got != want[i] {
			t.Errorf("%q: %d", s, got)
		}
	}
}

func TestScratchDir(t *testing.T) {
	if got := ScratchDir("/ws"); got != "/ws/scratchpad" {
		t.Error(got)
	}
}

func TestProposalPlaces(t *testing.T) {
	sep := string(filepath.Separator)
	if got := ProposalsRoot("/ws", "wip/proposals"); got != filepath.FromSlash("/ws/wip/proposals") {
		t.Error(got)
	}
	if got := Dir("/ws", "wip/proposals", Review); got != filepath.FromSlash("/ws/wip/proposals")+sep+"review" {
		t.Error(got)
	}
}
