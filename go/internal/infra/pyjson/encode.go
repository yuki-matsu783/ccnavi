package pyjson

import (
	"errors"
	"fmt"
	"math"
	"math/big"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/pyfmt"
)

// Options は json.dumps の引数。ゼロ値は json.dumps(v) の既定（ensure_ascii=True、区切り ", " と ": "）。
type Options struct {
	// RawUnicode は ensure_ascii=False。ASCII でない文字を \uXXXX にしない。
	RawUnicode bool
	// Indent は indent=N。0 は indent=None（1 行に出す）。indent を付けると要素の区切りは "," になる。
	Indent int
	// SortKeys は sort_keys=True。
	SortKeys bool
	// Compact は separators=(",", ":")。
	Compact bool
	// RejectNaN は allow_nan=False。NaN・Infinity を出さずにエラーにする。
	RejectNaN bool
}

// ErrUnsupported は JSON に出せない値（Python の TypeError）。
var ErrUnsupported = errors.New("JSON に出せない値")

// ErrOutOfRange は RejectNaN のときの NaN・Infinity（Python の ValueError）。
var ErrOutOfRange = errors.New("Out of range float values are not JSON compliant")

// Dumps は json.dumps(v, ...) と同じ文字列を返す。
//
// 受ける値は nil・bool・整数・*big.Int・float64・string・[]any・[]string・*Object・map[string]any・
// pyfmt.Items（キーは文字列に限る）。map[string]any は順序を持たないので、キーを並べ替えて出す。
func Dumps(v any, opts Options) (string, error) {
	e := encoder{opts: opts, item: ", ", key: ": "}
	if opts.Indent > 0 {
		e.item = ","
	}
	if opts.Compact {
		e.item, e.key = ",", ":"
	}
	if err := e.value(v, 0); err != nil {
		return "", err
	}
	return e.b.String(), nil
}

type encoder struct {
	b         strings.Builder
	opts      Options
	item, key string
}

func (e *encoder) newline(level int) {
	if e.opts.Indent > 0 {
		e.b.WriteByte('\n')
		e.b.WriteString(strings.Repeat(" ", e.opts.Indent*level))
	}
}

func (e *encoder) value(v any, level int) error {
	switch x := v.(type) {
	case nil:
		e.b.WriteString("null")
	case bool:
		if x {
			e.b.WriteString("true")
		} else {
			e.b.WriteString("false")
		}
	case int:
		e.b.WriteString(strconv.Itoa(x))
	case int64:
		e.b.WriteString(strconv.FormatInt(x, 10))
	case int32:
		e.b.WriteString(strconv.FormatInt(int64(x), 10))
	case uint64:
		e.b.WriteString(strconv.FormatUint(x, 10))
	case *big.Int:
		e.b.WriteString(x.String())
	case float64:
		return e.float(x)
	case string:
		e.str(x)
	case []string:
		items := make([]any, len(x))
		for i, s := range x {
			items[i] = s
		}
		return e.list(items, level)
	case []any:
		return e.list(x, level)
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		slices.Sort(keys)
		o := &Object{}
		for _, k := range keys {
			o.Set(k, x[k])
		}
		return e.object(o, level)
	case pyfmt.Items:
		return e.object(x, level)
	default:
		return fmt.Errorf("%w: Object of type %T is not JSON serializable", ErrUnsupported, v)
	}
	return nil
}

func (e *encoder) float(f float64) error {
	var text string
	switch {
	case math.IsNaN(f):
		text = "NaN"
	case math.IsInf(f, 1):
		text = "Infinity"
	case math.IsInf(f, -1):
		text = "-Infinity"
	default:
		e.b.WriteString(pyfmt.Float(f))
		return nil
	}
	if e.opts.RejectNaN {
		return fmt.Errorf("%w: %s", ErrOutOfRange, pyfmt.Float(f))
	}
	e.b.WriteString(text)
	return nil
}

func (e *encoder) list(items []any, level int) error {
	if len(items) == 0 {
		e.b.WriteString("[]")
		return nil
	}
	e.b.WriteByte('[')
	for i, item := range items {
		if i > 0 {
			e.b.WriteString(e.item)
		}
		e.newline(level + 1)
		if err := e.value(item, level+1); err != nil {
			return err
		}
	}
	e.newline(level)
	e.b.WriteByte(']')
	return nil
}

func (e *encoder) object(o pyfmt.Items, level int) error {
	if o.Len() == 0 {
		e.b.WriteString("{}")
		return nil
	}
	type pair struct {
		key string
		val any
	}
	pairs := make([]pair, 0, o.Len())
	for k, v := range o.All() {
		key, ok := k.(string)
		if !ok {
			return fmt.Errorf("%w: keys must be str, not %T", ErrUnsupported, k)
		}
		pairs = append(pairs, pair{key, v})
	}
	if e.opts.SortKeys {
		slices.SortStableFunc(pairs, func(a, b pair) int { return strings.Compare(a.key, b.key) })
	}
	e.b.WriteByte('{')
	for i, p := range pairs {
		if i > 0 {
			e.b.WriteString(e.item)
		}
		e.newline(level + 1)
		e.str(p.key)
		e.b.WriteString(e.key)
		if err := e.value(p.val, level+1); err != nil {
			return err
		}
	}
	e.newline(level)
	e.b.WriteByte('}')
	return nil
}

// str は文字列を引用する。ensure_ascii=True は ASCII の表示できる文字（0x20〜0x7e）の外をすべて
// \uXXXX にし、BMP の外はサロゲートの組にする。False は 0x20 未満だけを逃がす。
func (e *encoder) str(s string) {
	b := &e.b
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			switch {
			case r < 0x20:
				fmt.Fprintf(b, `\u%04x`, r)
			case e.opts.RawUnicode || r <= 0x7e:
				b.WriteRune(r)
			case r <= 0xffff:
				fmt.Fprintf(b, `\u%04x`, r)
			default:
				r -= 0x10000
				fmt.Fprintf(b, `\u%04x\u%04x`, 0xd800+(r>>10), 0xdc00+(r&0x3ff))
			}
		}
	}
	b.WriteByte('"')
}
