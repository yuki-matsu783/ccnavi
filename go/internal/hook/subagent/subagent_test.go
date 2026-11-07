package subagent

import (
	"bytes"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/prune"
	"ccnavi/internal/tickets/testkit"
)

// 印の値に区切りが残ると、行で読む側が途中で切れた値を受け取る。
func TestFlatRemovesEveryLineBreakOfSplitlines(t *testing.T) {
	got := flat("a\nb\rc\x0bd\x0ce\x1cf\x1dg\x1eh\u0085i j k")
	if strings.Join(strings.Split(got, " "), "") != "abcdefghijk" || strings.ContainsAny(got, "\n\r\v\f\x1c\x1d\x1e\u0085") {
		t.Errorf("%q", got)
	}
}

func clock(t *testing.T, at time.Time) {
	t.Helper()
	t.Cleanup(fsio.SetNow(func() time.Time { return at }))
}

func TestRunningCountsOnlyFreshMarksOfThisSession(t *testing.T) {
	state := filepath.Join(testkit.TempRoot(t), "state")
	at := time.Unix(1_800_000_000, 0)
	clock(t, at)
	file := markPath(state, "s1", "a1", runningSuffix)
	limit := int64(config.SubagentRunningMax.Value().Seconds())
	cases := []struct {
		name, text string
		mtime      time.Time
		want       bool
	}{
		{"期限の内", markText("s1", []string{strconv.FormatInt(at.Unix()-limit+600, 10)}), at, true},
		{"期限の外", markText("s1", []string{strconv.FormatInt(at.Unix()-limit-60, 10)}), at, false},
		// 開始時刻を読めない印は更新時刻で期限を見る。新しければ促さない側に倒す。
		{"壊れていて新しい", "", at, true},
		{"壊れていて古い", "", at.Add(-time.Duration(limit+60) * time.Second), false},
		{"時刻が数でない", "session s1\nnot-a-number\n", at, true},
		// 名前の前置きが重なる別のセッションの印は、1 行目で外す。
		{"前置きが重なる別のセッション", "session s1-other\n" + strconv.FormatInt(at.Unix(), 10) + "\n", at, false},
	}
	for _, c := range cases {
		testkit.Put(t, file, c.text)
		if err := os.Chtimes(file, c.mtime, c.mtime); err != nil {
			t.Fatal(err)
		}
		if got := Running(state, "s1"); got != c.want {
			t.Errorf("%s: %v", c.name, got)
		}
	}
	if Running("", "s1") || Running(filepath.Join(state, "none"), "s1") {
		t.Error("置き場が無いのに実行中")
	}
	// 一覧できない置き場は実行中（促さない側）。
	if !Running(file, "s1") {
		t.Error("一覧できないのに実行中でない")
	}
}

func TestForgetRunningKeepsMarksOfAnotherSession(t *testing.T) {
	state := filepath.Join(testkit.TempRoot(t), "state")
	mine := testkit.Put(t, markPath(state, "s1", "a1", runningSuffix), markText("s1", []string{"1"}))
	broken := testkit.Put(t, markPath(state, "s1", "a2", runningSuffix), "")
	other := testkit.Put(t, markPath(state, "s1", "a3", runningSuffix), "session s1-other\n1\n")
	ForgetRunning(state, "s1")
	for file, gone := range map[string]bool{mine: true, broken: true, other: false} {
		if _, err := os.Stat(file); (err != nil) != gone {
			t.Errorf("%s: 消えた %v", filepath.Base(file), err != nil)
		}
	}
}

// 期限を過ぎた相手と、別のセッションの相手は、同じワークツリーの案内に数えない。
func TestSharedNoteSkipsExpiredAndOtherSessions(t *testing.T) {
	root := testkit.Workspace(t)
	conf := testkit.Settings(t, root)
	at := time.Unix(1_800_000_000, 0)
	clock(t, at)
	limit := int64(config.SubagentRunningMax.Value().Seconds())
	peer := func(session, who string, started int64) {
		testkit.Put(t, markPath(conf.State, session, who, runningSuffix),
			markText(session, []string{strconv.FormatInt(started, 10), "tree main ", "type general-purpose"}))
	}
	peer("s1", "old", at.Unix()-limit-1)
	peer("s2", "other", at.Unix())
	start := func(agent string) string {
		var out bytes.Buffer
		in := hookio.Input{Event: hookio.SubagentStart, Cwd: root, SessionID: "s1", AgentID: agent, AgentType: "general-purpose"}
		if _, err := AtStart(&out, &bytes.Buffer{}, conf, root, in, &audit.Record{}); err != nil {
			t.Fatal(err)
		}
		return out.String()
	}
	if strings.Contains(start("a1"), "別の編集する") {
		t.Error("期限切れか別のセッションの相手を数えた")
	}
	peer("s1", "fresh", at.Unix())
	// a1 自身の印と fresh の 2 つ。
	if !strings.Contains(start("a2"), "general-purpose 2 件") {
		t.Error("期限内の相手を数えない")
	}
}

// 後始末（records/prune）が名前の写しで、このパッケージの印を古いセッションのものとして消す。
func TestPruneKnowsTheNames(t *testing.T) {
	root := testkit.TempRoot(t)
	state := filepath.Join(root, "logs", "state")
	past := time.Now().Add(-365 * 24 * time.Hour)
	var old, fresh []string
	for _, suffix := range []string{bouncedSuffix, uncheckedSuffix, runningSuffix} {
		file := testkit.Put(t, markPath(state, "old-session", "agent-1", suffix), "x")
		if err := os.Chtimes(file, past, past); err != nil {
			t.Fatal(err)
		}
		old = append(old, file)
		fresh = append(fresh, testkit.Put(t, markPath(state, "new-session", "agent-1", suffix), "x"))
	}
	report := prune.Run(root, filepath.Join(root, "logs", "ccnavi.log"), state, "live", prune.Options{})
	for _, file := range old {
		if _, err := os.Stat(file); err == nil {
			t.Errorf("古い印が残った: %s %+v", filepath.Base(file), report)
		}
	}
	for _, file := range fresh {
		if _, err := os.Stat(file); err != nil {
			t.Errorf("新しい印が消えた: %s %+v", filepath.Base(file), report)
		}
	}
}
