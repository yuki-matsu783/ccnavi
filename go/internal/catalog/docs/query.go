package docs

import (
	"math/big"
	"slices"
	"sort"
	"strings"

	"ccnavi/internal/catalog/docindex"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
)

// Sort は `--sort` の並べ方。空は SortPath。
type Sort string

const (
	SortPath  Sort = "path"
	SortMtime Sort = "mtime"
	SortType  Sort = "type"
	SortTitle Sort = "title"
)

// sorts は受ける並べ方（苦情に並べる順）。
var sorts = []Sort{SortPath, SortMtime, SortType, SortTitle}

// Format は `--format` の出力の形。空は FormatTable。
type Format string

const (
	FormatTable  Format = "table"
	FormatPath   Format = "path"
	FormatDetail Format = "detail"
	FormatJSON   Format = "json"
	FormatJSONL  Format = "jsonl"
	FormatCount  Format = "count"
)

// formats は受ける出力の形（苦情に並べる順）。
var formats = []Format{FormatTable, FormatPath, FormatDetail, FormatJSON, FormatJSONL, FormatCount}

// learningWikiType は手順書を作る側の材料の type。--type で明示したときだけ引く（作業のセッションに出さない）。
const learningWikiType = "learning-wiki"

// frontmatter のキーのうち、引き方と出力が名前で読むもの。
const (
	fieldType        = "type"
	fieldTitle       = "title"
	fieldDescription = "description"
	fieldTags        = "tags"
	fieldKeywords    = "keywords"
)

// Query は `--docs` の問い。同じ欄の値どうしは OR、欄どうしは AND。大文字小文字は区別せず、NFC にそろえて
// 比べる。Types・Tags・Keywords は値との完全一致（スカラーもリストとして扱う）、Paths は concept_id への部分一致、
// Texts は concept_id・mtime・frontmatter のすべてのスカラーの値（キー名は含まない）への部分一致。
// Since・Until は mtime と文字列で比べ、Until は書いた桁の終わりまで延ばす。Limit が 0 なら切らない。
type Query struct {
	Types, Tags, Keywords, Paths, Texts []string
	Since, Until                        string
	Sort                                Sort
	Reverse                             bool
	Limit                               int
	Format                              Format
}

func (q Query) sort() Sort {
	if q.Sort == "" {
		return SortPath
	}
	return q.Sort
}

func (q Query) format() Format {
	if q.Format == "" {
		return FormatTable
	}
	return q.Format
}

// when の形と、その形ごとの datetime.strptime の式（Python 3.13 の _strptime が組む式と同じ）。書き誤った値が
// 気づかないうちに 0 件になるのを避けるため、読めない日時は断る。
var (
	whenShape = mustPyre(`^\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?$`, 0)
	whenParts = map[int]*pyre.Regexp{}
)

const (
	strptimeY = `(?P<Y>\d\d\d\d)`
	strptimeM = `-(?P<m>1[0-2]|0[1-9]|[1-9])`
	strptimeD = `-(?P<d>3[01]|[12]\d|0[1-9]|[1-9]| [1-9])`
	strptimeH = `T(?P<H>2[0-3]|[0-1]\d|\d)`
	strptimeI = `:(?P<M>[0-5]\d|\d)`
	strptimeS = `:(?P<S>6[0-1]|[0-5]\d|\d)`
)

func init() {
	pattern := strptimeY + strptimeM + strptimeD
	whenParts[10] = mustPyre(pattern, pyre.IgnoreCase)
	pattern += strptimeH
	whenParts[13] = mustPyre(pattern, pyre.IgnoreCase)
	pattern += strptimeI
	whenParts[16] = mustPyre(pattern, pyre.IgnoreCase)
	pattern += strptimeS
	whenParts[19] = mustPyre(pattern, pyre.IgnoreCase)
}

func mustPyre(pattern string, flags pyre.Flag) *pyre.Regexp {
	re, err := pyre.Compile(pattern, flags)
	if err != nil {
		panic(err) // 固定の式
	}
	return re
}

// validWhen は `--since`・`--until` に受ける日時か（datetime.strptime が読めるか）。
func validWhen(value string) bool {
	if !whenShape.Search(value) {
		return false
	}
	runes := []rune(value)
	re, ok := whenParts[len(runes)]
	if !ok {
		return false
	}
	found, ok := re.MatchIn(value, 0, len(runes))
	if !ok || found.End != len(runes) {
		return false
	}
	field := func(name string, fallback int) int {
		text, ok := found.Named(name)
		if !ok || text == "" {
			return fallback
		}
		n, err := pyfmt.ParseInt(text)
		if err != nil {
			return -1
		}
		return n
	}
	year, month, day := field("Y", 0), field("m", 1), field("d", 1)
	if year < 1 || month < 1 || month > 12 || day < 1 || day > daysIn(year, month) {
		return false
	}
	return field("H", 0) <= 23 && field("M", 0) <= 59 && field("S", 0) <= 59
}

func daysIn(year, month int) int {
	switch month {
	case 2:
		if year%4 == 0 && (year%100 != 0 || year%400 == 0) {
			return 29
		}
		return 28
	case 4, 6, 9, 11:
		return 30
	}
	return 31
}

// usageProblems は使い方の誤り。空なら引ける。
func usageProblems(q Query) []string {
	var found []string
	if !slices.Contains(sorts, q.sort()) {
		found = append(found, "--sort は "+joined(sorts)+" のどれか（"+pyfmt.Repr(string(q.Sort))+" は指定できない）")
	}
	if !slices.Contains(formats, q.format()) {
		found = append(found, "--format は "+joined(formats)+" のどれか（"+pyfmt.Repr(string(q.Format))+" は指定できない）")
	}
	for _, f := range []struct{ flag, value string }{{"--since", q.Since}, {"--until", q.Until}} {
		if f.value != "" && !validWhen(f.value) {
			found = append(found, f.flag+" には日時を YYYY-MM-DD[THH[:MM[:SS]]] の形で書く（"+pyfmt.Repr(f.value)+" は読めない）")
		}
	}
	return found
}

func joined[T ~string](values []T) string {
	parts := make([]string, len(values))
	for i, v := range values {
		parts[i] = string(v)
	}
	return strings.Join(parts, " / ")
}

// text は値を比べる・出す文字列にする（Python の _text）。None は空、真偽は true・false、入れ子は詰めた JSON。
func text(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case bool:
		if x {
			return "true"
		}
		return "false"
	case *pyjson.Object, []any:
		s, err := pyjson.Dumps(x, pyjson.Options{RawUnicode: true, Compact: true})
		if err != nil {
			return ""
		}
		return s
	case *big.Int:
		return x.String()
	}
	return pyfmt.Str(v)
}

func str(r docindex.Row, key string) string { return text(r.Front().Value(key)) }

func arr(r docindex.Row, key string) []string {
	switch x := r.Front().Value(key).(type) {
	case nil:
		return nil
	case []any:
		out := make([]string, len(x))
		for i, v := range x {
			out[i] = text(v)
		}
		return out
	default:
		return []string{text(x)}
	}
}

// scalars は入れ子のスカラーの値を、書かれた順に。再帰しない。
func scalars(v any) []string {
	var found []string
	stack := []any{v}
	for len(stack) > 0 {
		item := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		switch x := item.(type) {
		case *pyjson.Object:
			if x == nil {
				continue
			}
			var values []any
			for _, inner := range x.All() {
				values = append(values, inner)
			}
			for i := len(values) - 1; i >= 0; i-- {
				stack = append(stack, values[i])
			}
		case []any:
			for i := len(x) - 1; i >= 0; i-- {
				stack = append(stack, x[i])
			}
		case nil:
		default:
			found = append(found, text(x))
		}
	}
	return found
}

// fold は比べる形。NFC にそろえて小文字にする。
func fold(s string) string { return pyfmt.Lower(pyfmt.NFC(s)) }

func searchable(r docindex.Row) string {
	parts := append([]string{r.ConceptID(), r.Mtime()}, scalars(r.Front())...)
	return fold(strings.Join(parts, "\n"))
}

func needles(values []string) []string {
	var out []string
	for _, v := range values {
		if v != "" {
			out = append(out, fold(v))
		}
	}
	return out
}

// untilEnd は `--until` を書いた桁の終わりまで延ばす（mtime は秒までの文字列で比べるので、日付だけのままだと
// その日の分が残らない）。
func untilEnd(until string) string {
	switch len([]rune(until)) {
	case 10:
		return until + "T23:59:59"
	case 13:
		return until + ":59:59"
	case 16:
		return until + ":59"
	}
	return until
}

func matches(r docindex.Row, q Query) bool {
	types := needles(q.Types)
	if fold(str(r, fieldType)) == learningWikiType && !slices.Contains(types, learningWikiType) {
		return false
	}
	exact := func(needles, hay []string) bool {
		if len(needles) == 0 {
			return true
		}
		folded := map[string]bool{}
		for _, h := range hay {
			folded[fold(h)] = true
		}
		return slices.ContainsFunc(needles, func(n string) bool { return folded[n] })
	}
	partial := func(needles []string, hay func() string) bool {
		if len(needles) == 0 {
			return true
		}
		h := hay()
		return slices.ContainsFunc(needles, func(n string) bool { return strings.Contains(h, n) })
	}
	mtime := r.Mtime()
	until := untilEnd(q.Until)
	return exact(types, []string{str(r, fieldType)}) &&
		exact(needles(q.Tags), arr(r, fieldTags)) &&
		exact(needles(q.Keywords), arr(r, fieldKeywords)) &&
		partial(needles(q.Paths), func() string { return fold(r.ConceptID()) }) &&
		partial(needles(q.Texts), func() string { return searchable(r) }) &&
		(q.Since == "" || mtime >= q.Since) &&
		(until == "" || mtime <= until)
}

// search は出す行と、絞った後で Limit で切る前の数。
func search(rows []docindex.Row, q Query) ([]docindex.Row, int) {
	var hits []docindex.Row
	for _, r := range rows {
		if matches(r, q) {
			hits = append(hits, r)
		}
	}
	type keyed struct {
		first, id string
		row       docindex.Row
	}
	keys := make([]keyed, len(hits))
	for i, r := range hits {
		id := pyfmt.NFC(r.ConceptID())
		k := keyed{id: id, row: r}
		switch q.sort() {
		case SortMtime:
			k.first = r.Mtime()
		case SortType, SortTitle:
			k.first = fold(str(r, string(q.sort())))
		}
		keys[i] = k
	}
	sort.SliceStable(keys, func(i, j int) bool {
		if keys[i].first != keys[j].first {
			return keys[i].first < keys[j].first
		}
		return keys[i].id < keys[j].id
	})
	for i, k := range keys {
		hits[i] = k.row
	}
	if q.Reverse {
		slices.Reverse(hits)
	}
	matched := len(hits)
	if q.Limit > 0 && len(hits) > q.Limit {
		hits = hits[:q.Limit]
	}
	return hits, matched
}
