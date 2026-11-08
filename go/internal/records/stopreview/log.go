package stopreview

import (
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// logEvent は試行のイベントを専用の記録に 1 行足す。書けなくても判定は止めない。
func (s Store) logEvent(name event, attempt *pyjson.Object) {
	if s.Logs == "" {
		return
	}
	row := pyjson.NewObject("at", fsio.Stamp(), "event", string(name))
	for _, key := range []string{keyAttemptID, keyState, keyFinal, keyJudge, keyReason} {
		row.Set(key, attempt.Value(key))
	}
	line, err := pyjson.Dumps(row, pyjson.Options{RawUnicode: true})
	if err != nil {
		return
	}
	path := logPath(s.Logs, EventsFile)
	if fsio.MakeDirs(ospath.Dirname(path)) == nil {
		_ = fsio.AppendLine(path, line, 0o666)
	}
}

// Side は共有キーを導こうとした側。
type Side string

const (
	SideHook  Side = "hook"
	SideJudge Side = "judge"
)

// LogNoKey は共有キーを導けなかった Stop を数える（`stop-review report` の件数）。
func (s Store) LogNoKey(who Side) {
	s.logEvent(eventNoKey, pyjson.NewObject(keyReason, string(who)))
}

// LogPlanTimeout は判定役が計画を待ち切れなかったことを数える。
func (s Store) LogPlanTimeout(key string) {
	s.logEvent(eventTimeout, pyjson.NewObject(keyAttemptID, attemptID(key, false)))
}

// LogSuspected は記録外の停止の疑いを数える。ほかの hook による継続を含みうるので、疑いとして数える。
func (s Store) LogSuspected(session, key string) {
	s.logEvent(eventSuspected, pyjson.NewObject(keyAttemptID, key, keyReason, session))
}

// LastStop は前の Stop で ccnavi の促しか差し戻しが出たか。次の Stop で、記録外の停止の疑いを決めるのに読む。
type LastStop struct {
	key      string
	Nudged   bool
	SentBack bool
	t        any // 書いた時刻をそのまま持ち越す（差し戻しの印を足すときに書き直さない）
}

// NoteLastStop はこの Stop で促しか差し戻しが出たかを書く。
func (s Store) NoteLastStop(session, key string, nudged, sentBack bool, now time.Time) {
	s.noteLastStop(session, key, nudged, sentBack, fsio.UnixSeconds(now))
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
		key:      obj.String("key"),
		Nudged:   pyfmt.Truthy(obj.Value("nudged")),
		SentBack: pyfmt.Truthy(obj.Value("sent_back")),
		t:        t,
	}, true
}

// markSentBack は差し戻したことを、同じ Stop の記録に足す。
func (s Store) markSentBack(key string) {
	session, _, _ := strings.Cut(key, ":")
	if last, ok := s.LastStop(session); ok && last.key == key {
		s.noteLastStop(session, key, last.Nudged, true, last.t)
	}
}

// TaintedFlag は作業者が `stop-review` を打った印（hook の stopjudge が書く）があるか。
func (s Store) TaintedFlag(session string) bool {
	return pyfmt.Truthy(readObject(sessionFile(s.State, TaintPrefix, session)).Value("calls"))
}
