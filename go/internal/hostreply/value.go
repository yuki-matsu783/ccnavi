package hostreply

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ErrShape は、応答が読み方の前提の形（表か列）でないこと。sh の jq が型の誤りで落ちるのと同じ扱いにする。
var ErrShape = errors.New("応答の形が違う")

// Decode は応答を読む。数は元の綴りのまま持つ（jq と同じく `5` を `5` と出すため）。
func Decode(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if dec.More() {
		return nil, ErrShape
	}
	return v, nil
}

// Field は表 v の name。v が表でも null でもなければ ErrShape（jq の `.name` と同じ）。
func Field(v any, name string) (any, error) {
	switch m := v.(type) {
	case map[string]any:
		return m[name], nil
	case nil:
		return nil, nil
	}
	return nil, ErrShape
}

// Path は Field を順にたどる。
func Path(v any, names ...string) (any, error) {
	for _, n := range names {
		var err error
		if v, err = Field(v, n); err != nil {
			return nil, err
		}
	}
	return v, nil
}

// List は v を列として読む（null は空の列。jq の `.[]` と同じく、表と列のほかは ErrShape）。
func List(v any) ([]any, error) {
	switch l := v.(type) {
	case []any:
		return l, nil
	case nil:
		return nil, nil
	}
	return nil, ErrShape
}

// Truthy は jq の真偽（null と false だけが偽）。
func Truthy(v any) bool {
	b, isBool := v.(bool)
	return v != nil && (!isBool || b)
}

// Alt は jq の `v // fallback`（null と false のときだけ fallback）。
func Alt(v, fallback any) any {
	if Truthy(v) {
		return v
	}
	return fallback
}

// Text は jq の文字列の埋め込み（`\(...)`）と -r の出力の綴り。
func Text(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case json.Number:
		return x.String()
	case nil:
		return "null"
	case bool:
		return strconv.FormatBool(x)
	}
	b, _ := json.Marshal(v)
	return string(b)
}

// Str は (v // "") を文字列として読む。文字列でなければ ErrShape（jq の gsub が型で落ちるのと同じ）。
func Str(v any) (string, error) {
	if !Truthy(v) {
		return "", nil
	}
	s, ok := v.(string)
	if !ok {
		return "", ErrShape
	}
	return s, nil
}

// Number は数として読む。数でなければ偽。
func Number(v any) (float64, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	f, err := n.Float64()
	return f, err == nil && !math.IsInf(f, 0)
}

// SameNumber は v が数で、引数で受けた数の綴り want と同じ値か（jq の `--argjson` で渡した数との `==`）。
func SameNumber(v any, want string) bool {
	got, isNum := Number(v)
	w, wantNum := Number(JSONNumber(want))
	return isNum && wantNum && got == w
}

// Clean は、外から来る文字列を出力に流す前に整える。制御文字・C1 制御文字・行区切り・段落区切り・全角空白の並びを空白 1 つにする。
func Clean(s string) string {
	var b strings.Builder
	inRun := false
	for _, r := range s {
		if unclean(r) {
			if !inRun {
				b.WriteByte(' ')
			}
			inRun = true
			continue
		}
		inRun = false
		b.WriteRune(r)
	}
	return b.String()
}

func unclean(r rune) bool {
	return (r >= 0x01 && r <= 0x1f) || (r >= 0x7f && r <= 0x9f) || r == 0x2028 || r == 0x2029 || r == 0x3000
}

// Head は先頭の n 文字（符号位置で数える。jq の `.[0:n]` と同じ）。
func Head(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

// CleanField は ((v // "") | Clean | .[0:n])。
func CleanField(v any, n int) (string, error) {
	s, err := Str(v)
	if err != nil {
		return "", err
	}
	return Head(Clean(s), n), nil
}

// JSONText は jq が -r 無しで出す値の綴り（ASCII でない文字も HTML の字もそのまま）。
func JSONText(v any) string {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return strings.TrimSuffix(b.String(), "\n")
}

// ToString は jq の `tostring`（文字列はそのまま、ほかは JSON の綴り）。
func ToString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return JSONText(v)
}

// JSONNumber は、引数で受けた数の綴りを、応答の数と同じ形にする。
func JSONNumber(s string) any { return json.Number(s) }
