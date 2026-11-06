package stopreview

import (
	"fmt"
	"os"
	"slices"
	"strings"
	"testing"
	"time"
)

func TestProposeIdent(t *testing.T) {
	cases := []struct {
		words string
		want  string
	}{
		{"sh .ccnavi/scripts/ccnavi-ticket.sh approval-request p1", "p1"},
		{`sh C:\w\ccnavi-TICKET.sh approval-request -- p1`, "p1"},
		{"sh ccnavi-ticket.sh approval-request --reason x p2", "p2"},
		{"sh ccnavi-ticket.sh approval-request --check p1", ""},
		{"sh ccnavi-ticket.sh start p1", ""},
		{"sh ccnavi-ticket.sh approval-request", ""},
		{"echo approval-request p1", ""},
		{"FOO=1 sh ccnavi-ticket.sh approval-request p1", "p1"},
		{"env FOO=1 sh ccnavi-ticket.sh approval-request p1", "p1"},
		{"timeout 60 sh ccnavi-ticket.sh approval-request p1", "p1"},
		{"nohup sh -x ccnavi-ticket.sh approval-request p1", "p1"},
		{"uv run sh ccnavi-ticket.sh approval-request p1", "p1"},
		{"echo -n ccnavi-ticket.sh approval-request p1", ""},
		{"grep ccnavi-ticket.sh approval-request p1", ""},
	}
	for _, c := range cases {
		if got := ProposeIdent(strings.Fields(c.words)); got != c.want {
			t.Errorf("ProposeIdent(%q) = %q, want %q", c.words, got, c.want)
		}
	}
}

// kv は ccnavi-ticket.sh が書く結果ファイルを置く。
func kv(t *testing.T, state, name, ident, route string, started, ended int, extra ...string) {
	t.Helper()
	body := fmt.Sprintf("id=%s\nroute=%s\nrc=0\nstarted=%d\nended=%d\n", ident, route, started, ended)
	writeFile(t, state+"/"+ResultDir+"/"+name, body+strings.Join(extra, "\n")+"\n")
}

func sec(n int) time.Time { return time.Unix(int64(n), 0) }

func TestBindByIdentAndTime(t *testing.T) {
	state := t.TempDir()
	kv(t, state, "a.kv", "p1", "sent", 100, 101, "commit=c1", "file=sha1 wip/proposals/todo/p1.md")
	kv(t, state, "b.kv", "other", "sent", 100, 101)
	Register(state, "s1", "tu1", "p1", sec(100))
	op, ok := Bind(state, "tu1", sec(102))
	if !ok || op.Route != RouteSent || op.Commit != "c1" || !slices.Equal(op.Files, []File{{"sha1", "wip/proposals/todo/p1.md"}}) {
		t.Errorf("Bind = %+v, %v", op, ok)
	}
	if _, ok := Bind(state, "tu1", sec(102)); ok {
		t.Error("2 度結んだ")
	}
}

func TestAResultIsBoundToOneCallOnly(t *testing.T) {
	state := t.TempDir()
	kv(t, state, "a.kv", "p1", "sent", 100, 101)
	Register(state, "s1", "tu1", "p1", sec(100))
	Register(state, "s1", "tu2", "p1", sec(100))
	if _, ok := Bind(state, "tu1", sec(102)); !ok {
		t.Error("結ばない")
	}
	if _, ok := Bind(state, "tu2", sec(102)); ok {
		t.Error("同じ結果を 2 つの呼び出しに結んだ")
	}
}

func TestBindIgnores(t *testing.T) {
	cases := map[string]func(state string){
		"範囲の外": func(state string) { kv(t, state, "a.kv", "p1", "sent", 50, 51) },
		"知らない経路と壊れたファイル": func(state string) {
			kv(t, state, "a.kv", "p1", "mystery", 100, 101)
			writeFile(t, state+"/"+ResultDir+"/b.kv", "garbage\n")
		},
	}
	for name, setup := range cases {
		state := t.TempDir()
		setup(state)
		Register(state, "s1", "tu1", "p1", sec(100))
		if _, ok := Bind(state, "tu1", sec(102)); ok {
			t.Errorf("%s: 結んだ", name)
		}
	}
}

// op を書けないまま pre を bound にすると、結び直せなくなっていた。
func TestUnwritableOpLeavesThePreUnbound(t *testing.T) {
	state := t.TempDir()
	kv(t, state, "a.kv", "p1", "sent", 100, 101)
	Register(state, "s1", "tu1", "p1", sec(100))
	blocker := opPath(state, "tu1", "p1")
	if err := os.MkdirAll(blocker, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, ok := Bind(state, "tu1", sec(102)); ok {
		t.Error("op を書けないのに結んだ")
	}
	if bound := readObject(prePath(state, "tu1", "p1")).Value("bound"); bound != "" {
		t.Errorf("pre の bound = %v", bound)
	}
	if err := os.Remove(blocker); err != nil {
		t.Fatal(err)
	}
	if _, ok := Bind(state, "tu1", sec(102)); !ok {
		t.Error("書けるようになっても結べない")
	}
}

func TestRebindLaterThenUnlinked(t *testing.T) {
	state := t.TempDir()
	Register(state, "s1", "tu1", "p1", sec(100))
	if _, ok := Bind(state, "tu1", sec(102)); ok {
		t.Error("結果がまだ無いのに結んだ")
	}
	kv(t, state, "a.kv", "p1", "sent", 101, 140) // バックグラウンドで後から書かれた
	Rebind(state, "s1", sec(141))
	if got := TurnOps(state, "s1", sec(0)); len(got) != 1 {
		t.Errorf("TurnOps = %+v", got)
	}
	Register(state, "s1", "tu9", "p9", sec(200))
	Rebind(state, "s1", sec(201))
	if got := unlinkedTotal(state); got != 0 {
		t.Errorf("未連携 = %d", got)
	}
	Rebind(state, "s1", sec(200).Add(unlinkAfter+time.Second))
	if got := unlinkedTotal(state); got != 1 {
		t.Errorf("未連携 = %d", got)
	}
}

func TestTurnStartAndTurnOps(t *testing.T) {
	state := t.TempDir()
	if got := TurnStart(state, "s1"); !got.Equal(sec(0)) {
		t.Errorf("TurnStart = %v", got)
	}
	StartTurn(state, "s1", sec(123))
	if got := TurnStart(state, "s1"); !got.Equal(sec(123)) {
		t.Errorf("TurnStart = %v", got)
	}
	kv(t, state, "a.kv", "p1", "sent", 10, 11)
	kv(t, state, "b.kv", "p1", "sent", 200, 201)
	for tu, t0 := range map[string]int{"old": 10, "new": 200} {
		Register(state, "s1", tu, "p1", sec(t0))
		Bind(state, tu, sec(t0+2))
	}
	Register(state, "s2", "x", "p1", sec(200))
	var ids []string
	for _, op := range TurnOps(state, "s1", sec(150)) {
		ids = append(ids, op.ToolUseID)
	}
	if !slices.Equal(ids, []string{"new"}) {
		t.Errorf("TurnOps = %v", ids)
	}
	if got := TurnOps(state, "s1", sec(0)); len(got) != 2 {
		t.Errorf("TurnOps = %+v", got)
	}
}

func TestFinalVersions(t *testing.T) {
	op := func(route Route, ended int, ident, commit string) Op {
		return Op{Ident: ident, Route: route, Ended: sec(ended), Commit: commit}
	}
	cases := []struct {
		name string
		ops  []Op
		want []Op
	}{
		{"最後に送った版", []Op{op(RouteSent, 1, "p1", "c1"), op(RouteSent, 2, "p1", "c2")}, []Op{op(RouteSent, 2, "p1", "c2")}},
		{"送った後の失敗は版を残す", []Op{op(RouteSent, 1, "p1", "c1"), op(RouteFailedBefore, 2, "p1", "")}, []Op{op(RouteSent, 1, "p1", "c1")}},
		{"変更なしは版を動かさない", []Op{op(RouteSent, 1, "p1", "c1"), op(RouteUnchanged, 2, "p1", "")}, []Op{op(RouteSent, 1, "p1", "c1")}},
		{"失敗だけなら最後の理由", []Op{op(RouteFailedBefore, 1, "p1", ""), op(RouteStopped, 2, "p1", "")}, []Op{op(RouteStopped, 2, "p1", "")}},
		{"親と子は別", []Op{op(RouteSent, 1, "p1", "c1"), op(RouteStopped, 2, "p1-01-01", "")}, []Op{op(RouteSent, 1, "p1", "c1"), op(RouteStopped, 2, "p1-01-01", "")}},
	}
	for _, c := range cases {
		got := FinalVersions(c.ops)
		if len(got) != len(c.want) {
			t.Errorf("%s: %+v", c.name, got)
			continue
		}
		for i := range got {
			if got[i].Ident != c.want[i].Ident || got[i].Route != c.want[i].Route || got[i].Commit != c.want[i].Commit {
				t.Errorf("%s: %+v", c.name, got)
			}
		}
	}
	if got := TargetKey(Op{Ident: "p1", Route: RouteSent, Commit: "abcdef0123456789"}); got != "proposal:p1:abcdef012345" {
		t.Errorf("TargetKey = %q", got)
	}
	if got := TargetKey(Op{Ident: "p1", Route: RouteStopped}); got != "proposal:p1:stopped" {
		t.Errorf("TargetKey = %q", got)
	}
}

func TestWrittenIdent(t *testing.T) {
	cases := map[string]string{
		"/w/wip/proposals/todo/p1.md":                  "p1",
		`C:\w\WIP\Proposals\todo\P1-01-02.md`:          "P1-01-02",
		"/w/wip/proposals/todo/sub/p1.md":              "",
		"/w/wip/proposals/flows/p1.yml":                "",
		"/w/wip/proposals/todo/p1.txt":                 "",
		"/w/wip/proposals/todo/.md":                    "",
		"/w/src/todo/p1.md":                            "",
		"/w/\u212aelvin/wip/proposals/todo/\u00e9t.md": "\u00e9t", // 綴りでバイト長が変わる字の後ろ
	}
	for path, want := range cases {
		if got := WrittenIdent(path, "wip/proposals"); got != want {
			t.Errorf("WrittenIdent(%q) = %q, want %q", path, got, want)
		}
	}
}

func TestUnsentWrites(t *testing.T) {
	write := func(ident string, at int) Write {
		return Write{Ident: ident, At: sec(at), Path: "/w/wip/proposals/todo/" + ident + ".md"}
	}
	sent := func(ended int, idents ...string) Op {
		var files []File
		for _, i := range idents {
			files = append(files, File{"x", "wip/proposals/todo/" + i + ".md"})
		}
		return Op{Route: RouteSent, Ended: sec(ended), Files: files}
	}
	idents := func(ws []Write) []string {
		var out []string
		for _, w := range ws {
			out = append(out, w.Ident)
		}
		return out
	}
	cases := []struct {
		name   string
		writes []Write
		ops    []Op
		want   []string
	}{
		{"書いて送っていない", []Write{write("p1", 10)}, nil, []string{"p1"}},
		{"書いた後に親ごと送った", []Write{write("p1", 10), write("p1-01-01", 11)}, []Op{sent(20, "p1", "p1-01-01")}, nil},
		{"送った後に直した", []Write{write("p1", 30)}, []Op{sent(20, "p1")}, []string{"p1"}},
		{"失敗した approval-request は送ったことにならない", []Write{write("p1", 10)}, []Op{{Route: RouteFailedBefore, Ended: sec(20)}}, []string{"p1"}},
	}
	for _, c := range cases {
		if got := idents(UnsentWrites(c.writes, c.ops)); !slices.Equal(got, c.want) {
			t.Errorf("%s: %v", c.name, got)
		}
	}
}

func TestNotesArePerSessionAndTurn(t *testing.T) {
	state := t.TempDir()
	NoteWritten(state, "s1", "p1", "/w/p1.md", sec(10))
	NoteWritten(state, "s1", "P1", "/w/p1.md", sec(50)) // 同じ識別子（大文字小文字違い）
	NoteWritten(state, "s2", "p2", "/w/p2.md", sec(50))
	got := TurnWrites(state, "s1", sec(0))
	if len(got) != 1 || !got[0].At.Equal(sec(50)) {
		t.Errorf("TurnWrites = %+v", got)
	}
	if got := TurnWrites(state, "s1", sec(60)); got != nil {
		t.Errorf("TurnWrites = %+v", got)
	}
}

func TestRefs(t *testing.T) {
	state := t.TempDir()
	if got := SessionRefs(state, "s1"); got != nil {
		t.Errorf("SessionRefs = %v", got)
	}
	NoteRefs(state, "s1", []string{"#1", "!2"}, sec(1))
	NoteRefs(state, "s1", []string{"#1"}, sec(2))
	if got := SessionRefs(state, "s1"); !slices.Equal(got, []string{"!2", "#1"}) {
		t.Errorf("SessionRefs = %v", got)
	}
	for i := range 30 {
		NoteRefs(state, "s1", []string{fmt.Sprintf("#%d", i+10)}, sec(3+i))
	}
	if got := SessionRefs(state, "s1"); len(got) != refsKept+1 || got[len(got)-1] != "#39" {
		t.Errorf("SessionRefs = %v", got)
	}
}
