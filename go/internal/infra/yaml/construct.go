package yaml

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/pyfmt"
)

// 組み立ての途中の失敗に添える、Python の例外の名前。
const (
	excValue     = "ValueError"
	excIndex     = "IndexError"
	excKey       = "KeyError"
	excAttribute = "AttributeError"
	excType      = "TypeError"
)

const msgStringIndex = "string index out of range"

var timestampRe = fullMatch(timestampPattern)

// seqBox と setBox は組み立て中の列と集合。子は後から詰める（PyYAML と同じく、親を先に作って
// 子を順番待ちに積む。同じ順で組むので、誤りが複数あっても同じものを先に返す）。
type seqBox struct{ items []any }

type setBox struct{ m Map }

type constructor struct {
	built     map[*node]any
	building  map[*node]bool
	pending   []func() error
	finalized map[any]any
}

func construct(root *node) (any, error) {
	c := &constructor{built: map[*node]any{}, building: map[*node]bool{}}
	data, err := c.object(root)
	if err != nil {
		return nil, err
	}
	for len(c.pending) > 0 {
		fill := c.pending[0]
		c.pending = c.pending[1:]
		if err := fill(); err != nil {
			return nil, err
		}
	}
	return finalize(data, map[any]bool{}, map[any]any{})
}

// finalize は組み立て中の箱を公開する型（[]any・Set）に置き換える。自分を含む値（別名で自分を
// 指す列やマップ）は Go の値にできないので、入れ子が深すぎるものとして断る。
// 別名で 2 か所から指される箱は同じ列にする（書き出し（Dump）が PyYAML と同じく &id001 で書けるように）。
func finalize(v any, active map[any]bool, done map[any]any) (any, error) {
	switch x := v.(type) {
	case *seqBox:
		if f, ok := done[x]; ok {
			return f, nil
		}
		if active[x] {
			return nil, tooDeep()
		}
		active[x] = true
		defer delete(active, x)
		out := make([]any, len(x.items))
		for i, item := range x.items {
			f, err := finalize(item, active, done)
			if err != nil {
				return nil, err
			}
			out[i] = f
		}
		done[x] = out
		return out, nil
	case *setBox:
		return Set(x.m.keys), nil
	case *Map:
		if _, ok := done[x]; ok {
			return x, nil
		}
		if active[x] {
			return nil, tooDeep()
		}
		active[x] = true
		defer delete(active, x)
		for i, item := range x.vals {
			f, err := finalize(item, active, done)
			if err != nil {
				return nil, err
			}
			x.vals[i] = f
		}
		done[x] = x
		return x, nil
	case Tuple:
		for i, item := range x {
			f, err := finalize(item, active, done)
			if err != nil {
				return nil, err
			}
			x[i] = f
		}
		return x, nil
	}
	return v, nil
}

func constructorError(context string, contextMark *Mark, problem string, problemMark Mark) *Error {
	return markedError(ConstructorError, context, contextMark, problem, &problemMark)
}

func (c *constructor) object(n *node) (any, error) {
	if v, ok := c.built[n]; ok {
		return v, nil
	}
	if c.building[n] {
		return nil, constructorError("", nil, "found unconstructable recursive node", n.start)
	}
	c.building[n] = true
	var data any
	var err error
	switch n.tag {
	case tagNull:
		_, err = c.scalar(n)
	case tagBool:
		data, err = c.boolean(n)
	case tagInt:
		data, err = c.integer(n)
	case tagFloat:
		data, err = c.float(n)
	case tagBinary:
		data, err = c.binary(n)
	case tagTimestamp:
		data, err = c.timestamp(n)
	case tagStr:
		data, err = c.scalar(n)
	case tagSeq:
		box := &seqBox{items: []any{}}
		data = box
		c.later(func() (err error) { box.items, err = c.sequence(n); return })
	case tagMap:
		m := &Map{}
		data = m
		c.later(func() error { return c.mapping(n, m) })
	case tagSet:
		box := &setBox{}
		data = box
		c.later(func() error { return c.mapping(n, &box.m) })
	case tagOmap, tagPairs:
		box := &seqBox{items: []any{}}
		data = box
		c.later(func() (err error) { box.items, err = c.pairs(n); return })
	default:
		err = constructorError("", nil, "could not determine a constructor for the tag "+pyfmt.Quote(n.tag), n.start)
	}
	if err != nil {
		return nil, err
	}
	c.built[n] = data
	delete(c.building, n)
	return data, nil
}

func (c *constructor) later(fill func() error) { c.pending = append(c.pending, fill) }

// scalar はスカラーの中身。マップなら "=" のキーの値（YAML 1.1 の value）を使う。
func (c *constructor) scalar(n *node) (string, error) {
	if n.kind == nMapping {
		for _, p := range n.pairs {
			if p.key.tag == tagValue {
				return c.scalar(p.value)
			}
		}
	}
	if n.kind != nScalar {
		return "", constructorError("", nil, "expected a scalar node, but found "+nodeIDs[n.kind], n.start)
	}
	return n.value, nil
}

func (c *constructor) sequence(n *node) ([]any, error) {
	if n.kind != nSequence {
		return nil, constructorError("", nil, "expected a sequence node, but found "+nodeIDs[n.kind], n.start)
	}
	out := make([]any, 0, len(n.items))
	for _, child := range n.items {
		v, err := c.object(child)
		if err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, nil
}

// flatten は合流のキー（<<）を展開する。合流した欄が先、自分の欄が後に並び、後の欄が勝つ。
func (c *constructor) flatten(n *node) error {
	var merge []nodePair
	for i := 0; i < len(n.pairs); {
		key, value := n.pairs[i].key, n.pairs[i].value
		switch key.tag {
		case tagMerge:
			n.pairs = append(n.pairs[:i:i], n.pairs[i+1:]...)
			switch value.kind {
			case nMapping:
				if err := c.flatten(value); err != nil {
					return err
				}
				merge = append(merge, value.pairs...)
			case nSequence:
				var sub [][]nodePair
				for _, item := range value.items {
					if item.kind != nMapping {
						return constructorError("while constructing a mapping", markPtr(n.start),
							"expected a mapping for merging, but found "+nodeIDs[item.kind], item.start)
					}
					if err := c.flatten(item); err != nil {
						return err
					}
					sub = append(sub, item.pairs)
				}
				for j := len(sub) - 1; j >= 0; j-- {
					merge = append(merge, sub[j]...)
				}
			default:
				return constructorError("while constructing a mapping", markPtr(n.start),
					"expected a mapping or list of mappings for merging, but found "+nodeIDs[value.kind], value.start)
			}
		case tagValue:
			key.tag = tagStr
			i++
		default:
			i++
		}
	}
	if len(merge) > 0 {
		n.pairs = append(merge, n.pairs...)
	}
	return nil
}

func hashable(v any) bool {
	switch v.(type) {
	case *seqBox, *setBox, *Map:
		return false
	}
	return true
}

func (c *constructor) mapping(n *node, into *Map) error {
	if n.kind == nMapping {
		if err := c.flatten(n); err != nil {
			return err
		}
	}
	if n.kind != nMapping {
		return constructorError("", nil, "expected a mapping node, but found "+nodeIDs[n.kind], n.start)
	}
	for _, p := range n.pairs {
		key, err := c.object(p.key)
		if err != nil {
			return err
		}
		if !hashable(key) {
			return constructorError("while constructing a mapping", markPtr(n.start), "found unhashable key", p.key.start)
		}
		value, err := c.object(p.value)
		if err != nil {
			return err
		}
		into.set(key, value)
	}
	return nil
}

func (c *constructor) pairs(n *node) ([]any, error) {
	context := "while constructing an ordered map"
	if n.tag == tagPairs {
		context = "while constructing pairs"
	}
	if n.kind != nSequence {
		return nil, constructorError(context, markPtr(n.start), "expected a sequence, but found "+nodeIDs[n.kind], n.start)
	}
	out := []any{}
	for _, sub := range n.items {
		if sub.kind != nMapping {
			return nil, constructorError(context, markPtr(n.start),
				"expected a mapping of length 1, but found "+nodeIDs[sub.kind], sub.start)
		}
		if len(sub.pairs) != 1 {
			return nil, constructorError(context, markPtr(n.start),
				fmt.Sprintf("expected a single mapping item, but found %d items", len(sub.pairs)), sub.start)
		}
		key, err := c.object(sub.pairs[0].key)
		if err != nil {
			return nil, err
		}
		value, err := c.object(sub.pairs[0].value)
		if err != nil {
			return nil, err
		}
		out = append(out, Tuple{key, value})
	}
	return out, nil
}

func (c *constructor) boolean(n *node) (any, error) {
	value, err := c.scalar(n)
	if err != nil {
		return nil, err
	}
	b, ok := boolWords[strings.ToLower(value)]
	if !ok {
		return nil, buildError(excKey, pyfmt.Quote(strings.ToLower(value)))
	}
	return b, nil
}

// splitSign は先頭の符号を外す。空なら Python の value[0] と同じく IndexError。
func splitSign(value string) (int, string, error) {
	if value == "" {
		return 0, "", buildError(excIndex, msgStringIndex)
	}
	sign := 1
	if value[0] == '-' {
		sign = -1
	}
	if value[0] == '+' || value[0] == '-' {
		value = value[1:]
	}
	return sign, value, nil
}

// integer は YAML 1.1 の整数。0b は 2 進、0x は 16 進、0 始まりは 8 進、":" は 60 進。
func (c *constructor) integer(n *node) (any, error) {
	value, err := c.scalar(n)
	if err != nil {
		return nil, err
	}
	sign, value, err := splitSign(strings.ReplaceAll(value, "_", ""))
	if err != nil {
		return nil, err
	}
	var out *big.Int
	switch {
	case value == "0":
		return 0, nil
	case strings.HasPrefix(value, "0b"):
		out, err = pyInt(value[2:], 2)
	case strings.HasPrefix(value, "0x"):
		out, err = pyInt(value[2:], 16)
	case value == "":
		return nil, buildError(excIndex, msgStringIndex)
	case value[0] == '0':
		out, err = pyInt(value, 8)
	case strings.Contains(value, ":"):
		// Python は欄を左から順に数にしてから足すので、読めない欄は左のものを先に断る。
		var digits []*big.Int
		for _, part := range strings.Split(value, ":") {
			d, err := pyInt(part, 10)
			if err != nil {
				return nil, err
			}
			digits = append(digits, d)
		}
		out = new(big.Int)
		base := big.NewInt(1)
		for i := len(digits) - 1; i >= 0; i-- {
			out.Add(out, new(big.Int).Mul(digits[i], base))
			base.Mul(base, big.NewInt(60))
		}
	default:
		out, err = pyInt(value, 10)
	}
	if err != nil {
		return nil, err
	}
	if sign < 0 {
		out.Neg(out)
	}
	return intValue(out), nil
}

// intValue は int に収まれば int、収まらなければ *big.Int。
func intValue(n *big.Int) any {
	if n.IsInt64() && n.Int64() >= math.MinInt && n.Int64() <= math.MaxInt {
		return int(n.Int64())
	}
	return n
}

// pyInt は Python の int(text, base)。読めなければ素の ValueError を包んだエラー。
func pyInt(text string, base int) (*big.Int, error) {
	n, err := pyfmt.ParseBigInt(text, base)
	if err != nil {
		return nil, buildError(excValue, err.Error())
	}
	return n, nil
}

// pyFloat は Python の float(text)。桁あふれは Python と同じく inf になる。
func pyFloat(text string) (float64, error) {
	f, err := pyfmt.ParseFloat(text)
	if err != nil {
		return 0, buildError(excValue, err.Error())
	}
	return f, nil
}

// float は YAML 1.1 の小数。.inf・.nan と、":" の 60 進を読む。
func (c *constructor) float(n *node) (any, error) {
	value, err := c.scalar(n)
	if err != nil {
		return nil, err
	}
	sign, value, err := splitSign(strings.ToLower(strings.ReplaceAll(value, "_", "")))
	if err != nil {
		return nil, err
	}
	switch {
	case value == ".inf":
		return float64(sign) * math.Inf(1), nil
	case value == ".nan":
		return math.NaN(), nil
	case strings.Contains(value, ":"):
		var digits []float64
		for _, part := range strings.Split(value, ":") {
			d, err := pyFloat(part)
			if err != nil {
				return nil, err
			}
			digits = append(digits, d)
		}
		total, base := 0.0, 1.0
		for i := len(digits) - 1; i >= 0; i-- {
			total += digits[i] * base
			base *= 60
		}
		return float64(sign) * total, nil
	}
	f, err := pyFloat(value)
	if err != nil {
		return nil, err
	}
	return float64(sign) * f, nil
}

func (c *constructor) binary(n *node) (any, error) {
	value, err := c.scalar(n)
	if err != nil {
		return nil, err
	}
	if problem := asciiProblem([]rune(value)); problem != "" {
		return nil, constructorError("", nil, "failed to convert base64 data into ascii: "+problem, n.start)
	}
	data, problem := decodeBase64(value)
	if problem != "" {
		return nil, constructorError("", nil, "failed to decode base64 data: "+problem, n.start)
	}
	return data, nil
}

// decodeBase64 は Python 3.13 の base64.decodebytes。base64 の文字でないもの（途中の "=" を含む）は
// 読み飛ばす。端数の 2・3 文字は、後ろに "=" が揃っていれば捨て、揃っていなければ誤り。
func decodeBase64(text string) ([]byte, string) {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	var out []byte
	quad, pads, count := 0, 0, 0
	var left byte
	for i := 0; i < len(text); i++ {
		ch := text[i]
		if ch == '=' {
			pads++
			continue
		}
		v := strings.IndexByte(alphabet, ch)
		if v < 0 {
			continue
		}
		b := byte(v)
		pads = 0
		count++
		switch quad {
		case 0:
			quad, left = 1, b
		case 1:
			quad = 2
			out = append(out, left<<2|b>>4)
			left = b & 0x0f
		case 2:
			quad = 3
			out = append(out, left<<4|b>>2)
			left = b & 0x03
		case 3:
			quad = 0
			out = append(out, left<<6|b)
			left = 0
		}
	}
	switch {
	case quad == 0 || quad >= 2 && quad+pads >= 4:
		return out, ""
	case quad == 1:
		return nil, fmt.Sprintf("Invalid base64-encoded string: number of data characters (%d) "+
			"cannot be 1 more than a multiple of 4", count)
	}
	return nil, "Incorrect padding"
}

// timestamp は日付か日時。時刻が無ければ Date、あれば DateTime。
func (c *constructor) timestamp(n *node) (any, error) {
	if _, err := c.scalar(n); err != nil {
		return nil, err
	}
	if n.kind != nScalar {
		return nil, buildError(excType, "expected string or bytes-like object, got 'list'")
	}
	m := timestampRe.FindStringSubmatch(n.value)
	if m == nil {
		return nil, buildError(excAttribute, "'NoneType' object has no attribute 'groupdict'")
	}
	group := func(name string) string { return m[timestampRe.SubexpIndex(name)] }
	atoi := func(name string) int { v, _ := strconv.Atoi(group(name)); return v }
	date := Date{atoi("year"), atoi("month"), atoi("day")}
	if group("hour") == "" {
		if err := checkDate(date); err != nil {
			return nil, err
		}
		return date, nil
	}
	t := DateTime{Date: date, hour: atoi("hour"), minute: atoi("minute"), second: atoi("second")}
	if frac := group("fraction"); frac != "" {
		frac = (frac + "000000")[:6]
		t.microsecond, _ = strconv.Atoi(frac)
	}
	if sign := group("tz_sign"); sign != "" {
		t.HasZone = true
		t.offset = time.Duration(atoi("tz_hour"))*time.Hour + time.Duration(atoi("tz_minute"))*time.Minute
		if sign == "-" {
			t.offset = -t.offset
		}
		if t.offset <= -24*time.Hour || t.offset >= 24*time.Hour {
			return nil, buildError(excValue, "offset must be a timedelta strictly between "+
				"-timedelta(hours=24) and timedelta(hours=24), not "+timedeltaRepr(t.offset)+".")
		}
	} else if group("tz") != "" {
		t.HasZone = true
	}
	if err := checkDate(date); err != nil {
		return nil, err
	}
	for _, f := range []struct {
		name  string
		value int
		max   int
	}{{"hour", t.hour, 23}, {"minute", t.minute, 59}, {"second", t.second, 59}} {
		if f.value > f.max {
			return nil, buildError(excValue, fmt.Sprintf("%s must be in 0..%d", f.name, f.max))
		}
	}
	return t, nil
}

// checkDate は Python の datetime.date の検査と同じ順と文で、ありえない日付を断る。
func checkDate(d Date) error {
	switch {
	case d.year < 1:
		return buildError(excValue, fmt.Sprintf("year %d is out of range", d.year))
	case d.month < 1 || d.month > 12:
		return buildError(excValue, "month must be in 1..12")
	case d.day < 1 || d.day > daysIn(d.year, d.month):
		return buildError(excValue, "day is out of range for month")
	}
	return nil
}

func daysIn(year, month int) int {
	return time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day()
}

// asciiProblem は str.encode("ascii") の UnicodeEncodeError の文。ASCII だけなら空。
func asciiProblem(text []rune) string {
	for i, r := range text {
		if r <= 0x7f {
			continue
		}
		j := i
		for j < len(text) && text[j] > 0x7f {
			j++
		}
		if j == i+1 {
			return fmt.Sprintf("'ascii' codec can't encode character '%s' in position %d: ordinal not in range(128)", escapeRune(r), i)
		}
		return fmt.Sprintf("'ascii' codec can't encode characters in position %d-%d: ordinal not in range(128)", i, j-1)
	}
	return ""
}

// escapeRune は Python の例外の文が使う \xNN・\uNNNN・\UNNNNNNNN の形。
func escapeRune(r rune) string {
	switch {
	case r <= 0xff:
		return fmt.Sprintf(`\x%02x`, r)
	case r <= 0xffff:
		return fmt.Sprintf(`\u%04x`, r)
	}
	return fmt.Sprintf(`\U%08x`, r)
}
