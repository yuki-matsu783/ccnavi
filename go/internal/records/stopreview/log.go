package stopreview

import (
	"os"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// logEvent は試行のイベントを専用の記録に 1 行足す。書けなくても判定は止めない。
func (s Store) logEvent(event string, attempt *pyjson.Object) {
	if s.Logs == "" {
		return
	}
	row := pyjson.NewObject("at", fsio.Stamp(), "event", event)
	for _, key := range []string{keyAttemptID, keyState, keyFinal, keyJudge, keyReason} {
		row.Set(key, attempt.Value(key))
	}
	line, err := pyjson.Dumps(row, pyjson.Options{RawUnicode: true})
	if err != nil {
		return
	}
	appendLine(logPath(s.Logs, EventsFile), line)
}

// appendLine は 1 行をテキストとして足す（Python の open(path, "a") と同じく、改行は機械のもの）。
func appendLine(path, line string) {
	if fsio.MakeDirs(ospath.Dirname(path)) != nil {
		return
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o666)
	if err != nil {
		return
	}
	defer f.Close()
	newline := "\n"
	if ospath.Windows {
		newline = "\r\n"
	}
	_, _ = f.WriteString(line + newline)
}

// LogNoKey は共有キーを導けなかった Stop を数える（`stop-review report` の件数）。who は "hook" か "judge"。
func (s Store) LogNoKey(who string) {
	s.logEvent(eventNoKey, pyjson.NewObject(keyReason, who))
}

// LogPlanTimeout は判定役が計画を待ち切れなかったことを数える。
func (s Store) LogPlanTimeout(key string) {
	s.logEvent(eventTimeout, pyjson.NewObject(keyAttemptID, AttemptID(key, false)))
}

// LogSuspected は記録外の停止の疑いを数える。ほかの hook による継続を含みうるので、疑いとして数える。
func (s Store) LogSuspected(session, key string) {
	s.logEvent(eventSuspected, pyjson.NewObject(keyAttemptID, key, keyReason, session))
}

// LastStop は前の Stop で ccnavi の促しか差し戻しが出たか。次の Stop で、記録外の停止の疑いを決めるのに読む。
type LastStop struct {
	Key      string
	Nudged   bool
	SentBack bool
	t        any // 書いた時刻をそのまま持ち越す（差し戻しの印を足すときに書き直さない）
}

// NoteLastStop はこの Stop で促しか差し戻しが出たかを書く。
func (s Store) NoteLastStop(session, key string, nudged, sentBack bool, now time.Time) {
	s.noteLastStop(session, key, nudged, sentBack, seconds(now))
}

func (s Store) noteLastStop(session, key string, nudged, sentBack bool, t any) {
	if s.State != "" && session != "" {
		fsio.WriteJSON(sessionFile(s.State, LastStopPrefix, session),
			pyjson.NewObject("key", key, "nudged", nudged, "sent_back", sentBack, "t", t))
	}
}

// LastStop は前の Stop の記録。無い・読めなければ false。
func (s Store) LastStop(session string) (LastStop, bool) {
	if s.State == "" || session == "" {
		return LastStop{}, false
	}
	obj := readObject(sessionFile(s.State, LastStopPrefix, session))
	if obj == nil {
		return LastStop{}, false
	}
	t, ok := obj.Get("t")
	if !ok {
		t = 0.0
	}
	return LastStop{
		Key:      obj.String("key"),
		Nudged:   pyfmt.Truthy(obj.Value("nudged")),
		SentBack: pyfmt.Truthy(obj.Value("sent_back")),
		t:        t,
	}, true
}

// markSentBack は差し戻したことを、同じ Stop の記録に足す。
func (s Store) markSentBack(key string) {
	session, _, _ := strings.Cut(key, ":")
	if last, ok := s.LastStop(session); ok && last.Key == key {
		s.noteLastStop(session, key, last.Nudged, true, last.t)
	}
}

// TaintedFlag は作業者が `stop-review` を打った印（hook の stopjudge が書く）があるか。
func (s Store) TaintedFlag(session string) bool {
	return pyfmt.Truthy(readObject(sessionFile(s.State, TaintPrefix, session)).Value("calls"))
}
