package stop

import (
	"cmp"
	"slices"
	"strconv"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/lifecycle/closing"
)

// NudgePrefix は促した状態の記録の名前の頭（records/prune が同じ頭で消す）。
const NudgePrefix = "nudged-"

// unknownSession は session が空のときの記録の名前。
const unknownSession = "unknown"

// UserStopNote は Stop の促しの共通の 1 文。直前にユーザが止めた・待たせた・相談したいと言ったときは、
// 促しに従わずに終えさせる。
const UserStopNote = "直前にユーザが「止めて」「待って」「相談したい」と言った場合は、このまま進めずにターンを終えてください。" +
	"止めた理由と、再開するときの次の一手を1行で書きます。" +
	"ユーザの判断が本当に要る問いがある場合は、その問いを最後に書き直して終えてください。"

// Nudge は促した記録に残す相手。Key はキー、Digest は状態の要約で、同じ要約では 1 回だけ促す。
type Nudge interface {
	Key() string
	Digest() string
}

// NudgePath は促した状態の記録のパス。セッションごとに置く。
func NudgePath(stateDir, session string) string {
	return fsio.RecordPath(stateDir, NudgePrefix, cmp.Or(session, unknownSession), fsio.JSONExt)
}

func readNudges(stateDir, session string) *pyjson.Object {
	data, ok := fsio.ReadObject(NudgePath(stateDir, session))
	if !ok {
		return &pyjson.Object{}
	}
	return data
}

// seen は記録が key に value を持つか。
func seen(data *pyjson.Object, key, value string) bool {
	got, ok := data.Value(key).(string)
	return ok && got == value
}

// NudgedBefore はこのセッションで、同じ相手を同じ状態のまま促したことがあるか。
func NudgedBefore(stateDir, session string, n Nudge) bool {
	data := readNudges(stateDir, session)
	if seen(data, n.Key(), n.Digest()) {
		return true
	}
	if p, ok := n.(*Pending); ok {
		return p.finishNudged(data)
	}
	return false
}

// RememberNudge は促した状態を記録する（キーの順に並べ直す）。書けなければ理由。git で共有する履歴には入れない。
func RememberNudge(stateDir, session string, n Nudge) string {
	path := NudgePath(stateDir, session)
	data, ok := fsio.ReadObject(path)
	if !ok {
		data = &pyjson.Object{}
	}
	data.Set(n.Key(), n.Digest())
	keys := data.Keys()
	slices.Sort(keys)
	sorted := &pyjson.Object{}
	for _, k := range keys {
		sorted.Set(k, data.Value(k))
	}
	return fsio.WriteJSON(path, sorted)
}

// HeadSHA はワークツリーの HEAD の sha。読めなければ空。deadline がゼロ値でなければ、過ぎてから git を起こさない。
func HeadSHA(worktree string, deadline time.Time) string {
	args := []string{"rev-parse", "HEAD"}
	o := git.Options{Timeout: closing.GitTimeout}
	var done git.Done
	if deadline.IsZero() {
		done = git.Run(worktree, args, o)
	} else {
		done = git.RunUntil(worktree, args, deadline, o)
	}
	if done.Failure != nil || done.Code != 0 {
		return ""
	}
	return ospath.Strip(done.Out)
}

// Count は git の数の答え（rev-list --count）を読む。数字だけでなければ偽。
func Count(out string) (int, bool) {
	s := ospath.Strip(out)
	if s == "" {
		return 0, false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return 0, false
		}
	}
	n, err := strconv.Atoi(s)
	return n, err == nil
}

// zeroTime は期限なし（HeadSHA に渡す）。
var zeroTime time.Time
