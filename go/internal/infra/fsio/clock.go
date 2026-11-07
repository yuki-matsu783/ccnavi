package fsio

import (
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/pyfmt"
)

// StampLayout は、マーカーと記録に書く時刻の形。Python の "%Y-%m-%dT%H:%M:%S%z" に当たる。
const StampLayout = LocalStampLayout + "-0700"

// LocalStampLayout は StampLayout の時差の前まで（"%Y-%m-%dT%H:%M:%S"、19 字）。
const LocalStampLayout = "2006-01-02T15:04:05"

// 名前に入れる時刻の形。どれも名前の順と時刻の順が合う。
const (
	// IDStampLayout は識別子に入れる 14 桁の時刻（"%Y%m%d%H%M%S"）。
	IDStampLayout = "20060102150405"
	// NameStampLayout はローテートした記録・退避した版の名前の頭の時刻（"%Y%m%d-%H%M%S"）。
	NameStampLayout = "20060102-150405"
)

// ISOStampLayout は、時差にコロンを入れた ISO 8601 の時刻（Python の datetime.isoformat の秒まで）。
// 判定の記録と診断ログが書く。
const ISOStampLayout = "2006-01-02T15:04:05-07:00"

// ISOFormat は Python の t.isoformat()。マイクロ秒が 0 でなければ秒の後ろに 6 桁で書く。
func ISOFormat(t time.Time) string {
	if t.Nanosecond()/1000 == 0 {
		return t.Format(ISOStampLayout)
	}
	return t.Format(strings.Replace(ISOStampLayout, "05", "05.000000", 1))
}

// stampFormat は、エラーの文に出す Python の書式の表記。
const stampFormat = "%Y-%m-%dT%H:%M:%S%z"

// fixedStamp は FixClock で固定した時刻。空なら手元の時計を読む。
var fixedStamp string

// Stamp は、マーカーと記録に書く時刻。手元の時計の値で、オフセット付き。FixClock の間は固定した時刻を返す。
func Stamp() string {
	if fixedStamp != "" {
		return fixedStamp
	}
	return time.Now().Format(StampLayout)
}

// FixClock は、返した関数を呼ぶまで Stamp を fixed に固定する。fixed が空なら固定しない（外側の固定は残る）。
//
// 承認の plan が書く時刻を固定する。Chrome と手元が同じ入力から同じバイト列を出すために使う。
// fixed が StampLayout の形でなければ、固定せずにエラーを返す。
func FixClock(fixed string) (restore func(), err error) {
	before := fixedStamp
	if fixed != "" {
		if !validStamp(fixed) {
			return func() {}, fmt.Errorf("時刻の形が違う（%s で読めない）: %s", stampFormat, pyfmt.Quote(fixed))
		}
		fixedStamp = fixed
	}
	return func() { fixedStamp = before }, nil
}

// stampPattern は、Python の strptime が "%Y-%m-%dT%H:%M:%S%z" で受ける形。T は大文字でも小文字でもよい。
// 月日時分秒は 1 桁でもよい。%z は Z、±HHMM、±HH:MM を受け、秒と小数秒も付けられる。
var stampPattern = regexp.MustCompile(`^(\d{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])[Tt]` +
	`(2[0-3]|[0-1]\d|\d):([0-5]\d|\d):(6[0-1]|[0-5]\d|\d)(Z|[+-](\d\d):?[0-5]\d(:?[0-5]\d(\.\d{1,6})?)?)$`)

// validStamp は、strptime が受けて datetime が作れる時刻かを調べる。2 月 30 日や 60 秒は作れない。
func validStamp(text string) bool {
	m := stampPattern.FindStringSubmatch(text)
	if m == nil {
		return false
	}
	num := func(s string) int {
		n, _ := strconv.Atoi(s)
		return n
	}
	year, month, day := num(m[1]), num(m[2]), num(m[3][len(m[3])-1:])
	if len(m[3]) == 2 && m[3][0] != ' ' {
		day = num(m[3])
	}
	if num(m[6]) > 59 || (m[8] != "" && num(m[8]) > 23) || year < 1 {
		return false
	}
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC).Day() == day
}

// UnixSeconds は、記録に書く UNIX 秒。Python の time.time() と同じ小数の形。
func UnixSeconds(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

// FromUnixSeconds は、記録の UNIX 秒を時刻に戻す。小数部分はナノ秒に丸める。
func FromUnixSeconds(f float64) time.Time {
	whole, frac := math.Modf(f)
	return time.Unix(int64(whole), int64(math.Round(frac*1e9)))
}
