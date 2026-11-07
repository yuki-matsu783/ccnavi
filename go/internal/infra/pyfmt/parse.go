package pyfmt

import (
	"errors"
	"fmt"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

// MaxIntDigits は、Python が 10 進の文字列から整数を作るときの桁数の上限。sys.get_int_max_str_digits の既定値と同じ。
const MaxIntDigits = 4300

// ErrIntRange は、値が Go の int に収まらないときのエラー。Python の int() はこの値を読めるが、呼び手は読めない値と同じに扱う。
var ErrIntRange = errors.New("int に収まらない")

// numberSpace は int() と float() が前後から外す空白。ASCII の 0x1c〜0x1f は str.strip() と違って外さない。
// ASCII の外の空白は先に " " に置き換えてから、残りを C の isspace で外すため。
func numberSpace(r rune) bool {
	if r < 0x80 {
		return strings.ContainsRune(" \t\n\v\f\r", r)
	}
	return unicode.IsSpace(r)
}

// asciiNumber は、Python が数を読む前に行う置き換えと前後の空白の除去（CPython の _PyUnicode_TransformDecimalAndSpaceToASCII と同じ）。
// ASCII の外の 10 進の数字（全角・アラビア数字など）は 0〜9 に、空白は " " に置き換える。
// ASCII の外のほかの文字があれば、読めないとして false を返す。
func asciiNumber(text string) (string, bool) {
	var b strings.Builder
	for _, r := range text {
		switch {
		case r < 0x80:
			b.WriteRune(r)
		case unicode.IsSpace(r):
			b.WriteByte(' ')
		case unicode.IsDigit(r):
			b.WriteByte('0' + digitValue(r))
		default:
			return "", false
		}
	}
	return strings.TrimFunc(b.String(), numberSpace), true
}

// digitValue は 10 進の数字の値。Unicode の 10 進の数字は 0 から 9 が続けて並ぶので、前へたどって並びの先頭からの距離を数える。
func digitValue(r rune) byte {
	n := 0
	for unicode.IsDigit(r - rune(n) - 1) {
		n++
	}
	return byte(n % 10)
}

// ParseInt は Python の int(text)。
// 読めなければ Python の ValueError と同じ文のエラーを返し、int に収まらなければ ErrIntRange を返す。
func ParseInt(text string) (int, error) {
	n, err := ParseBigInt(text, 10)
	if err != nil {
		return 0, err
	}
	if !n.IsInt64() || n.Int64() < math.MinInt || n.Int64() > math.MaxInt {
		return 0, ErrIntRange
	}
	return int(n.Int64()), nil
}

// ParseBigInt は Python の int(text, base)。base は 2〜36。
// 前後の空白、符号、基数の接頭辞（0b・0o・0x）、数字の間の "_" を読む。エラーの文は Python の ValueError と同じにする。
func ParseBigInt(text string, base int) (*big.Int, error) {
	invalid := func() error {
		return fmt.Errorf("invalid literal for int() with base %d: %s", base, Head(Quote(text), 200))
	}
	// 1. 空白と全角数字を ASCII にそろえ、符号と基数の接頭辞（0b・0o・0x）を外す。
	s, ok := asciiNumber(text)
	if !ok {
		return nil, invalid()
	}
	neg := false
	if s != "" && (s[0] == '+' || s[0] == '-') {
		neg = s[0] == '-'
		s = s[1:]
	}
	prefixed := false
	if len(s) >= 2 && s[0] == '0' {
		if p := map[int]byte{2: 'b', 8: 'o', 16: 'x'}[base]; p != 0 && (s[1]|0x20) == p {
			s, prefixed = s[2:], true
		}
	}
	// value は 1 字の数字の値。数字でない字は 99（どの基数でも超える）。
	value := func(ch byte) int {
		switch {
		case ch >= '0' && ch <= '9':
			return int(ch - '0')
		case ch >= 'a' && ch <= 'z':
			return int(ch-'a') + 10
		case ch >= 'A' && ch <= 'Z':
			return int(ch-'A') + 10
		}
		return 99
	}
	// 2. 数字を集める。"_" は数字の間（接頭辞の直後を含む）に 1 つだけ置け、先頭・末尾・連続は読めない。
	// 10 進で桁が MaxIntDigits を超えるときは、読めない綴りより先に桁の上限の誤りにする。
	var digits strings.Builder
	count := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '_' {
			if i == len(s)-1 || s[i+1] == '_' || i == 0 && !prefixed {
				return nil, invalid()
			}
			continue
		}
		if value(s[i]) >= base {
			if count > MaxIntDigits && base == 10 {
				return nil, digitLimit(count)
			}
			return nil, invalid()
		}
		digits.WriteByte(s[i])
		count++
	}
	if count == 0 {
		return nil, invalid()
	}
	if count > MaxIntDigits && base == 10 {
		return nil, digitLimit(count)
	}
	out, _ := new(big.Int).SetString(digits.String(), base)
	if neg {
		out.Neg(out)
	}
	return out, nil
}

func digitLimit(count int) error {
	return fmt.Errorf("Exceeds the limit (%d digits) for integer string conversion: "+
		"value has %d digits; use sys.set_int_max_str_digits() to increase the limit", MaxIntDigits, count)
}

// floatSyntax は、"_" を外した後に Python の float(text) が読む綴り。16 進は読まない。
var floatSyntax = regexp.MustCompile(`^[+-]?(?:(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|(?i:inf|infinity|nan))$`)

// ParseFloat は Python の float(text)。数字の間の "_" を 1 つずつ読む。
// 桁あふれは Python と同じく ±inf か 0 になる。読めなければ Python の ValueError と同じ文のエラーを返す。
func ParseFloat(text string) (float64, error) {
	invalid := fmt.Errorf("could not convert string to float: %s", Quote(text))
	s, ok := asciiNumber(text)
	if !ok {
		return 0, invalid
	}
	if strings.Contains(s, "_") {
		for i := 0; i < len(s); i++ {
			if s[i] == '_' && (i == 0 || i == len(s)-1 || !isDigit(s[i-1]) || !isDigit(s[i+1])) {
				return 0, invalid
			}
		}
		s = strings.ReplaceAll(s, "_", "")
	}
	if !floatSyntax.MatchString(s) {
		return 0, invalid
	}
	if strings.EqualFold(strings.TrimLeft(s, "+-"), "nan") {
		return math.NaN(), nil // Go の ParseFloat は符号付きの nan を読めないため、ここで扱う。
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return 0, invalid
	}
	return f, nil
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }
