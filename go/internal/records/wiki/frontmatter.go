package wiki

import (
	"errors"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
)

// headLimit は frontmatter を探す頭の長さ（字数）。
const headLimit = 64 * 1024

const (
	fence    = "---"
	docEnd   = "..."
	byteMark = "\uFEFF"
)

// frontMatter は頭の frontmatter（--- で囲んだ YAML）のマップ。読めなければ理由。
func frontMatter(text string) (*yaml.Map, string) {
	lines := ospath.Splitlines(strings.TrimLeft(pyfmt.Head(text, headLimit), byteMark))
	if len(lines) == 0 || rstrip(lines[0]) != fence {
		return nil, "frontmatter が無い"
	}
	end := 0
	for i := 1; i < len(lines); i++ {
		if line := rstrip(lines[i]); line == fence || line == docEnd {
			end = i
			break
		}
	}
	if end == 0 {
		return nil, "frontmatter が閉じていない"
	}
	data, err := yaml.Load(strings.Join(lines[1:end], "\n"))
	if err != nil {
		var yerr *yaml.Error
		if !errors.As(err, &yerr) {
			return nil, "frontmatter を YAML として読めない"
		}
		return nil, "frontmatter を YAML として読めない (" + string(yerr.Class()) + ")"
	}
	m, ok := data.(*yaml.Map)
	if !ok {
		return nil, "frontmatter が対応表（key: value）でない"
	}
	return m, ""
}

func rstrip(s string) string { return strings.TrimRightFunc(s, ospath.IsSpace) }

// text は文字列の欄。無い・null は空。
func text(data *yaml.Map, name string, required bool) (string, string) {
	value := data.Get(name)
	if value == nil {
		if required {
			return "", name + " が無い"
		}
		return "", ""
	}
	s, ok := value.(string)
	if !ok {
		return "", name + " が文字列でない"
	}
	if required && ospath.Strip(s) == "" {
		return "", name + " が空"
	}
	return ospath.Strip(s), ""
}

// strs は文字列の一覧の欄。無い・null は空の一覧。
func strs(data *yaml.Map, name string) ([]string, string) {
	value := data.Get(name)
	if value == nil {
		return nil, ""
	}
	list, ok := yaml.Strings(value)
	if ok {
		for _, v := range list {
			if ospath.Strip(v) == "" {
				ok = false
			}
		}
	}
	if !ok {
		return nil, name + " が空でない文字列の一覧でない"
	}
	out := make([]string, len(list))
	for i, v := range list {
		out[i] = ospath.Strip(v)
	}
	return out, ""
}

func unique(values []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, v := range values {
		if !seen[key(v)] {
			seen[key(v)] = true
			out = append(out, v)
		}
	}
	return out
}

func target(data *yaml.Map) (Target, bool) {
	s, ok := data.Get("target").(string)
	if !ok {
		return "", false
	}
	for _, t := range Targets {
		if string(t) == s {
			return t, true
		}
	}
	return "", false
}

func targetProblem() string { return "target が " + targetsText() + " のどれでもない" }

// ParsePattern はパターンの frontmatter を読む。壊れていれば理由（空でない文字列）。
func ParsePattern(name, body string) (Pattern, string) {
	data, problem := frontMatter(body)
	if problem != "" {
		return Pattern{}, problem
	}
	if s, ok := data.Get("type").(string); !ok || s != patternType {
		return Pattern{}, "type が " + patternType + " でない"
	}
	t, ok := target(data)
	if !ok {
		return Pattern{}, targetProblem()
	}
	sources, problem := strs(data, "sources")
	if problem != "" {
		return Pattern{}, problem
	}
	if len(sources) == 0 {
		return Pattern{}, "sources が空（観測元が無い）"
	}
	title, problem := text(data, "title", false)
	if problem == "" {
		_, problem = text(data, "description", false)
	}
	for _, other := range []string{"tags", "keywords"} {
		if problem == "" {
			_, problem = strs(data, other)
		}
	}
	if problem != "" {
		return Pattern{}, problem
	}
	return Pattern{Name: name, Target: t, Sources: unique(sources), Title: title}, ""
}

// counts は patterns（パターン名から観測元の数）。同じ名前（前後の空白を除いて）は後の数で置き換える。
func counts(value any) ([]PatternCount, string) {
	if value == nil {
		return nil, ""
	}
	m, ok := value.(*yaml.Map)
	if !ok {
		return nil, "patterns がパターン名から観測元の数への対応表でない"
	}
	var out []PatternCount
	index := map[string]int{}
	for k, v := range m.All() {
		name, ok := k.(string)
		if !ok || ospath.Strip(name) == "" {
			return nil, "patterns のパターン名が文字列でない"
		}
		n, ok := count(v)
		if !ok {
			return nil, "patterns の " + name + " の数が 0 以上の整数でない"
		}
		name = ospath.Strip(name)
		if i, seen := index[name]; seen {
			out[i].Count = n
			continue
		}
		index[name] = len(out)
		out = append(out, PatternCount{name, n})
	}
	return out, ""
}

// count は 0 以上の整数（真偽を除く）。int に収まらない数は上限にする。
func count(v any) (int, bool) {
	switch x := v.(type) {
	case int:
		return x, x >= 0
	case *big.Int:
		return math.MaxInt, x.Sign() >= 0
	}
	return 0, false
}

func decision(data *yaml.Map) (Decision, bool) {
	s, ok := data.Get("status").(string)
	if !ok {
		return "", false
	}
	for _, d := range decisions {
		if string(d) == s {
			return d, true
		}
	}
	return "", false
}

// parseImpact は採否の記録の frontmatter を読む。壊れていれば理由。
func parseImpact(body string) (Impact, string) {
	data, problem := frontMatter(body)
	if problem != "" {
		return Impact{}, problem
	}
	proposal, problem := text(data, "proposal", true)
	if problem != "" {
		return Impact{}, problem
	}
	t, ok := target(data)
	if !ok {
		return Impact{}, targetProblem()
	}
	files, problem := strs(data, "files")
	if problem != "" {
		return Impact{}, problem
	}
	patterns, problem := counts(data.Get("patterns"))
	if problem != "" {
		return Impact{}, problem
	}
	status, ok := decision(data)
	if !ok {
		return Impact{}, "status が " + decisionsText() + " のどれでもない"
	}
	reason, problem := text(data, "reason", false)
	if problem != "" {
		return Impact{}, problem
	}
	var decided *time.Time
	if raw := data.Get("decided_at"); raw != nil && raw != "" {
		when, problem := moment(raw)
		if problem != "" {
			return Impact{}, problem
		}
		decided = &when
	} else if status != Pending {
		return Impact{}, "decided_at が無い（承認待ちのほかは日時が要る）"
	}
	return Impact{proposal, t, files, patterns, status, reason, decided}, ""
}

// moment は decided_at。タイムゾーン付きの日時か、読めない理由。
func moment(value any) (time.Time, string) {
	switch v := value.(type) {
	case yaml.DateTime:
		if !v.HasZone {
			return time.Time{}, noZone
		}
		return v.Time(), ""
	case string:
		when, zoned, ok := fromISOFormat(ospath.Strip(v))
		if !ok {
			return time.Time{}, "decided_at が日時として読めない"
		}
		if !zoned {
			return time.Time{}, noZone
		}
		return when, ""
	}
	return time.Time{}, "decided_at が日時でない"
}

const noZone = "decided_at にタイムゾーンが無い"

// fromISOFormat は Python の datetime.fromisoformat の主な綴り。日付は YYYY-MM-DD か YYYYMMDD、
// 続けて任意の 1 字の区切りと、時刻 HH[:MM[:SS[.f…]]]（区切りなしも可）、時差 Z・±HH[:MM[:SS[.f…]]]。
// zoned は時差が書かれていたか。
func fromISOFormat(s string) (time.Time, bool, bool) {
	var year, month, day int
	var rest string
	switch {
	case len(s) >= 10 && s[4] == '-' && s[7] == '-':
		year, month, day = atoi(s[0:4]), atoi(s[5:7]), atoi(s[8:10])
		if !digits(s[0:4]) || !digits(s[5:7]) || !digits(s[8:10]) {
			return time.Time{}, false, false
		}
		rest = s[10:]
	case len(s) >= 8 && digits(s[0:8]):
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
	if len(parts) == 0 || len(parts) > 3 || (hasFrac && (len(parts) != 3 || frac == "" || !digits(frac))) {
		return 0, false
	}
	limits := []int{23, 59, 59}
	var d time.Duration
	units := []time.Duration{time.Hour, time.Minute, time.Second}
	for i, p := range parts {
		if len(p) != 2 || !digits(p) || atoi(p) > limits[i] {
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

func digits(s string) bool {
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

func atoi(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}
