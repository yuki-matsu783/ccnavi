package start

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

// errShape は、応答が読み方の前提の形（表か列）でないこと。sh の jq が型の誤りで落ちるのと同じ扱いにする。
var errShape = errors.New("応答の形が違う")

// decode は応答を読む。数は元の綴りのまま持つ（jq と同じく `5` を `5` と出すため）。
func decode(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if dec.More() {
		return nil, errShape
	}
	return v, nil
}

// field は表 v の name。v が表でも null でもなければ errShape（jq の `.name` と同じ）。
func field(v any, name string) (any, error) {
	switch m := v.(type) {
	case map[string]any:
		return m[name], nil
	case nil:
		return nil, nil
	}
	return nil, errShape
}

// path は field を順にたどる。
func path(v any, names ...string) (any, error) {
	for _, n := range names {
		var err error
		if v, err = field(v, n); err != nil {
			return nil, err
		}
	}
	return v, nil
}

// list は v を列として読む（null は空の列。jq の `.[]` と同じく、表と列のほかは errShape）。
func list(v any) ([]any, error) {
	switch l := v.(type) {
	case []any:
		return l, nil
	case nil:
		return nil, nil
	}
	return nil, errShape
}

// truthy は jq の真偽（null と false だけが偽）。
func truthy(v any) bool {
	b, isBool := v.(bool)
	return v != nil && (!isBool || b)
}

// text は jq の文字列の埋め込み（`\(...)`）と -r の出力の綴り。
func text(v any) string {
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

// str は (v // "") を文字列として読む。文字列でなければ errShape（jq の gsub が型で落ちるのと同じ）。
func str(v any) (string, error) {
	if !truthy(v) {
		return "", nil
	}
	s, ok := v.(string)
	if !ok {
		return "", errShape
	}
	return s, nil
}

// number は数として読む。数でなければ偽。
func number(v any) (float64, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	f, err := n.Float64()
	return f, err == nil && !math.IsInf(f, 0)
}

// clean は sh の CLEAN_JQ と同じ。制御文字・C1 制御文字・行区切り・段落区切り・全角空白の並びを空白 1 つにする。
func clean(s string) string {
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

// head は先頭の n 文字（符号位置で数える。jq の `.[0:n]` と同じ）。
func head(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

// cleanField は ((v // "") | clean | .[0:n])。
func cleanField(v any, n int) (string, error) {
	s, err := str(v)
	if err != nil {
		return "", err
	}
	return head(clean(s), n), nil
}

// jsonText は jq が -r 無しで出す値の綴り（ASCII でない文字も HTML の字もそのまま）。
func jsonText(v any) string {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return strings.TrimSuffix(b.String(), "\n")
}

// jsonNumber は、引数で受けた数の綴りを、応答の数と同じ形にする。
func jsonNumber(s string) any { return json.Number(s) }
