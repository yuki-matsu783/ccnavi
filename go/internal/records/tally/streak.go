package tally

import (
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// コマンドが失敗したり拒否されたりしても、エージェントは資料を引かずに試行錯誤を重ねることがある。
// 失敗（PostToolUseFailure）と拒否（PreToolUse の deny。dry-run も数える）の連続を数え、成功で 0 に戻す。
// 案内の文面は catalog が持つ。サブエージェントの呼び出しは、その子の連続として別に数える（案内は
// その文脈のモデルにしか届かないので、メインの成功が子の連続を消さないように）。

// DefaultStreakThreshold は回数の既定（CCNAVI_FAIL_STREAK_COUNT）。0 は数えない。
const DefaultStreakThreshold = 3

// Notice は Fail が出す案内の種類。
type Notice string

const (
	// NoNotice は案内を出さない回。
	NoNotice Notice = ""
	// First はこのセッションとエージェントで最初の失敗。成功で連続が戻っても印は残す。
	First Notice = "first"
	// Streak は連続の長さが n の倍数になった回。First と重なれば Streak だけを出す。
	Streak Notice = "streak"
)

const (
	streakPrefix = "failstreak-"
	mainAgent    = "main"
	keyCount     = "count"
	keyFirst     = "first"
)

// StreakThreshold は書かれた回数。0 は機能を使わない。読めない値と負数は既定。
func StreakThreshold(written string) int {
	if n, err := pyfmt.ParseInt(ospath.Strip(written)); err == nil && n >= 0 {
		return n
	}
	return DefaultStreakThreshold
}

func streakPath(stateDir, session, agent string) string {
	part := fsio.SafeName(agent, fsio.SafeNameLimit)
	if part == "" {
		part = mainAgent
	}
	return ospath.Join(stateDir, streakPrefix+fsio.SafeName(session, fsio.SafeNameLimit)+"-"+part+jsonExt)
}

// Fail は失敗か拒否を 1 回数え、案内の種類と今の連続の長さを返す。出さない回は NoNotice。
// 回数を書けなければ出さない（覚えられないまま出すと、失敗のたびに同じ案内が並ぶ）。n が 0 以下、
// 置き場が無い、セッションが空のときは数えない。
func Fail(stateDir, session, agent string, n int) (Notice, int) {
	if n <= 0 || stateDir == "" || session == "" {
		return NoNotice, 0
	}
	path := streakPath(stateDir, session, agent)
	data, ok := fsio.ReadObject(path)
	if !ok {
		data = pyjson.NewObject()
	}
	count, ok := recordedInt(data.Value(keyCount))
	if !ok {
		return NoNotice, 0
	}
	count++
	kind := NoNotice
	if count%n == 0 {
		kind = Streak
	} else if data.Value(keyFirst) != true {
		kind = First
	}
	if fsio.WriteJSON(path, pyjson.NewObject(keyCount, count, keyFirst, true)) != "" {
		return NoNotice, 0
	}
	return kind, count
}

// Reset は成功が入ったときに連続を 0 に戻す。最初の 1 回を案内した印は残す。
func Reset(stateDir, session, agent string, n int) {
	if n <= 0 || stateDir == "" || session == "" {
		return
	}
	path := streakPath(stateDir, session, agent)
	if data, ok := fsio.ReadObject(path); ok && pyfmt.Truthy(data.Value(keyCount)) {
		fsio.WriteJSON(path, pyjson.NewObject(keyFirst, true))
	}
}
