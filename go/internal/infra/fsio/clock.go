package fsio

import (
	"fmt"
	"regexp"
	"strconv"
	"time"

	"ccnavi/internal/infra/pyfmt"
)

// StampLayout はマーカーと記録に書く時刻の形（Python の "%Y-%m-%dT%H:%M:%S%z"）。
const StampLayout = "2006-01-02T15:04:05-0700"

// stampFormat はエラーの文に出す、Python の書式の表記。
const stampFormat = "%Y-%m-%dT%H:%M:%S%z"

// fixedStamp は FixClock で固定した時刻。空なら手元の時計を読む。
var fixedStamp string

// Stamp はマーカーと記録に書く時刻。手元の時計、オフセット付き。FixClock の間は固定した時刻。
func Stamp() string {
	if fixedStamp != "" {
		return fixedStamp
	}
	return time.Now().Format(StampLayout)
}

// FixClock は、返した関数を呼ぶまでの Stamp を fixed に固定する。空なら固定しない（外側の固定は残る）。
//
// 承認の plan が書く時刻を固定し、Chrome と手元が同じ入力から同じバイト列を出すために使う。
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

// stampPattern は Python の strptime が "%Y-%m-%dT%H:%M:%S%z" で受ける形。T は大文字小文字を問わず、
// 月日時分秒は 1 桁も受け、%z は Z・±HHMM・±HH:MM（秒と小数秒も付けられる）を受ける。
var stampPattern = regexp.MustCompile(`^(\d{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])[Tt]` +
	`(2[0-3]|[0-1]\d|\d):([0-5]\d|\d):(6[0-1]|[0-5]\d|\d)(Z|[+-](\d\d):?[0-5]\d(:?[0-5]\d(\.\d{1,6})?)?)$`)

// validStamp は strptime が受け、datetime が作れる時刻か（2 月 30 日や 60 秒は作れない）。
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
