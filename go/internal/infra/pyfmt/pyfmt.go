package pyfmt

import (
	"fmt"
	"iter"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"
)

// Items は順序つきの map。yaml.Map と pyjson.Object が満たす。
type Items interface {
	Len() int
	All() iter.Seq2[any, any]
}

// Reprer は repr() の形を自分で持つ値（日付・組・集合など）。
type Reprer interface {
	PyRepr() string
}

// Stringer は str() が repr() と違う値（日付など）。
type Stringer interface {
	PyStr() string
}

// Repr は Python の repr(v)。
func Repr(v any) string {
	var b strings.Builder
	writeRepr(&b, v)
	return b.String()
}

// Str は Python の str(v)。文字列はそのまま、ほかは repr と同じ（日付などは PyStr）。
func Str(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case Stringer:
		return x.PyStr()
	}
	return Repr(v)
}

// Truthy は Python の bool(v)。None・False・0・0.0・空の文字列・空の列・空の map が偽。
func Truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case int:
		return x != 0
	case int64:
		return x != 0
	case *big.Int:
		return x.Sign() != 0
	case float64:
		return x != 0
	case string:
		return x != ""
	case []byte:
		return len(x) > 0
	case []any:
		return len(x) > 0
	case []string:
		return len(x) > 0
	case Items:
		return x.Len() > 0
	case interface{ Len() int }:
		return x.Len() > 0
	}
	return true
}

func writeRepr(b *strings.Builder, v any) {
	switch x := v.(type) {
	case nil:
		b.WriteString("None")
	case bool:
		if x {
			b.WriteString("True")
		} else {
			b.WriteString("False")
		}
	case int:
		b.WriteString(strconv.Itoa(x))
	case int64:
		b.WriteString(strconv.FormatInt(x, 10))
	case *big.Int:
		b.WriteString(x.String())
	case float64:
		b.WriteString(Float(x))
	case string:
		b.WriteString(Quote(x))
	case []byte:
		b.WriteString(Bytes(x))
	case []string:
		b.WriteByte('[')
		for i, s := range x {
			if i > 0 {
				b.WriteString(", ")
			}
			b.WriteString(Quote(s))
		}
		b.WriteByte(']')
	case []any:
		b.WriteByte('[')
		for i, item := range x {
			if i > 0 {
				b.WriteString(", ")
			}
			writeRepr(b, item)
		}
		b.WriteByte(']')
	case Reprer:
		b.WriteString(x.PyRepr())
	case Items:
		b.WriteByte('{')
		i := 0
		for k, item := range x.All() {
			if i > 0 {
				b.WriteString(", ")
			}
			writeRepr(b, k)
			b.WriteString(": ")
			writeRepr(b, item)
			i++
		}
		b.WriteByte('}')
	default:
		fmt.Fprint(b, x)
	}
}

// Float は Python の repr(float)。指数は 1e-4 未満か 1e16 以上で使い、整数の値には ".0" を付ける。
func Float(f float64) string {
	switch {
	case math.IsNaN(f):
		return "nan"
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	}
	sci := strconv.FormatFloat(f, 'e', -1, 64)
	mant, expText, _ := strings.Cut(sci, "e")
	exp, _ := strconv.Atoi(expText)
	if exp < -4 || exp >= 16 {
		return mant + "e" + expText[:1] + fmt.Sprintf("%02d", abs(exp))
	}
	fixed := strconv.FormatFloat(f, 'f', -1, 64)
	if !strings.ContainsAny(fixed, ".") {
		fixed += ".0"
	}
	return fixed
}

func abs(n int) int {
	if n < 0 {
		return -n
	}
	return n
}

// Quote は Python の repr(str)。引用符は ' を使い、中に ' があって " が無ければ " を使う。
func Quote(s string) string {
	quote := '\''
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var b strings.Builder
	b.WriteRune(quote)
	for _, r := range s {
		switch {
		case r == quote || r == '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\t':
			b.WriteString(`\t`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r < ' ' || r == 0x7f:
			fmt.Fprintf(&b, `\x%02x`, r)
		case r < 0x7f:
			b.WriteRune(r)
		case unicode.IsPrint(r):
			b.WriteRune(r)
		case r <= 0xff:
			fmt.Fprintf(&b, `\x%02x`, r)
		case r <= 0xffff:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			fmt.Fprintf(&b, `\U%08x`, r)
		}
	}
	b.WriteRune(quote)
	return b.String()
}

// Bytes は Python の repr(bytes)。
func Bytes(data []byte) string {
	quote := byte('\'')
	if strings.IndexByte(string(data), '\'') >= 0 && strings.IndexByte(string(data), '"') < 0 {
		quote = '"'
	}
	var b strings.Builder
	b.WriteByte('b')
	b.WriteByte(quote)
	for _, c := range data {
		switch {
		case c == quote || c == '\\':
			b.WriteByte('\\')
			b.WriteByte(c)
		case c == '\t':
			b.WriteString(`\t`)
		case c == '\n':
			b.WriteString(`\n`)
		case c == '\r':
			b.WriteString(`\r`)
		case c < ' ' || c >= 0x7f:
			fmt.Fprintf(&b, `\x%02x`, c)
		default:
			b.WriteByte(c)
		}
	}
	b.WriteByte(quote)
	return b.String()
}

// Head は Python の s[:n]（符号位置で数える）。n が長さ以上なら s のまま。
func Head(s string, n int) string {
	i := 0
	for k := range s {
		if i == n {
			return s[:k]
		}
		i++
	}
	return s
}
