package yaml

import (
	"bytes"
	"fmt"
	"iter"
	"math"
	"math/big"
	"reflect"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/pyfmt"
)

// Map は YAML のマップ。Python の dict と同じく入れた順を保ち、キーは Python の等しさで比べる
// （1・1.0・true は同じキー。後から来た値で上書きし、位置とキーは最初のまま）。
// 取り出しの関数は nil の *Map でも使え、欄が無い・型が違うときは既定の値を返す。
type Map struct {
	keys  []any
	vals  []any
	index map[string]int // 文字列のキーの位置
}

// Len は要素の数。
func (m *Map) Len() int {
	if m == nil {
		return 0
	}
	return len(m.keys)
}

// All は入れた順に (キー, 値) を返す。
func (m *Map) All() iter.Seq2[any, any] {
	return func(yield func(any, any) bool) {
		if m == nil {
			return
		}
		for i, k := range m.keys {
			if !yield(k, m.vals[i]) {
				return
			}
		}
	}
}

// Keys は入れた順のキー。
func (m *Map) Keys() []any {
	if m == nil {
		return nil
	}
	return append([]any(nil), m.keys...)
}

// StringKeys は、キーがすべて文字列なら入れた順のキーと真。文字列でないキーがあれば偽。
func (m *Map) StringKeys() ([]string, bool) {
	out := make([]string, 0, m.Len())
	for k := range m.All() {
		s, ok := k.(string)
		if !ok {
			return nil, false
		}
		out = append(out, s)
	}
	return out, true
}

func (m *Map) find(key any) int {
	if m == nil {
		return -1
	}
	if s, ok := key.(string); ok {
		if i, ok := m.index[s]; ok {
			return i
		}
		return -1
	}
	for i, k := range m.keys {
		if keyEqual(k, key) {
			return i
		}
	}
	return -1
}

// set はキーの値を置く。同じキー（Python の等しさ）があれば値だけを変える。
func (m *Map) set(key, value any) {
	if i := m.find(key); i >= 0 {
		m.vals[i] = value
		return
	}
	if s, ok := key.(string); ok {
		if m.index == nil {
			m.index = map[string]int{}
		}
		m.index[s] = len(m.keys)
	}
	m.keys = append(m.keys, key)
	m.vals = append(m.vals, value)
}

// Without は keys の欄を除いた写し（Python の {k: v for k, v in m.items() if k not in keys}）。順は保つ。
func (m *Map) Without(keys ...string) *Map {
	out := &Map{}
	for k, v := range m.All() {
		if s, ok := k.(string); ok && slices.Contains(keys, s) {
			continue
		}
		out.Put(k, v)
	}
	return out
}

// Lookup は key の値と、その欄があるか（Python の key in m）。
func (m *Map) Lookup(key any) (any, bool) {
	if i := m.find(key); i >= 0 {
		return m.vals[i], true
	}
	return nil, false
}

// Has は key の欄があるか。
func (m *Map) Has(key string) bool {
	_, ok := m.Lookup(key)
	return ok
}

// Get は key の値。無ければ nil（Python の m.get(key)）。
func (m *Map) Get(key string) any {
	v, _ := m.Lookup(key)
	return v
}

// Text は Python の str(m.get(key) or "")。偽の値（無い・null・false・0・空）は空文字列、
// ほかは Python の str() と同じ文字列（数は 10 進、真偽は True、日付は 2026-01-02）。
func (m *Map) Text(key string) string {
	v := m.Get(key)
	if !pyfmt.Truthy(v) {
		return ""
	}
	return pyfmt.Str(v)
}

// String は値が文字列ならそれと真。無い・文字列でなければ ("", false)。
func (m *Map) String(key string) (string, bool) {
	s, ok := m.Get(key).(string)
	return s, ok
}

// Strings は値が文字列だけの列ならそれと真。無い・列でない・文字列でない要素があれば (nil, false)。
func (m *Map) Strings(key string) ([]string, bool) {
	return Strings(m.Get(key))
}

// List は値が列ならそれと真。
func (m *Map) List(key string) ([]any, bool) {
	l, ok := m.Get(key).([]any)
	return l, ok
}

// Map は値がマップならそれ。無い・マップでなければ nil（nil の *Map の取り出しは既定の値を返す）。
func (m *Map) Map(key string) *Map {
	sub, _ := m.Get(key).(*Map)
	return sub
}

// Bool は値が真偽ならそれと真（Python の isinstance(v, bool)）。
func (m *Map) Bool(key string) (bool, bool) {
	b, ok := m.Get(key).(bool)
	return b, ok
}

// Int は値が整数（真偽を除く。int に収まるもの）ならそれと真。
func (m *Map) Int(key string) (int, bool) {
	n, ok := m.Get(key).(int)
	return n, ok
}

// Strings は v が文字列だけの列ならそれと真。
func Strings(v any) ([]string, bool) {
	l, ok := v.([]any)
	if !ok {
		return nil, false
	}
	out := make([]string, 0, len(l))
	for _, item := range l {
		s, ok := item.(string)
		if !ok {
			return nil, false
		}
		out = append(out, s)
	}
	return out, true
}

// Set は !!set の値（Python の set）。並びは入れた順（Python の set の並びは決まらない）。
type Set []any

// PyRepr は Python の repr(set)。
func (s Set) PyRepr() string {
	if len(s) == 0 {
		return "set()"
	}
	parts := make([]string, len(s))
	for i, v := range s {
		parts[i] = pyfmt.Repr(v)
	}
	return "{" + strings.Join(parts, ", ") + "}"
}

// Len は要素の数。
func (s Set) Len() int { return len(s) }

// Tuple は !!omap・!!pairs の 1 件（Python の (キー, 値) の組）。列の要素として入る。
type Tuple []any

// PyRepr は Python の repr(tuple)。
func (t Tuple) PyRepr() string {
	parts := make([]string, len(t))
	for i, v := range t {
		parts[i] = pyfmt.Repr(v)
	}
	if len(t) == 1 {
		return "(" + parts[0] + ",)"
	}
	return "(" + strings.Join(parts, ", ") + ")"
}

// Len は要素の数。
func (t Tuple) Len() int { return len(t) }

// Date は時刻の無い日付（Python の datetime.date）。YAML 1.1 では 2026-01-02 と書いた値がこれになる。
type Date struct {
	Year, Month, Day int
}

// ISOFormat は Python の date.isoformat()。
func (d Date) ISOFormat() string { return fmt.Sprintf("%04d-%02d-%02d", d.Year, d.Month, d.Day) }

// PyStr は Python の str(date)。
func (d Date) PyStr() string { return d.ISOFormat() }

// PyRepr は Python の repr(date)。
func (d Date) PyRepr() string {
	return fmt.Sprintf("datetime.date(%d, %d, %d)", d.Year, d.Month, d.Day)
}

// DateTime は日時（Python の datetime.datetime）。HasZone が偽ならタイムゾーンの無い日時。
type DateTime struct {
	Date
	Hour, Minute, Second, Microsecond int
	HasZone                           bool
	Offset                            time.Duration // UTC からのずれ（HasZone のときだけ意味を持つ）
}

// ISOFormat は Python の datetime.isoformat()（区切りは T）。
func (t DateTime) ISOFormat() string { return t.format("T") }

// PyStr は Python の str(datetime)（区切りは空白）。
func (t DateTime) PyStr() string { return t.format(" ") }

func (t DateTime) format(sep string) string {
	out := t.Date.ISOFormat() + sep + fmt.Sprintf("%02d:%02d:%02d", t.Hour, t.Minute, t.Second)
	if t.Microsecond != 0 {
		out += fmt.Sprintf(".%06d", t.Microsecond)
	}
	if t.HasZone {
		sign, off := "+", t.Offset
		if off < 0 {
			sign, off = "-", -off
		}
		out += fmt.Sprintf("%s%02d:%02d", sign, int(off.Hours()), int(off.Minutes())%60)
	}
	return out
}

// PyRepr は Python の repr(datetime)。
func (t DateTime) PyRepr() string {
	fields := []int{t.Year, t.Month, t.Day, t.Hour, t.Minute, t.Second, t.Microsecond}
	switch {
	case t.Microsecond != 0:
	case t.Second != 0:
		fields = fields[:6]
	default:
		fields = fields[:5]
	}
	parts := make([]string, len(fields))
	for i, f := range fields {
		parts[i] = fmt.Sprint(f)
	}
	out := "datetime.datetime(" + strings.Join(parts, ", ")
	if t.HasZone {
		if t.Offset == 0 {
			out += ", tzinfo=datetime.timezone.utc"
		} else {
			out += ", tzinfo=datetime.timezone(" + timedeltaRepr(t.Offset) + ")"
		}
	}
	return out + ")"
}

// Time は time.Time にする。タイムゾーンの無い日時は UTC として置く（HasZone で見分ける）。
func (t DateTime) Time() time.Time {
	loc := time.UTC
	if t.HasZone && t.Offset != 0 {
		loc = time.FixedZone("", int(t.Offset.Seconds()))
	}
	return time.Date(t.Year, time.Month(t.Month), t.Day, t.Hour, t.Minute, t.Second, t.Microsecond*1000, loc)
}

// timedeltaRepr は Python の repr(timedelta)。日と秒に正規化し、0 の欄は省く。
func timedeltaRepr(d time.Duration) string {
	secs := int64(d / time.Second)
	days := secs / 86400
	rest := secs % 86400
	if rest < 0 {
		days--
		rest += 86400
	}
	var parts []string
	if days != 0 {
		parts = append(parts, fmt.Sprintf("days=%d", days))
	}
	if rest != 0 {
		parts = append(parts, fmt.Sprintf("seconds=%d", rest))
	}
	if len(parts) == 0 {
		return "datetime.timedelta(0)"
	}
	return "datetime.timedelta(" + strings.Join(parts, ", ") + ")"
}

// Equal は読んだ値どうしの Python の a == b。マップはキーの順を見ず、数は値で比べる（True == 1 == 1.0）。
// 一番上の NaN は NaN と等しくないが、入れ物の中の NaN は等しい（Python の入れ物は同じ物を等しいと見なし、
// PyYAML の .nan は毎回同じ物を返す）。
func Equal(a, b any) bool {
	if x, ok := a.(float64); ok && math.IsNaN(x) {
		return false
	}
	return equal(a, b)
}

func equal(a, b any) bool {
	switch x := a.(type) {
	case *Map:
		y, ok := b.(*Map)
		if !ok || x.Len() != y.Len() {
			return false
		}
		for k, v := range x.All() {
			w, found := y.Lookup(k)
			if !found || !equal(v, w) {
				return false
			}
		}
		return true
	case []any:
		y, ok := b.([]any)
		return ok && slices.EqualFunc(x, y, equal)
	}
	return keyEqual(a, b) || reflect.DeepEqual(a, b)
}

// keyEqual は Python の == をマップのキーに当てる。数（真偽を含む）は値で比べる。
// NaN どうしは同じキーにする（PyYAML の .nan は毎回同じ値を返すので dict の上で 1 つになる）。
func keyEqual(a, b any) bool {
	if x, ok := number(a); ok {
		y, ok := number(b)
		return ok && x.equal(y)
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	case []byte:
		y, ok := b.([]byte)
		return ok && bytes.Equal(x, y)
	case Date:
		y, ok := b.(Date)
		return ok && x == y
	case DateTime:
		y, ok := b.(DateTime)
		if !ok || x.HasZone != y.HasZone {
			return false
		}
		if x.HasZone {
			return x.Time().Equal(y.Time())
		}
		return x == y
	}
	return false
}

type num struct {
	f     float64
	i     *big.Int
	float bool
}

func number(v any) (num, bool) {
	switch x := v.(type) {
	case bool:
		if x {
			return num{i: big.NewInt(1)}, true
		}
		return num{i: big.NewInt(0)}, true
	case int:
		return num{i: big.NewInt(int64(x))}, true
	case *big.Int:
		return num{i: x}, true
	case float64:
		return num{f: x, float: true}, true
	}
	return num{}, false
}

func (a num) equal(b num) bool {
	switch {
	case !a.float && !b.float:
		return a.i.Cmp(b.i) == 0
	case a.float && b.float:
		return a.f == b.f || math.IsNaN(a.f) && math.IsNaN(b.f)
	case a.float:
		a, b = b, a
	}
	if math.IsNaN(b.f) || math.IsInf(b.f, 0) {
		return false
	}
	return new(big.Float).SetInt(a.i).Cmp(big.NewFloat(b.f)) == 0
}
