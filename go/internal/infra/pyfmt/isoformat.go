package pyfmt

import (
	"strings"
	"time"
	"unicode/utf8"
)

// FromISOFormat は Python の datetime.fromisoformat の主な綴り。日付は YYYY-MM-DD か YYYYMMDD、
// 続けて任意の 1 字の区切りと、時刻 HH[:MM[:SS[.f…]]]（区切りなしも可）、時差 Z・±HH[:MM[:SS[.f…]]]。
// zoned は時差が書かれていたか。
func FromISOFormat(s string) (when time.Time, zoned, ok bool) {
	var year, month, day int
	var rest string
	switch {
	case len(s) >= 10 && s[4] == '-' && s[7] == '-':
		year, month, day = atoi(s[0:4]), atoi(s[5:7]), atoi(s[8:10])
		if !asciiDigits(s[0:4]) || !asciiDigits(s[5:7]) || !asciiDigits(s[8:10]) {
			return time.Time{}, false, false
		}
		rest = s[10:]
	case len(s) >= 8 && asciiDigits(s[0:8]):
		year, month, day = atoi(s[0:4]), atoi(s[4:6]), atoi(s[6:8])
		rest = s[8:]
	default:
		return time.Time{}, false, false
	}
	if !validDate(year, month, day) {
		return time.Time{}, false, false
	}
	if rest == "" {
		return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC), false, true
	}
	_, size := utf8.DecodeRuneInString(rest)
	clock, offset, zoned, ok := parseClock(rest[size:])
	if !ok {
		return time.Time{}, false, false
	}
	// 比べるだけなので UTC の時刻にする。
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC).Add(clock - offset), zoned, true
}

// parseClock は時刻と時差。clock は 0 時からの長さ。
func parseClock(s string) (clock, offset time.Duration, zoned, ok bool) {
	body, zone := s, ""
	if i := strings.IndexAny(s, "Z+-"); i >= 0 {
		body, zone = s[:i], s[i:]
	}
	clock, ok = parseTime(body)
	if !ok || clock >= 24*time.Hour {
		return 0, 0, false, false
	}
	switch {
	case zone == "":
		return clock, 0, false, true
	case zone == "Z":
		return clock, 0, true, true
	}
	offset, ok = parseTime(zone[1:])
	if !ok || offset >= 24*time.Hour {
		return 0, 0, false, false
	}
	if zone[0] == '-' {
		offset = -offset
	}
	return clock, offset, true, true
}

// parseTime は HH[:MM[:SS[.f…]]] か HH[MM[SS[.f…]]]。小数の秒は 6 桁まで読み、後ろは捨てる。
func parseTime(s string) (time.Duration, bool) {
	whole, frac, hasFrac := strings.Cut(s, ".")
	if !hasFrac {
		whole, frac, hasFrac = strings.Cut(s, ",")
	}
	var parts []string
	if strings.Contains(whole, ":") {
		parts = strings.Split(whole, ":")
	} else {
		for i := 0; i < len(whole); i += 2 {
			parts = append(parts, whole[i:min(i+2, len(whole))])
		}
	}
	if len(parts) == 0 || len(parts) > 3 || (hasFrac && (len(parts) != 3 || frac == "" || !asciiDigits(frac))) {
		return 0, false
	}
	limits := []int{23, 59, 59}
	var d time.Duration
	units := []time.Duration{time.Hour, time.Minute, time.Second}
	for i, p := range parts {
		if len(p) != 2 || !asciiDigits(p) || atoi(p) > limits[i] {
			return 0, false
		}
		d += time.Duration(atoi(p)) * units[i]
	}
	if hasFrac {
		micro := (frac + "000000")[:6]
		d += time.Duration(atoi(micro)) * time.Microsecond
	}
	return d, true
}

func validDate(year, month, day int) bool {
	if year < 1 || month < 1 || month > 12 || day < 1 {
		return false
	}
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC).Day() == day
}

// asciiDigits は ASCII の数字だけの並びか。fromisoformat は ASCII の外の数字（全角など）を読まない。
func asciiDigits(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}

// atoi は asciiDigits で確かめた並びの値。
func atoi(s string) int {
	n, _ := ParseInt(s)
	return n
}
