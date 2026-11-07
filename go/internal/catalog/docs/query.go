package docs

import (
	"math/big"
	"slices"
	"sort"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/catalog/docindex"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/records/wiki"
)

// Sort は `--sort` の並べ方。空は SortPath。
type Sort string

const (
	SortPath  Sort = "path"
	SortMtime Sort = "mtime"
	SortType  Sort = "type"
	SortTitle Sort = "title"
)

// sortFields は frontmatter の値で並べる並べ方と、その欄。
var sortFields = map[Sort]string{SortType: fieldType, SortTitle: fieldTitle}

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

// frontmatter のキーのうち、引き方と出力が名前で読むもの。
const (
	fieldType        = "type"
	fieldTitle       = "title"
	fieldDescription = "description"
	fieldTags        = "tags"
	fieldKeywords    = "keywords"
)

// Query は `--docs` の問い。同じ欄の値どうしは OR、欄どうしは AND で絞る。大文字小文字は区別せず、NFC にそろえて比べる。
// Types・Tags・Keywords は値の完全一致（スカラーもリストとして扱う）。Paths は concept_id への部分一致。
// Texts は concept_id・mtime・frontmatter のすべてのスカラーの値への部分一致（キー名は含まない）。
// Since・Until は mtime と文字列で比べる。Until は書いた桁の終わりまで延ばす。Limit が 0 なら切らない。
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

// when の形。書き誤った値が 0 件の結果に紛れて気づかれないのを避けるため、読めない日時は断る。
var whenShape = pyre.MustCompile(`^\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?$`, 0)

const (
	strptimeY = `(?P<Y>\d\d\d\d)`
	strptimeM = `-(?P<m>1[0-2]|0[1-9]|[1-9])`
	strptimeD = `-(?P<d>3[01]|[12]\d|0[1-9]|[1-9]| [1-9])`
	strptimeH = `T(?P<H>2[0-3]|[0-1]\d|\d)`
	strptimeI = `:(?P<M>[0-5]\d|\d)`
	strptimeS = `:(?P<S>6[0-1]|[0-5]\d|\d)`
)

// whenPieces は日時の桁の区切り（頭から）。text の字数が形の長さを決め、--until は後ろの区切りの text（その桁の
// 最大）を足して延ばす。strptime は datetime.strptime の式（Python 3.13 の _strptime が組む式と同じ）。
var whenPieces = []struct{ text, strptime string }{
	{"YYYY-MM-DD", strptimeY + strptimeM + strptimeD},
	{"T23", strptimeH},
	{":59", strptimeI},
	{":59", strptimeS},
}

// whenForm は字数ごとの日時の形。re は書いた桁までの strptime の式、tail は --until を延ばす尾。
type whenForm struct {
	re   *pyre.Regexp
	tail string
}

// whenForms は字数から形（10・13・16・19 字）。
var whenForms = func() map[int]whenForm {
	forms := map[int]whenForm{}
	pattern, width := "", 0
	for i, p := range whenPieces {
		pattern += p.strptime
		width += utf8.RuneCountInString(p.text)
		var tail strings.Builder
		for _, rest := range whenPieces[i+1:] {
			tail.WriteString(rest.text)
		}
		forms[width] = whenForm{pyre.MustCompile(pattern, pyre.IgnoreCase), tail.String()}
	}
	return forms
}()

// validWhen は `--since`・`--until` に受ける日時か（datetime.strptime が読めるか）。
func validWhen(value string) bool {
	if !whenShape.Search(value) {
		return false
	}
	runes := []rune(value)
	form, ok := whenForms[len(runes)]
	if !ok {
		return false
	}
	found, ok := form.re.MatchIn(value, 0, len(runes))
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
		s, err := docindex.Compact(x)
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

// scalars は入れ子のスカラーの値を、書かれた順に。
func scalars(v any) []string {
	var found []string
	docindex.Walk(v, func(node any, _ int) bool {
		switch node.(type) {
		case *pyjson.Object, []any, nil:
		default:
			found = append(found, text(node))
		}
		return true
	})
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
	if form, ok := whenForms[utf8.RuneCountInString(until)]; ok {
		return until + form.tail
	}
	return until
}

func matches(r docindex.Row, q Query) bool {
	types := needles(q.Types)
	// 手順書を作る側の材料は、--type で明示したときだけ引く（作業のセッションに出さない）。
	if fold(str(r, fieldType)) == wiki.DocType && !slices.Contains(types, wiki.DocType) {
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
		default:
			if field, ok := sortFields[q.sort()]; ok {
				k.first = fold(str(r, field))
			}
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
