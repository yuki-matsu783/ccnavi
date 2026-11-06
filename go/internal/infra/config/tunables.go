package config

import (
	"math"
	"os"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

// Tunable は環境変数で秒数を動かせる時間。判定の答えは変えず、待つ長さと数える長さだけを動かす。
type Tunable struct {
	Env     string
	Default time.Duration
}

// Value は環境変数の秒数。無い・読めない・0 以下・有限でない値は既定。
func (t Tunable) Value() time.Duration {
	seconds, ok := parseSeconds(os.Getenv(t.Env))
	if !ok || math.IsInf(seconds, 0) || math.IsNaN(seconds) || seconds <= 0 {
		return t.Default
	}
	if seconds >= float64(math.MaxInt64)/float64(time.Second) {
		return time.Duration(math.MaxInt64)
	}
	return time.Duration(seconds * float64(time.Second))
}

// parseSeconds は Python の float(text.strip()) が読める綴りだけを読む。
// ParseFloat は 16 進を読み、数字の間の "_" を読まないので、そこを Python に合わせる。
// 桁あふれ（Python では inf か 0）は読めない扱いにする。どちらも既定に戻るので答えは同じ。
func parseSeconds(text string) (float64, bool) {
	text = ospath.Strip(text)
	lower := strings.ToLower(text)
	if strings.Contains(lower, "0x") || strings.Contains(lower, "p") {
		return 0, false
	}
	if strings.Contains(text, "_") {
		for i := 0; i < len(text); i++ {
			if text[i] == '_' && (i == 0 || i == len(text)-1 || !isDigit(text[i-1]) || !isDigit(text[i+1])) {
				return 0, false
			}
		}
		text = strings.ReplaceAll(text, "_", "")
	}
	value, err := strconv.ParseFloat(text, 64)
	return value, err == nil
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

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
