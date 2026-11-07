package ctxfile

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
)

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestBadPath(t *testing.T) {
	for rel, bad := range map[string]bool{
		"": false, "docs/a.md": false, `docs\a.md`: false, "a..b/c": false,
		"/etc/passwd": true, `\x`: true, "C:/x": true, `c:\x`: true, "../x": true, `a\..\b`: true, "a/../b": true,
	} {
		if got := BadPath(rel) != ""; got != bad {
			t.Errorf("BadPath(%q) = %q", rel, BadPath(rel))
		}
	}
}

func TestBodyIsCutAtTheLimit(t *testing.T) {
	root := t.TempDir()
	write(t, filepath.Join(root, "docs", "guide.md"), "# 決まり\r\n\r\nテストは tests/ に置く。\n\n")
	write(t, filepath.Join(root, "docs", "long.md"), strings.Repeat("あ", MaxChars+50))
	write(t, filepath.Join(root, "docs", "edge.md"), strings.Repeat("い", MaxChars))
	var stderr bytes.Buffer
	if got := body(&stderr, []string{root}, "docs/guide.md"); got != "# 決まり\n\nテストは tests/ に置く。" {
		t.Errorf("%q", got)
	}
	cut := body(&stderr, []string{root}, "docs/long.md")
	if !strings.HasPrefix(cut, strings.Repeat("あ", MaxChars)+"\n\n(ccnavi: docs/long.md は") ||
		strings.Contains(cut, strings.Repeat("あ", MaxChars+1)) || !strings.Contains(cut, "続きはこのファイルを読んで") {
		t.Errorf("%q", cut[len(cut)-200:])
	}
	if OverLimit(filepath.Join(root, "docs", "edge.md")) || !OverLimit(filepath.Join(root, "docs", "long.md")) {
		t.Error("上限の境目を取り違えた")
	}
	if body(&stderr, []string{root}, "docs/none.md") != "" || body(&stderr, []string{root}, "../x") != "" {
		t.Error("無いファイル・外のファイルを読んだ")
	}
	// 近いルートが先。
	near := t.TempDir()
	write(t, filepath.Join(near, "docs", "guide.md"), "近い")
	if got := body(&stderr, []string{near, root}, "docs/guide.md"); got != "近い" {
		t.Errorf("%q", got)
	}
}

// 先頭だけを読んでも、全部を読んで切ったときと同じ答えになる（読む切れ目が文字の途中・改行の途中に来ても）。
func TestBodyOfLargeFileMatchesFullRead(t *testing.T) {
	root := t.TempDir()
	cases := map[string]string{
		"three.md": strings.Repeat("あ", 100_000),
		"four.md":  strings.Repeat("𝄞", 100_000),
		"mixed.md": strings.Repeat("a\r\nい\xff\xe3\x81𝄞 ", 20_000),
		"crlf.md":  strings.Repeat("\r\n", 50_000),
	}
	var stderr bytes.Buffer
	for name, text := range cases {
		full := filepath.Join(root, "docs", name)
		write(t, full, text)
		whole, err := fsio.ReadDiskText(full, fsio.Replace)
		if err != nil {
			t.Fatal(err)
		}
		runes := []rune(whole)
		over := len(runes) > MaxChars
		want := ospath.Strip(whole)
		if over {
			want = strings.TrimRightFunc(string(runes[:MaxChars]), ospath.IsSpace) +
				"\n\n(ccnavi: docs/" + name + " は 4000 文字を超えるので先頭だけを載せた。続きはこのファイルを読んでください)"
		}
		if got := body(&stderr, []string{root}, "docs/"+name); got != want {
			t.Errorf("%s: Body が全部を読んだときと違う", name)
		}
		if OverLimit(full) != over {
			t.Errorf("%s: OverLimit = %v", name, !over)
		}
	}
}

func input(session, agent string) hookio.Input {
	return hookio.Input{Event: hookio.PostToolUse, ToolName: "Write", SessionID: session, AgentID: agent}
}

func onceRule() *rules.Rule {
	return &rules.Rule{ID: "note", Match: "Write", Decision: rules.Allow, Every: 1, AdditionalContextOnce: "1 度だけの文。"}
}

func readGiven(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(data)
}

func TestEveryAndOnce(t *testing.T) {
	state := t.TempDir()
	var stderr bytes.Buffer
	rule := &rules.Rule{ID: "r", Every: 3, AdditionalContext: "毎回 {root}", AdditionalContextOnce: "最初だけ", Root: "/w"}
	plain := &rules.Rule{ID: "p", Every: 1, AdditionalContext: "いつも"}
	var got []string
	var told []bool
	for range 7 {
		text, once := Deliver(&stderr, state, input("s1", ""), []*rules.Rule{rule, plain}, Options{})
		got = append(got, text)
		told = append(told, once)
	}
	want := []string{"いつも", "いつも", "毎回 /w\n\n最初だけ\n\nいつも", "いつも", "いつも", "毎回 /w\n\nいつも", "いつも"}
	for i := range want {
		if got[i] != want[i] || told[i] != (i == 2) {
			t.Errorf("%d 回目: %q %v", i+1, got[i], told[i])
		}
	}
	// 数えは刻みか once を持つルールだけ。キーの順に並べ、Python と同じ形で書く。
	if s := readGiven(t, oncePath(state, "s1", "")); s != `{"given": {"r": 7}}` {
		t.Errorf("%s", s)
	}
	// サブエージェントは別の文脈で数える。
	if text, _ := Deliver(&stderr, state, input("s1", "a1"), []*rules.Rule{onceRule()}, Options{}); text != "1 度だけの文。" {
		t.Errorf("%q", text)
	}
	// 置き場が無ければ刻まず、once も毎回渡す。Quiet なら何も渡さない。
	for range 2 {
		if text, once := Deliver(&stderr, "", input("s1", ""), []*rules.Rule{rule}, Options{}); text != "毎回 /w\n\n最初だけ" || !once {
			t.Errorf("%q", text)
		}
	}
	if text, _ := Deliver(&stderr, "", input("s1", ""), []*rules.Rule{plain}, Options{QuietWhenUnsure: true}); text != "" {
		t.Errorf("%q", text)
	}
}

// 読めなかった記録を空で上書きしない（覚えていた分が消える）。文は渡す側を採る。
func TestUnreadableStateIsKept(t *testing.T) {
	state := t.TempDir()
	path := oncePath(state, "s1", "")
	var stderr bytes.Buffer
	if text, _ := Deliver(&stderr, state, input("s1", ""), []*rules.Rule{onceRule()}, Options{}); text != "1 度だけの文。" {
		t.Fatalf("%q", text)
	}
	if s := readGiven(t, path); s != `{"given": {"note": 1}}` {
		t.Fatalf("%s", s)
	}
	write(t, path, "{壊れた")
	text, _ := Deliver(&stderr, state, input("s1", ""), []*rules.Rule{onceRule()}, Options{})
	if text != "1 度だけの文。" || readGiven(t, path) != "{壊れた" || !strings.Contains(stderr.String(), "記録を読めない") {
		t.Errorf("%q %q %q", text, readGiven(t, path), stderr.String())
	}
	if text, _ := Deliver(&stderr, state, input("s1", ""), []*rules.Rule{onceRule()}, Options{QuietWhenUnsure: true}); text != "" {
		t.Errorf("%q", text)
	}
	// 数えに真偽が入っていれば Python と同じく 1 と数える。
	write(t, path, `{"given": {"note": true, "x": "a", "y": 1.5}}`)
	if text, _ := Deliver(&stderr, state, input("s1", ""), []*rules.Rule{onceRule()}, Options{}); text != "" {
		t.Errorf("%q", text)
	}
	if s := readGiven(t, path); s != `{"given": {"note": 2}}` {
		t.Errorf("%s", s)
	}
}

func TestToldRefsAndPeers(t *testing.T) {
	state := t.TempDir()
	var stderr bytes.Buffer
	if _, ok := ToldRefs(&stderr, "", "s1"); ok {
		t.Error("置き場が無いのに覚えていると答えた")
	}
	if got, ok := ToldRefs(&stderr, state, "s1"); !ok || len(got) != 0 {
		t.Errorf("%v %v", got, ok)
	}
	if !RememberRefs(&stderr, state, "s1", map[string]bool{"mr:2": true, "issue:1": true}) {
		t.Fatal(stderr.String())
	}
	if got, ok := ToldRefs(&stderr, state, "s1"); !ok || !got["mr:2"] || !got["issue:1"] || len(got) != 2 {
		t.Errorf("%v", got)
	}
	if !RememberPeers(&stderr, state, "s1", "a1", map[string]bool{"k": true}) {
		t.Fatal(stderr.String())
	}
	// 相手の記録は Deliver の記録と別のファイル。
	Deliver(&stderr, state, input("s1", "a1"), []*rules.Rule{onceRule()}, Options{})
	if got, ok := ToldPeers(&stderr, state, "s1", "a1"); !ok || !got["k"] {
		t.Errorf("%v", got)
	}
	if _, ok := ToldPeers(&stderr, state, "s1", ""); ok {
		t.Error("agent が無いのに覚えていると答えた")
	}
}

func TestForget(t *testing.T) {
	state := t.TempDir()
	mine := oncePath(state, "s-1", "")
	sub := oncePath(state, "s-1", "a1")
	other := oncePath(state, "s-2", "")
	old := oncePath(state, "s-3", "")
	stem := strings.TrimSuffix(filepath.Base(mine), fsio.JSONExt)
	leftover := filepath.Join(state, "."+stem+".abc12345.part"+fsio.JSONExt)
	foreign := filepath.Join(state, ".other.abc12345.part.json")
	stop := StopPath(state, "s-1")
	for _, p := range []string{mine, sub, other, old, leftover, foreign, stop} {
		write(t, p, "{}")
	}
	past := time.Now().Add(-onceKeep - time.Hour)
	if err := os.Chtimes(old, past, past); err != nil {
		t.Fatal(err)
	}
	Forget(state, "s-1", false)
	for p, kept := range map[string]bool{mine: false, sub: false, leftover: false, old: false, other: true, foreign: true, stop: true} {
		if _, err := os.Stat(p); (err == nil) != kept {
			t.Errorf("%s: 残す=%v", filepath.Base(p), kept)
		}
	}
	Forget(state, "s-1", true)
	if _, err := os.Stat(stop); err == nil {
		t.Error("起動で Stop の数えを消していない")
	}
	Forget("", "s-1", true)
}

func TestNamesMatchPython(t *testing.T) {
	if got := filepath.Base(oncePath("/s", "", "")); got != "once-unknown-main.json" {
		t.Error(got)
	}
	if got := filepath.Base(StopPath("/s", "a/b")); got != "stop-a_b.json" {
		t.Error(got)
	}
}
