package turn

import (
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyjson"
)

// NotePrompt はユーザの発言（hookio の is_user_prompt）の時刻を書く。ターンの印は Stop で消えるので、
// ターン分離のゲートが「前の段のあとにユーザが発言したか」を見るために別に持つ。書けなくても何も言わない。
func NotePrompt(state, session string, now time.Time) {
	if state == "" || session == "" {
		return
	}
	fsio.WriteJSON(path(state, UserPromptPrefix, session), pyjson.NewObject("session", session, "at", fsio.UnixSeconds(now)))
}

// LastPrompt は、最後のユーザの発言の時刻を返す。無い・読めない・形が違うときは false を返す。
// 発言が無いものとして読むので、ゲートは閉じる側に倒れる。次の発言が書き直すため、閉じたままにはならない。
func LastPrompt(state, session string) (time.Time, bool) {
	if state == "" || session == "" {
		return time.Time{}, false
	}
	data, err := fsio.ReadJSON(path(state, UserPromptPrefix, session))
	obj, ok := data.(*pyjson.Object)
	if err != nil || !ok {
		return time.Time{}, false
	}
	at, ok := pyjson.Number(obj.Value("at"), pyjson.BoolsRejected)
	if !ok {
		return time.Time{}, false
	}
	return fsio.FromUnixSeconds(at), true
}
