package docs

import (
	"math/big"
	"slices"
	"sort"
	"strings"
	"time"
	"unicode"
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
	sortMtime Sort = "mtime"
	sortType  Sort = "type"
	sortTitle Sort = "title"
)

// sortFields は frontmatter の値で並べる並べ方と、その欄。
var sortFields = map[Sort]string{sortType: fieldType, sortTitle: fieldTitle}

// sorts は受ける並べ方（苦情に並べる順）。
var sorts = []Sort{SortPath, sortMtime, sortType, sortTitle}

// Format は `--format` の出力の形。空は FormatTable。
type Format string

const (
	FormatTable  Format = "table"
	formatPath   Format = "path"
	formatDetail Format = "detail"
	FormatJSON   Format = "json"
	formatJSONL  Format = "jsonl"
	formatCount  Format = "count"
)

// formats は受ける出力の形（苦情に並べる順）。
var formats = []Format{FormatTable, formatPath, formatDetail, FormatJSON, formatJSONL, formatCount}

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
// \d は全角などの数字にも当たる。strptime の式が \d で受ける桁（年の 4 桁、日・時・分・秒の 2 桁目）だけに許し、
// 式が 0-9 の字で書く桁（月、日・時の 1 桁目）は [0-9] にする。日の 2 桁目は 1 桁目が 1・2 のとき、時の 2 桁目は
// 0・1 のときだけ \d で受ける（3・2 のときは 3[01]・2[0-3] の字で受ける）。
var whenShape = pyre.MustCompile(`^\d{4}-[0-9]{2}-([12]\d|[03][0-9])(T([01]\d|2[0-9])(:[0-5]\d(:[0-5]\d)?)?)?$`, 0)

// whenForms は字数ごとの日時の形（10・13・16・19 字）。layout は time.Parse の形、tail は --until を延ばす尾
// （後ろの桁の最大）。
var whenForms = map[int]struct{ layout, tail string }{
	10: {"2006-01-02", "T23:59:59"},
	13: {"2006-01-02T15", ":59:59"},
	16: {"2006-01-02T15:04", ":59"},
	19: {"2006-01-02T15:04:05", ""},
}

// validWhen は `--since`・`--until` に受ける日時か（datetime.strptime が読めるか）。whenShape が桁の幅と数字の字を
// 決めるので、残るのは日時として在るかと、datetime が作れない 0 年。
func validWhen(value string) bool {
	if !whenShape.Search(value) {
		return false
	}
	form, ok := whenForms[utf8.RuneCountInString(value)]
	if !ok {
		return false
	}
	t, err := time.Parse(form.layout, asciiDigits(value))
	return err == nil && t.Year() >= 1
}

// asciiDigits は 10 進の数字を ASCII の数字にする。
func asciiDigits(value string) string {
	return strings.Map(func(r rune) rune {
		if r <= unicode.MaxASCII || !unicode.IsDigit(r) {
			return r
		}
		n, err := pyfmt.ParseInt(string(r))
		if err != nil {
			return r
		}
		return '0' + rune(n)
	}, value)
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
		case sortMtime:
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
