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
	fsio.WriteJSON(path(state, UserPromptPrefix, session), pyjson.NewObject("session", session, "at", seconds(now)))
}

// LastPrompt は最後のユーザの発言の時刻。無い・読めない・形が違うときは false（発言が無いものとして読み、
// ゲートは閉じる側に倒れる。次の発言が書き直すので閉じたままにはならない）。
func LastPrompt(state, session string) (time.Time, bool) {
	if state == "" || session == "" {
		return time.Time{}, false
	}
	data, err := fsio.ReadJSON(path(state, UserPromptPrefix, session))
	obj, ok := data.(*pyjson.Object)
	if err != nil || !ok {
		return time.Time{}, false
	}
	at, ok := number(obj.Value("at"))
	if !ok {
		return time.Time{}, false
	}
	return fromSeconds(at), true
}
