package events

import (
	"bytes"
	"errors"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/hook/outcome"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/testkit"
)

type brokenWriter struct{}

func (brokenWriter) Write([]byte) (int, error) { return 0, errors.New("closed") }

// 実行後チェックが応答に載せたサブエージェントの終わり方の知らせは、応答を書き出せてから「通知済み」にする。
// 書き出せなかったら印を付けず、次の機会に出し直す。
func TestNoticeIsMarkedOnlyAfterTheAnswerIsWritten(t *testing.T) {
	root := testkit.TempRoot(t)
	conf := testkit.Settings(t, root)
	if why := outcome.Write(conf.State, "s1", "agent-x", outcome.Entry{Kind: outcome.Unattributed}); why != "" {
		t.Fatal(why)
	}
	notified := func() bool { return outcome.Of(conf.State, "s1", "agent-x").Notified }
	for _, broken := range []bool{true, false} {
		var held hookio.Held
		var stderr bytes.Buffer
		in := bash(hookio.PostToolUse, "ls", "t1")
		in.Cwd = root
		if _, err := Decide(&held, &stderr, config.Enable, conf, root, in, &audit.Record{}, time.Time{}); err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(held.String(), "agent-x") {
			t.Fatalf("知らせが応答に無い: %q %s", held.String(), stderr.String())
		}
		if notified() {
			t.Fatal("書き出す前に通知済みにした")
		}
		if broken {
			if held.Flush(brokenWriter{}) == nil {
				t.Fatal("書けない出力で誤りを返さない")
			}
			if notified() {
				t.Fatal("書けなかったのに通知済みにした")
			}
			continue
		}
		var out bytes.Buffer
		if err := held.Flush(&out); err != nil || out.Len() == 0 || !notified() {
			t.Fatalf("書けたのに通知済みにしない: %v %q", err, out.String())
		}
	}
}
