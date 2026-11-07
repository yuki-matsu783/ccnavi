package outcome

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/prune"
	"ccnavi/internal/tickets/testkit"
)

// fixture は記録の置き場の見本。確定・確定前・別のセッション・壊れたもの・手で直した型の違うもの。
var fixture = map[string]string{
	"a": `{"id": "s1:a:1", "session": "s1", "agent_id": "a", "child": "i0001-01-01", "outcome": "outside", "bounces": 0, ` +
		`"paths": ["p1", "p2", "p3", "p4", "p5", "p6", "p7"], "final": true, "notified": false, "nudged": false, "at": 3}`,
	"b": `{"id": "s1:b:1", "session": "s1", "agent_id": "b", "child": "i0001-01-02", "outcome": "outside", "bounces": 2, ` +
		`"paths": ["x"], "final": true, "notified": false, "nudged": false, "at": 1.5}`,
	"c": `{"id": "s1:c:1", "session": "s1", "agent_id": "c", "child": "", "outcome": "unchecked", "at": 2}`,
	"d": `{"id": "s1:d:1", "session": "s1", "agent_id": "d", "child": "i0001-01-01", "outcome": "unchecked", "nudged": true, "at": 4}`,
	"e": `{"id": "s1:e:1", "session": "s1", "agent_id": "e", "outcome": "unattributed", "paths": ["q"], "at": 5}`,
	"f": `{"id": "s1:f:1", "session": "s1", "agent_id": "f", "outcome": "unattributed", "notified": true, "at": 6}`,
	"g": `{"id": "s1:g:1", "session": "s1", "agent_id": "g", "outcome": "ok", "at": 7}`,
	"h": `{"id": "s1:h:1", "session": "s1", "agent_id": "h", "outcome": "outside", "final": false, "at": 8}`,
	"i": `{"id": "s2:i:1", "session": "s2", "agent_id": "i", "outcome": "outside", "at": 1}`,
	"j": `{"id": "s1:j:1", "session": "s1", "outcome": "outside", "at": `,
	"k": `{"id": "s1:k:1", "session": "s1", "agent_id": 5, "outcome": "outside", "bounces": "3", "paths": "ab", ` +
		`"final": null, "at": "9.5"}`,
	"l": `{"id": "s1:l:1", "session": "s1", "agent_id": "l", "outcome": "outside", "bounces": [1], "at": 1}`,
	"m": `[1]`,
	"n": `{"id": "s1:n:1", "session": "s1", "agent_id": "n", "outcome": "weird", "at": 1}`,
	// 識別子が文字列でない記録には印を付けられない（毎回促す）。
	"o": `{"id": 5, "session": "s1", "agent_id": "o", "outcome": "outside", "paths": {"k": 1}, "at": 10}`,
	"p": `{"id": "s1:p:1", "session": "s1", "agent_id": "p", "outcome": "unattributed", "paths": 3, "at": 1}`,
}

func putFixture(t *testing.T) string {
	dir := testkit.TempRoot(t)
	for who, text := range fixture {
		testkit.Put(t, path(dir, "s1", who), text)
	}
	testkit.Put(t, filepath.Join(dir, "subagent-s1-z.bounced"), "x")
	return dir
}

type pyAnswer struct {
	Of      [][]any           `json:"of"`
	Notices []any             `json:"notices"`
	Stop    [][]string        `json:"stop"`
	Files   map[string]string `json:"files"`
}

// 読み・知らせ・Stop の促し・印の付け方と、書き直したファイルのバイト列が Python と同じか。
func TestAgainstPython(t *testing.T) {
	who := []string{"a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "missing", ""}
	nudge := []string{"g", "h", "c", "b"}
	want := golden.Load[pyAnswer](t, "outcome")

	dir := putFixture(t)
	var got pyAnswer
	for _, w := range who {
		o := Of(dir, "s1", w)
		if o == nil {
			got.Of = append(got.Of, nil)
			continue
		}
		got.Of = append(got.Of, []any{o.agentID, o.child, o.Kind, o.bounces, o.paths, o.Final, o.Notified, o.nudged, line(o)})
	}
	text, done := Notices(dir, "s1")
	ids := []string{}
	for _, o := range done {
		ids = append(ids, o.agentID)
	}
	confirmed := Confirm(done)
	again, _ := Notices(dir, "s1")
	got.Notices = []any{text, ids, confirmed, again}
	got.Stop = [][]string{StopLines(dir, "s1"), StopLines(dir, "s1")}
	for _, w := range nudge {
		MarkNudged(dir, "s1", w)
	}
	got.Files = map[string]string{}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		data, _ := os.ReadFile(filepath.Join(dir, e.Name()))
		got.Files[e.Name()] = string(data)
	}
	testkit.SameJSON(t, map[string]any{"of": got.Of, "notices": got.Notices, "stop": got.Stop, "files": got.Files},
		map[string]any{"of": toAny(t, want.Of), "notices": toAny(t, want.Notices), "stop": toAny(t, want.Stop),
			"files": toAny(t, want.Files)})
}

// toAny は SameJSON が比べられる形（JSON を読み直した値）にする。
func toAny(t *testing.T, v any) any {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// 書いた記録の欄の並びと、読み直した値。知らない種類は書かない。
func TestWrite(t *testing.T) {
	dir := testkit.TempRoot(t)
	if why := Write(dir, "s1", "a/b", Entry{Kind: Outside, Child: "c", Bounces: 2, Paths: []string{"x", "y"}, Provisional: true}); why != "" {
		t.Fatal(why)
	}
	text, err := os.ReadFile(filepath.Join(dir, "subagent-s1-a_b.outcome"))
	if err != nil {
		t.Fatal(err)
	}
	value, err := pyjson.Loads(string(text))
	if err != nil {
		t.Fatal(err)
	}
	keys := value.(*pyjson.Object).Keys()
	order := []string{"id", "session", "agent_id", "child", "outcome", "bounces", "paths", "final", "notified", "nudged", "at"}
	if !slices.Equal(keys, order) {
		t.Errorf("欄の並び %v", keys)
	}
	if id := value.(*pyjson.Object).String("id"); !strings.HasPrefix(id, "s1:a/b:") {
		t.Errorf("id %q", id)
	}
	o := Of(dir, "s1", "a/b")
	if o == nil || o.Kind != Outside || o.Final || o.bounces != 2 || !slices.Equal(o.paths, []string{"x", "y"}) || o.child != "c" {
		t.Errorf("読み直し %+v", o)
	}
	if Write(dir, "s1", "a", Entry{Kind: "weird"}) != "知らない結果: weird" {
		t.Error("知らない種類を書いた")
	}
	if Write("", "s1", "a", Entry{Kind: OK}) != "" || Of("", "s1", "a") != nil || Of(dir, "s1", "") != nil {
		t.Error("置き場・相手の無いときに何かした")
	}
}

// 後始末（records/prune）が名前の写しで、このパッケージの記録を古いセッションのものとして消す。
func TestPruneKnowsTheName(t *testing.T) {
	root := testkit.TempRoot(t)
	state := filepath.Join(root, "logs", "state")
	if why := Write(state, "old-session", "agent-1", Entry{Kind: OK}); why != "" {
		t.Fatal(why)
	}
	file := path(state, "old-session", "agent-1")
	past := time.Now().Add(-365 * 24 * time.Hour)
	if err := os.Chtimes(file, past, past); err != nil {
		t.Fatal(err)
	}
	if why := Write(state, "new-session", "agent-1", Entry{Kind: OK}); why != "" {
		t.Fatal(why)
	}
	report := prune.Run(root, filepath.Join(root, "logs", "ccnavi.log"), state, "live", prune.Options{})
	if _, err := os.Stat(file); err == nil {
		t.Errorf("古い記録が残った: %+v", report)
	}
	if _, err := os.Stat(path(state, "new-session", "agent-1")); err != nil {
		t.Errorf("新しい記録が消えた: %+v", report)
	}
}
