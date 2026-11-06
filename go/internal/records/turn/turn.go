package turn

import (
	"os"
	"regexp"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
)

// 置き場のファイル名の先頭。後始末（prune）が名前からセッションを読むので、形を変えるときは揃えて直す。
const (
	TurnPrefix       = "turn-"
	BusyPrefix       = "busy-"
	UserPromptPrefix = "userprompt-"
	suffix           = ".json"
)

var turnName = regexp.MustCompile(`^turn-(.+)\.json$`)

// Ongoing はターン中のセッション 1 つ。
type Ongoing struct {
	Session string
	Since   time.Time // ターンが始まった時刻
	Seen    time.Time // 最後にツールの呼び出しがあった時刻（ファイルの更新時刻）
}

func path(state, prefix, session string) string {
	return ospath.Join(state, prefix+fsio.SafeName(session, fsio.SafeNameLimit)+suffix)
}

// Begin はターンの始まりを書く（ユーザの発言とバックグラウンド作業の完了通知）。書けなくても何も言わない。
func Begin(state, session string, now time.Time) {
	if state == "" || session == "" {
		return
	}
	fsio.WriteJSON(path(state, TurnPrefix, session), pyjson.NewObject("session", session, "since", fsio.UnixSeconds(now)))
}

// Touch は印があるときだけ更新時刻を新しくする。無い印は作らない。
// Stop の直後に並んで走ったサブエージェントの呼び出しが、消した印を作り直さないため。
func Touch(state, session string, now time.Time) {
	if state == "" || session == "" {
		return
	}
	_ = os.Chtimes(path(state, TurnPrefix, session), now, now)
}

// End はターンの終わり（Stop・中断）と、起動・再開（Clear）で印を消す。
func End(state, session string) {
	if state != "" && session != "" {
		fsio.Remove(path(state, TurnPrefix, session))
	}
}

// Clear は起動と再開で、3 つの記録をすべて消す。新しいプロセスでは前のターンも作業も発言も今と関係しない。
func Clear(state, session string) {
	if state == "" || session == "" {
		return
	}
	for _, prefix := range []string{TurnPrefix, BusyPrefix, UserPromptPrefix} {
		fsio.Remove(path(state, prefix, session))
	}
}

// Active はいまターン中のセッション。更新時刻が config.TurnStale より前の印（途中で落ちた・中断で
// Stop が来なかった）と、読めない印は数えない。読むだけで、何も書かない・消さない。
func Active(state string, now time.Time) []Ongoing {
	if state == "" {
		return nil
	}
	entries, err := os.ReadDir(state)
	if err != nil {
		return nil
	}
	stale := config.TurnStale.Value()
	var found []Ongoing
	for _, entry := range entries {
		match := turnName.FindStringSubmatch(entry.Name())
		if match == nil {
			continue
		}
		file := ospath.Join(state, entry.Name())
		info, err := os.Stat(file)
		if err != nil {
			continue
		}
		seen := info.ModTime()
		if now.Sub(seen) >= stale {
			continue
		}
		data, err := fsio.ReadJSON(file)
		obj, ok := data.(*pyjson.Object)
		if err != nil || !ok {
			continue
		}
		since, ok := number(obj.Value("since"))
		if !ok {
			continue
		}
		session := obj.String("session")
		if session == "" {
			session = match[1]
		}
		found = append(found, Ongoing{Session: session, Since: fsio.FromUnixSeconds(since), Seen: seen})
	}
	return found
}

// number は JSON の数（真偽は除く）。
func number(v any) (float64, bool) {
	switch x := v.(type) {
	case int:
		return float64(x), true
	case float64:
		return x, true
	}
	return 0, false
}
