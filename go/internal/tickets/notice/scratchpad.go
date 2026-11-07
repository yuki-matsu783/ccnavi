package notice

import (
	"os"
	"strconv"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/place"
)

// defaultWarnMB は scratchpad/ の容量を知らせるしきい値の既定（MB）。0 なら知らせない。
const defaultWarnMB = 500.0

const mb = 1024 * 1024

// warnMB はしきい値（MB）。空・読めない・負・無限大・nan は既定。
func warnMB() float64 {
	value, ok := config.Finite(os.Getenv(env.ScratchpadWarnMB))
	if !ok || value < 0 {
		return defaultWarnMB
	}
	return value
}

// scratchBytes は scratchpad/ の中の通常ファイルの大きさの合計。無ければ 0。until を過ぎたら、そこまでの合計を
// 返す（ゼロ値は期限なし）。シンボリックリンクと junction は辿らず、数えない。読めないものは飛ばす。
func scratchBytes(root string, until time.Time) int64 {
	var total int64
	stack := []string{place.ScratchDir(root)}
	for len(stack) > 0 && (until.IsZero() || time.Now().Before(until)) {
		current := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		entries, err := os.ReadDir(current)
		if err != nil {
			continue
		}
		for _, e := range entries {
			path := ospath.Join(current, e.Name())
			kind := e.Type()
			switch {
			case kind&os.ModeSymlink != 0:
			case kind.IsDir():
				if !pathmatch.IsLink(path) {
					stack = append(stack, path)
				}
			case kind.IsRegular():
				if info, err := e.Info(); err == nil {
					total += info.Size()
				}
			}
		}
	}
	return total
}

// Scratchpad は scratchpad/ の中身がしきい値（CCNAVI_SCRATCHPAD_WARN_MB）を超えたときに、消してよいものを
// ユーザに尋ねる文。超えない・止めているときは空。ccnavi は scratchpad/ を消さないので、ここでも消さない。
// until は走査を打ち切る時刻（ゼロ値は期限なし）。
func Scratchpad(root string, until time.Time) string {
	limit := warnMB()
	if limit <= 0 {
		return ""
	}
	size := scratchBytes(root, until)
	if float64(size) <= limit*mb {
		return ""
	}
	values := texts.Values{
		"size":  strconv.FormatFloat(float64(size)/mb, 'f', 0, 64) + " MB",
		"limit": strconv.FormatFloat(limit, 'g', 6, 64) + " MB",
		"path":  place.ScratchDir(root),
	}
	return texts.Say(root, "NOTICE_SCRATCHPAD_LARGE", values, nil)
}
