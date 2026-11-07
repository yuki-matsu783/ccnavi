package turn

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
)

func sessions(state string, now time.Time) []string {
	var out []string
	for _, a := range Active(state, now) {
		out = append(out, a.Session)
	}
	return out
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestBeginShowsTheSessionWithItsStart(t *testing.T) {
	state := t.TempDir()
	Begin(state, "s1", time.Unix(1000, 0))
	found := Active(state, time.Now())
	if len(found) != 1 || found[0].Session != "s1" || !found[0].Since.Equal(time.Unix(1000, 0)) {
		t.Fatalf("Active = %+v", found)
	}
	End(state, "s1")
	if got := sessions(state, time.Now()); got != nil {
		t.Errorf("終えたあと = %v", got)
	}
}

// 中断や途中で落ちたときは Stop が来ない。更新の無い印は古いとして数えない。
func TestStaleMarkerIsNotCountedUntilTouched(t *testing.T) {
	state := t.TempDir()
	stale := config.TurnStale.Value()
	base := time.Unix(1000, 0)
	Begin(state, "s1", base)
	if err := os.Chtimes(filepath.Join(state, "turn-s1.json"), base, base); err != nil {
		t.Fatal(err)
	}
	if got := sessions(state, base.Add(stale-time.Second)); !slices.Equal(got, []string{"s1"}) {
		t.Errorf("期限の手前 = %v", got)
	}
	if got := sessions(state, base.Add(stale)); got != nil {
		t.Errorf("期限 = %v", got)
	}
	Touch(state, "s1", base.Add(stale-5*time.Second))
	if got := sessions(state, base.Add(stale+time.Minute)); !slices.Equal(got, []string{"s1"}) {
		t.Errorf("更新のあと = %v", got)
	}
}

// Stop の直後に走ったサブエージェントの呼び出しが、消した印を作り直さない。
func TestTouchNeverCreatesTheMarker(t *testing.T) {
	state := t.TempDir()
	Begin(state, "s1", time.Now())
	End(state, "s1")
	Touch(state, "s1", time.Now())
	if _, err := os.Stat(filepath.Join(state, "turn-s1.json")); !os.IsNotExist(err) {
		t.Errorf("印が作られた: %v", err)
	}
}

func TestSessionsDoNotTouchEachOther(t *testing.T) {
	state := t.TempDir()
	Begin(state, "s1", time.Now())
	Begin(state, "s2", time.Now())
	End(state, "s1")
	if got := sessions(state, time.Now()); !slices.Equal(got, []string{"s2"}) {
		t.Errorf("Active = %v", got)
	}
}

func TestBrokenMarkersAreSkippedAndKept(t *testing.T) {
	state := t.TempDir()
	Begin(state, "ok", time.Now())
	for name, text := range map[string]string{
		"turn-broken.json":   "{not json",
		"turn-list.json":     "[]",
		"turn-nosince.json":  `{"session": "x"}`,
		"turn-badsince.json": `{"since": "yesterday"}`,
		"turn-bool.json":     `{"since": true}`,
	} {
		write(t, filepath.Join(state, name), text)
	}
	if got := sessions(state, time.Now()); !slices.Equal(got, []string{"ok"}) {
		t.Errorf("Active = %v", got)
	}
	if _, err := os.Stat(filepath.Join(state, "turn-broken.json")); err != nil {
		t.Errorf("読むだけで消さない: %v", err)
	}
}

func TestMissingOrEmptyPlacesNeverFail(t *testing.T) {
	dir := t.TempDir()
	blocker := filepath.Join(dir, "file")
	write(t, blocker, "x")
	for _, state := range []string{"", filepath.Join(dir, "nowhere", "state"), blocker} {
		Begin(state, "s1", time.Now())
		Touch(state, "s1", time.Now())
		End(state, "s1")
		Clear(state, "s1")
		if got := Active(state, time.Now()); got != nil {
			t.Errorf("%q: Active = %v", state, got)
		}
		if Busy(state, "s1") {
			t.Errorf("%q: Busy", state)
		}
		NotePrompt(state, "s1", time.Now())
		Clear(state, "s1")
		if _, ok := LastPrompt(state, "s1"); ok {
			t.Errorf("%q: LastPrompt", state)
		}
	}
	Begin(dir, "", time.Now())
	if got := sessions(dir, time.Now()); got != nil {
		t.Errorf("空のセッション = %v", got)
	}
}

func TestBusy(t *testing.T) {
	state := t.TempDir()
	if Busy(state, "s1") {
		t.Error("無い印が残っている")
	}
	RecordBusy(state, "s1", true)
	if !Busy(state, "s1") {
		t.Error("置いた印が無い")
	}
	RecordBusy(state, "s1", false)
	if Busy(state, "s1") {
		t.Error("残っていないのに印がある")
	}
	// 在るのに読めない・形が違う印は、残っているものとして読む。
	for _, text := range []string{"{broken", "[]", `{"busy": 0}`, `{}`} {
		write(t, filepath.Join(state, "busy-s1.json"), text)
		if !Busy(state, "s1") {
			t.Errorf("%s: 残っていないと読んだ", text)
		}
	}
	write(t, filepath.Join(state, "busy-s1.json"), `{"busy": false}`)
	if Busy(state, "s1") {
		t.Error(`{"busy": false} を残っていると読んだ`)
	}
}

func TestLastPrompt(t *testing.T) {
	state := t.TempDir()
	NotePrompt(state, "s1", time.Unix(1000, 0))
	NotePrompt(state, "s1", time.Unix(2000, 0))
	if at, ok := LastPrompt(state, "s1"); !ok || !at.Equal(time.Unix(2000, 0)) {
		t.Errorf("LastPrompt = %v, %v", at, ok)
	}
	if _, ok := LastPrompt(state, "s2"); ok {
		t.Error("別のセッションの記録を読んだ")
	}
	for _, text := range []string{"{not json", "[]", `{"at": "yesterday"}`, `{"at": true}`} {
		write(t, filepath.Join(state, "userprompt-s1.json"), text)
		if _, ok := LastPrompt(state, "s1"); ok {
			t.Errorf("%s: 読めた", text)
		}
	}
	NotePrompt(state, "s1", time.Unix(1000, 0))
	Clear(state, "s1")
	Clear(state, "nobody")
	if _, ok := LastPrompt(state, "s1"); ok {
		t.Error("消したのに読めた")
	}
}
