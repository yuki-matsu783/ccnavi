package config

import (
	"math"
	"os"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// Tunable は環境変数で秒数を動かせる時間。判定の答えは変えず、待つ長さと数える長さだけを動かす。
type Tunable struct {
	Env     string
	Default time.Duration
}

// Value は環境変数の秒数。無い・読めない・0 以下・有限でない値は既定。
func (t Tunable) Value() time.Duration {
	seconds, ok := Finite(os.Getenv(t.Env))
	if !ok || seconds <= 0 {
		return t.Default
	}
	if seconds >= float64(math.MaxInt64)/float64(time.Second) {
		return time.Duration(math.MaxInt64)
	}
	return time.Duration(seconds * float64(time.Second))
}

var (
	// HookDeadline は 1 回の hook の判定に張る期限。
	HookDeadline = Tunable{env.HookDeadlineSeconds, 3 * time.Second}
	// AskContextWait は確認の前に、直前の発言が会話記録へ書かれるのを待つ上限。
	AskContextWait = Tunable{env.AskContextWaitSeconds, 2500 * time.Millisecond}
	// IntegratePending は SessionStart で、終わっていない integrate の記録を確かめる上限。
	IntegratePending = Tunable{env.IntegratePendingSeconds, 2 * time.Second}
	// SubagentRunningMax は実行中のサブエージェントの印を「実行中」と数える上限。
	SubagentRunningMax = Tunable{env.SubagentRunningMaxSeconds, 4 * time.Hour}
	// TurnStale はターンの印を、更新が無いまま古いと数えるまでの長さ。
	TurnStale = Tunable{env.TurnStaleSeconds, 30 * time.Minute}
	// StopReviewTTL は停止レビューの判定計画を待つ時間と、試行の期限。
	StopReviewTTL = Tunable{env.StopReviewTTLSeconds, 10 * time.Minute}
)

// 書かれた数の読み。設定・環境変数の値を前後の空白を外して Python の int()・float() で読む。
// 下限や既定の扱いは読む側ごとに違うので、ここは読めたかまでを決める（Int は下限も受ける）。

// Int は書かれた整数。読めない値と least より小さい値は fallback。
func Int(written string, fallback, least int) int {
	if n, err := pyfmt.ParseInt(ospath.Strip(written)); err == nil && n >= least {
		return n
	}
	return fallback
}

// Finite は書かれた有限の数。読めない値・inf・nan は false。
func Finite(written string) (float64, bool) {
	value, err := pyfmt.ParseFloat(ospath.Strip(written))
	if err != nil || math.IsInf(value, 0) || math.IsNaN(value) {
		return 0, false
	}
	return value, true
}
