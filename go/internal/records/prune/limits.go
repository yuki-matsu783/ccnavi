package prune

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

const day = 24 * time.Hour

// limit は環境変数で動かせるしきい値。floor は 0 のほかに受ける下限（0 なら下限なし）。
type limit struct {
	env      string
	fallback float64
	floor    float64
}

var (
	rotateMB           = limit{env.LogRotateMB, 10, 1}
	logKeepDays        = limit{env.LogKeepDays, 14, 1}
	stateKeepDays      = limit{env.StateKeepDays, 14, 1}
	stopReviewKeepDays = limit{env.StopReviewKeepDays, 90, 0}
)

// read はしきい値と、読めなかった値の苦情（読めれば空）。
func (l limit) read() (float64, string) {
	raw := ospath.Strip(os.Getenv(l.env))
	if raw == "" {
		return l.fallback, ""
	}
	value, err := pyfmt.ParseFloat(raw)
	if err != nil || value < 0 || math.IsInf(value, 0) || math.IsNaN(value) {
		return l.fallback, fmt.Sprintf("%s=%s は 0 以上の数ではないので既定の %s で動く", l.env, pyfmt.Repr(raw), g(l.fallback))
	}
	if value > 0 && value < l.floor {
		return l.fallback, fmt.Sprintf("%s=%s は下限の %s より小さいので既定の %s で動く（0 はその段を止める）",
			l.env, pyfmt.Repr(raw), g(l.floor), g(l.fallback))
	}
	return value, ""
}

// g は Python の format(x, "g")。
func g(x float64) string { return strconv.FormatFloat(x, 'g', 6, 64) }

// days は日数を長さにする。表せないほど長ければ上限にする（何も消さない）。
func days(n float64) time.Duration {
	if n*float64(day) >= math.MaxInt64 {
		return time.Duration(math.MaxInt64)
	}
	return time.Duration(n * float64(day))
}

// bytesOf は MB をバイトにする。
func bytesOf(mb float64) int64 {
	if mb*1024*1024 >= math.MaxInt64 {
		return math.MaxInt64
	}
	return int64(mb * 1024 * 1024)
}

// cutoff は now から keep だけ前。
func cutoff(now time.Time, keep time.Duration) time.Time { return now.Add(-keep) }
