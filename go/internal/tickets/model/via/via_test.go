package via

import "testing"

func state() (Route, string) { return Current(), Actor() }

func TestSessionSetsAndRestores(t *testing.T) {
	before, beforeActor := state()
	done := Session(Board, "octo")
	if r, a := state(); r != Board || a != "octo" {
		t.Fatalf("Session: %s %s", r, a)
	}
	inner := Session(Chrome, "")
	if r, a := state(); r != Chrome || a != "" {
		t.Errorf("入れ子: %s %q", r, a)
	}
	inner()
	if r, a := state(); r != Board || a != "octo" {
		t.Errorf("入れ子の戻し: %s %s", r, a)
	}
	done()
	if r, a := state(); r != before || a != beforeActor {
		t.Errorf("戻し: %s %q", r, a)
	}
}

func TestSetActorIgnoresEmptyAndSetRouteReplaces(t *testing.T) {
	defer Session(CLI, "octo")()
	SetActor("")
	if Actor() != "octo" {
		t.Errorf("空で消えた: %q", Actor())
	}
	SetRoute(Terminal)
	if Current() != Terminal {
		t.Errorf("SetRoute: %s", Current())
	}
}
