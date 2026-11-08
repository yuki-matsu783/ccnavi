package pyjson

import (
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
)

// DecodeError は json.loads が読めなかった場所と理由（Python の JSONDecodeError）。
// Error() は Python の str(exc) と同じ「理由: line L column C (char N)」。
type DecodeError struct {
	msg    string
	pos    int // 符号位置で数えた位置（0 始まり）
	line   int
	column int
}

func (e *DecodeError) Error() string {
	return fmt.Sprintf("%s: line %d column %d (char %d)", e.msg, e.line, e.column, e.pos)
}

// ErrIntDigits は 4300 桁を超える整数（Python の int の上限。json.loads は ValueError で止まる）。
var ErrIntDigits = errors.New("Exceeds the limit (4300 digits) for integer string conversion")

// ErrTooDeep は入れ子が maxDepth 段を超えた（Python の json.loads の RecursionError）。
var ErrTooDeep = errors.New("maximum recursion depth exceeded while decoding a JSON value")

// maxDepth は読める入れ子の段数。Python 3.13 の json.loads が RecursionError を出さずに読める最大の段数。
const maxDepth = 9998

// maxIntDigits は Python が 10 進の文字列から作る整数の桁の上限（sys.get_int_max_str_digits の既定）。
const maxIntDigits = 4300

// Loads は json.loads(text)。入れ子が maxDepth を超えれば ErrTooDeep。値は nil・bool・int（範囲を超えれば *big.Int）・float64・string・
// []any・*Object。NaN・Infinity・-Infinity も Python と同じく読む。
//
// 文字列の中の孤立したサロゲート（"\ud800"）は Go の文字列に置けないので U+FFFD にする。
func Loads(text string) (any, error) {
	d := decoder{s: []rune(text)}
	if len(d.s) > 0 && d.s[0] == bom {
		return nil, d.fail("Unexpected UTF-8 BOM (decode using utf-8-sig)", 0)
	}
	idx := d.skip(0)
	v, end, err := d.scan(idx)
	if err != nil {
		return nil, err
	}
	end = d.skip(end)
	if end != len(d.s) {
		return nil, d.fail("Extra data", end)
	}
	return v, nil
}

// bom は先頭の U+FEFF。Python は utf-8-sig で読めと言って拒む。
const bom = '\ufeff'

type decoder struct {
	s     []rune
	depth int
}

// stopIteration は Python の C の走査器が StopIteration で返す「値が無い」。呼び手が Expecting value にする。
type stopIteration struct{ pos int }

func (stopIteration) Error() string { return "Expecting value" }

func (d *decoder) fail(msg string, pos int) error {
	line := 1
	last := -1
	for i := 0; i < pos && i < len(d.s); i++ {
		if d.s[i] == '\n' {
			line++
			last = i
		}
	}
	return &DecodeError{msg: msg, pos: pos, line: line, column: pos - last}
}

func (d *decoder) skip(i int) int {
	for i < len(d.s) {
		switch d.s[i] {
		case ' ', '\t', '\n', '\r':
			i++
		default:
			return i
		}
	}
	return i
}

func (d *decoder) has(i int, word string) bool {
	w := []rune(word)
	if i+len(w) > len(d.s) {
		return false
	}
	for k, r := range w {
		if d.s[i+k] != r {
			return false
		}
	}
	return true
}

// scan は 1 つの値を読む。値が無いときは "Expecting value" を、その位置で返す。
func (d *decoder) scan(idx int) (any, int, error) {
	v, end, err := d.scanOnce(idx)
	var stop stopIteration
	if errors.As(err, &stop) {
		return nil, 0, d.fail("Expecting value", stop.pos)
	}
	return v, end, err
}

func (d *decoder) scanOnce(idx int) (any, int, error) {
	if idx >= len(d.s) {
		return nil, 0, stopIteration{idx}
	}
	switch d.s[idx] {
	case '"':
		return d.str(idx + 1)
	case '{', '[':
		d.depth++
		defer func() { d.depth-- }()
		if d.depth > maxDepth {
			return nil, 0, ErrTooDeep
		}
		if d.s[idx] == '{' {
			return d.object(idx + 1)
		}
		return d.array(idx + 1)
	case 'n':
		if d.has(idx, "null") {
			return nil, idx + 4, nil
		}
	case 't':
		if d.has(idx, "true") {
			return true, idx + 4, nil
		}
	case 'f':
		if d.has(idx, "false") {
			return false, idx + 5, nil
		}
	case 'N':
		if d.has(idx, "NaN") {
			return nan(), idx + 3, nil
		}
	case 'I':
		if d.has(idx, "Infinity") {
			return inf(1), idx + 8, nil
		}
	case '-':
		if d.has(idx, "-Infinity") {
			return inf(-1), idx + 9, nil
		}
	}
	return d.number(idx)
}

func isDigit(r rune) bool { return r >= '0' && r <= '9' }

func (d *decoder) number(start int) (any, int, error) {
	end := len(d.s) - 1
	idx := start
	if d.s[idx] == '-' {
		idx++
		if idx > end {
			return nil, 0, stopIteration{start}
		}
	}
	switch {
	case d.s[idx] >= '1' && d.s[idx] <= '9':
		idx++
		for idx <= end && isDigit(d.s[idx]) {
			idx++
		}
	case d.s[idx] == '0':
		idx++
	default:
		return nil, 0, stopIteration{start}
	}
	isFloat := false
	if idx < end && d.s[idx] == '.' && isDigit(d.s[idx+1]) {
		isFloat = true
		idx += 2
		for idx <= end && isDigit(d.s[idx]) {
			idx++
		}
	}
	if idx < end && (d.s[idx] == 'e' || d.s[idx] == 'E') {
		eStart := idx
		idx++
		if idx < end && (d.s[idx] == '-' || d.s[idx] == '+') {
			idx++
		}
		for idx <= end && isDigit(d.s[idx]) {
			idx++
		}
		if isDigit(d.s[idx-1]) {
			isFloat = true
		} else {
			idx = eStart
		}
	}
	text := string(d.s[start:idx])
	if isFloat {
		f, err := strconv.ParseFloat(text, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return nil, 0, err
		}
		return f, idx, nil
	}
	if n, err := strconv.Atoi(text); err == nil {
		return n, idx, nil
	}
	digits := len(strings.TrimPrefix(text, "-"))
	if digits > maxIntDigits {
		return nil, 0, fmt.Errorf("%w: value has %d digits; use sys.set_int_max_str_digits() to increase the limit", ErrIntDigits, digits)
	}
	n, _ := new(big.Int).SetString(text, 10)
	return n, idx, nil
}

func (d *decoder) object(idx int) (any, int, error) {
	end := len(d.s) - 1
	obj := &Object{}
	idx = d.skip(idx)
	if idx > end || d.s[idx] != '}' {
		for {
			if idx > end || d.s[idx] != '"' {
				return nil, 0, d.fail("Expecting property name enclosed in double quotes", idx)
			}
			key, next, err := d.str(idx + 1)
			if err != nil {
				return nil, 0, err
			}
			idx = d.skip(next)
			if idx > end || d.s[idx] != ':' {
				return nil, 0, d.fail("Expecting ':' delimiter", idx)
			}
			idx = d.skip(idx + 1)
			val, next, err := d.scanOnce(idx)
			if err != nil {
				return nil, 0, err
			}
			obj.Set(key.(string), val)
			idx = d.skip(next)
			if idx <= end && d.s[idx] == '}' {
				break
			}
			if idx > end || d.s[idx] != ',' {
				return nil, 0, d.fail("Expecting ',' delimiter", idx)
			}
			comma := idx
			idx = d.skip(idx + 1)
			if idx <= end && d.s[idx] == '}' {
				return nil, 0, d.fail("Illegal trailing comma before end of object", comma)
			}
		}
	}
	return obj, idx + 1, nil
}

func (d *decoder) array(idx int) (any, int, error) {
	end := len(d.s) - 1
	items := []any{}
	idx = d.skip(idx)
	if idx > end || d.s[idx] != ']' {
		for {
			val, next, err := d.scanOnce(idx)
			if err != nil {
				return nil, 0, err
			}
			items = append(items, val)
			idx = d.skip(next)
			if idx <= end && d.s[idx] == ']' {
				break
			}
			if idx > end || d.s[idx] != ',' {
				return nil, 0, d.fail("Expecting ',' delimiter", idx)
			}
			comma := idx
			idx = d.skip(idx + 1)
			if idx <= end && d.s[idx] == ']' {
				return nil, 0, d.fail("Illegal trailing comma before end of array", comma)
			}
		}
	}
	return items, idx + 1, nil
}

var simpleEscapes = map[rune]rune{'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}

func hexValue(r rune) (rune, bool) {
	switch {
	case r >= '0' && r <= '9':
		return r - '0', true
	case r >= 'a' && r <= 'f':
		return r - 'a' + 10, true
	case r >= 'A' && r <= 'F':
		return r - 'A' + 10, true
	}
	return 0, false
}

// str は開きの引用符の次（end）から文字列を読む。制御文字（0x20 未満）は strict のとおり断る。
func (d *decoder) str(end int) (any, int, error) {
	begin := end - 1
	n := len(d.s)
	var b strings.Builder
	for {
		next := end
		var c rune
		for ; next < n; next++ {
			c = d.s[next]
			if c == '"' || c == '\\' {
				break
			}
			if c <= 0x1f {
				return nil, 0, d.fail("Invalid control character at", next)
			}
		}
		if next >= n {
			return nil, 0, d.fail("Unterminated string starting at", begin)
		}
		b.WriteString(string(d.s[end:next]))
		next++
		if c == '"' {
			end = next
			break
		}
		if next == n {
			return nil, 0, d.fail("Unterminated string starting at", begin)
		}
		c = d.s[next]
		if c != 'u' {
			end = next + 1
			r, ok := simpleEscapes[c]
			if !ok {
				return nil, 0, d.fail(`Invalid \escape`, end-2)
			}
			b.WriteRune(r)
			continue
		}
		next++
		end = next + 4
		if end > n {
			return nil, 0, d.fail(`Invalid \uXXXX escape`, next-1)
		}
		c = 0
		for ; next < end; next++ {
			h, ok := hexValue(d.s[next])
			if !ok {
				return nil, 0, d.fail(`Invalid \uXXXX escape`, end-5)
			}
			c = c<<4 | h
		}
		if c >= 0xd800 && c <= 0xdbff && end+6 < n && d.s[next] == '\\' && d.s[next+1] == 'u' {
			next += 2
			end += 6
			var c2 rune
			for ; next < end; next++ {
				h, ok := hexValue(d.s[next])
				if !ok {
					return nil, 0, d.fail(`Invalid \uXXXX escape`, end-5)
				}
				c2 = c2<<4 | h
			}
			if c2 >= 0xdc00 && c2 <= 0xdfff {
				c = 0x10000 + (c-0xd800)<<10 + (c2 - 0xdc00)
			} else {
				end -= 6
			}
		}
		b.WriteRune(c) // 孤立したサロゲートは WriteRune が U+FFFD にする
	}
	return b.String(), end, nil
}

func nan() float64 { return math.NaN() }

func inf(sign int) float64 { return math.Inf(sign) }
