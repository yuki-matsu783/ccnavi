package pyjson

import (
	"errors"
	"math"
	"math/big"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyfmt"
)

// 期待値は Python 3.13 の json.dumps が出した文字列。
var dumpsCases = []struct {
	opts Options
	want string
}{
	{Options{}, "{\"b\": [1, 1.0, 1e+16, 1e-05, 0.1, -0.0, 123.456, 12345678901234567890, true, false, null, \"\\u00e9\\ud83d\\ude00\\u001b\\u007f\\\"\\\\\\n\\t/<>&\\u2028\\b\\f\\r\"], \"a\": {}, \"c\": [], \"nan\": NaN, \"inf\": -Infinity, \"d\": {\"z\": 1, \"y\": [{}]}}"},
	{Options{RawUnicode: true}, "{\"b\": [1, 1.0, 1e+16, 1e-05, 0.1, -0.0, 123.456, 12345678901234567890, true, false, null, \"é😀\\u001b\x7f\\\"\\\\\\n\\t/<>&\u2028\\b\\f\\r\"], \"a\": {}, \"c\": [], \"nan\": NaN, \"inf\": -Infinity, \"d\": {\"z\": 1, \"y\": [{}]}}"},
	{Options{Indent: 1}, "{\n \"b\": [\n  1,\n  1.0,\n  1e+16,\n  1e-05,\n  0.1,\n  -0.0,\n  123.456,\n  12345678901234567890,\n  true,\n  false,\n  null,\n  \"\\u00e9\\ud83d\\ude00\\u001b\\u007f\\\"\\\\\\n\\t/<>&\\u2028\\b\\f\\r\"\n ],\n \"a\": {},\n \"c\": [],\n \"nan\": NaN,\n \"inf\": -Infinity,\n \"d\": {\n  \"z\": 1,\n  \"y\": [\n   {}\n  ]\n }\n}"},
	{Options{Indent: 2, SortKeys: true, RawUnicode: true}, "{\n  \"a\": {},\n  \"b\": [\n    1,\n    1.0,\n    1e+16,\n    1e-05,\n    0.1,\n    -0.0,\n    123.456,\n    12345678901234567890,\n    true,\n    false,\n    null,\n    \"é😀\\u001b\x7f\\\"\\\\\\n\\t/<>&\u2028\\b\\f\\r\"\n  ],\n  \"c\": [],\n  \"d\": {\n    \"y\": [\n      {}\n    ],\n    \"z\": 1\n  },\n  \"inf\": -Infinity,\n  \"nan\": NaN\n}"},
	{Options{Compact: true, SortKeys: true}, "{\"a\":{},\"b\":[1,1.0,1e+16,1e-05,0.1,-0.0,123.456,12345678901234567890,true,false,null,\"\\u00e9\\ud83d\\ude00\\u001b\\u007f\\\"\\\\\\n\\t/<>&\\u2028\\b\\f\\r\"],\"c\":[],\"d\":{\"y\":[{}],\"z\":1},\"inf\":-Infinity,\"nan\":NaN}"},
}

func sample() *Object {
	huge, _ := new(big.Int).SetString("12345678901234567890", 10)
	return NewObject(
		"b", []any{1, 1.0, 1e16, 1e-05, 0.1, math.Copysign(0, -1), 123.456, huge, true, false, nil,
			"é😀\x1b\x7f\"\\\n\t/<>&\u2028\b\f\r"},
		"a", &Object{},
		"c", []any{},
		"nan", math.NaN(),
		"inf", math.Inf(-1),
		"d", NewObject("z", 1, "y", []any{NewObject()}),
	)
}

// 引数ごとに Python の json.dumps と同じバイト列を出す（区切り・ASCII への逃がし・字下げ・並べ替え・小数の形）。
func TestDumpsMatchesPython(t *testing.T) {
	for _, c := range dumpsCases {
		got, err := Dumps(sample(), c.opts)
		if err != nil || got != c.want {
			t.Errorf("Dumps(%+v)\n got %q %v\nwant %q", c.opts, got, err, c.want)
		}
	}
}

func TestDumpsRefusals(t *testing.T) {
	if _, err := Dumps([]any{math.NaN()}, Options{RejectNaN: true}); !errors.Is(err, errOutOfRange) ||
		err.Error() != "Out of range float values are not JSON compliant: nan" {
		t.Errorf("RejectNaN: %v", err)
	}
	if _, err := Dumps([]any{struct{}{}}, Options{}); !errors.Is(err, errUnsupported) {
		t.Errorf("unsupported: %v", err)
	}
}

// 期待値は Python 3.13 の json.loads が出した文。
var loadsErrors = []struct{ name, in, want string }{
	{"empty", "", "Expecting value: line 1 column 1 (char 0)"},
	{"x", "x", "Expecting value: line 1 column 1 (char 0)"},
	{"trailing-object", "{\"a\":1,}", "Illegal trailing comma before end of object: line 1 column 7 (char 6)"},
	{"trailing-array", "[1,]", "Illegal trailing comma before end of array: line 1 column 3 (char 2)"},
	{"colon", "{\"a\" 1}", "Expecting ':' delimiter: line 1 column 6 (char 5)"},
	{"name", "{a:1}", "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"},
	{"comma", "[1 2]", "Expecting ',' delimiter: line 1 column 4 (char 3)"},
	{"unterminated", "\"abc", "Unterminated string starting at: line 1 column 1 (char 0)"},
	{"control", "\"a\x01\"", "Invalid control character at: line 1 column 3 (char 2)"},
	{"escape", "\"\\q\"", "Invalid \\escape: line 1 column 2 (char 1)"},
	{"u-escape", "\"\\u12\"", "Invalid \\uXXXX escape: line 1 column 3 (char 2)"},
	{"extra", "{} x", "Extra data: line 1 column 4 (char 3)"},
	{"newlines", "\n\n  {\"a\":", "Expecting value: line 3 column 8 (char 9)"},
	{"open-object", "{\"a\":1", "Expecting ',' delimiter: line 1 column 7 (char 6)"},
	{"bom", "\ufeff{}", "Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)"},
	{"minus", "-", "Expecting value: line 1 column 1 (char 0)"},
	{"pair-then-bad", "\"\\ud800\\u12\"", "Invalid \\uXXXX escape: line 1 column 9 (char 8)"},
	// 回帰: 4 桁が入力の末尾にちょうど届くときは、桁の誤りではなく閉じていない文字列になる。
	{"u-escape-at-end", "\"\\u1234", "Unterminated string starting at: line 1 column 1 (char 0)"},
	{"wide", "[\"日本\", \"語\", 1 2]", "Expecting ',' delimiter: line 1 column 15 (char 14)"},
}

// 読めない理由と位置（符号位置で数える）が Python の JSONDecodeError と同じ。
func TestLoadsErrorsMatchPython(t *testing.T) {
	for _, c := range loadsErrors {
		_, err := Loads(c.in)
		var de *DecodeError
		if !errors.As(err, &de) || err.Error() != c.want {
			t.Errorf("%s: Loads(%q) = %v, want %q", c.name, c.in, err, c.want)
		}
	}
}

// 読んだ値は、オブジェクトの順序・重複したキー（位置は最初、値は後）・整数と小数の区別・サロゲートの組を保ち、
// 書き戻すと同じ形になる。
func TestLoadsValues(t *testing.T) {
	v, err := Loads(` {"z": 1, "a": [1.0, 2, -0, 1e2, 12345678901234567890, NaN, -Infinity], "z": "\ud83d\ude00\u00e9", "n": null} `)
	if err != nil {
		t.Fatal(err)
	}
	got, _ := Dumps(v, Options{RawUnicode: true})
	want := `{"z": "😀é", "a": [1.0, 2, 0, 100.0, 12345678901234567890, NaN, -Infinity], "n": null}`
	if got != want {
		t.Errorf("got  %s\nwant %s", got, want)
	}
	if pyfmt.Repr(v) != `{'z': '😀é', 'a': [1.0, 2, 0, 100.0, 12345678901234567890, nan, -inf], 'n': None}` {
		t.Errorf("repr %s", pyfmt.Repr(v))
	}
}

func TestLoadsDepth(t *testing.T) {
	if _, err := Loads(strings.Repeat("[", maxDepth) + strings.Repeat("]", maxDepth)); err != nil {
		t.Errorf("%d 段: %v", maxDepth, err)
	}
	n := maxDepth + 1
	if _, err := Loads(strings.Repeat(`{"a":`, n) + "1" + strings.Repeat("}", n)); !errors.Is(err, ErrTooDeep) {
		t.Errorf("%d 段: %v", n, err)
	}
}

// 期待値は Python の dict(data, **kv) と dict.pop(key, None)。
func TestObjectWithAndPop(t *testing.T) {
	data := NewObject("a", 1, "b", 2)
	cases := []struct {
		name string
		got  *Object
		want string
	}{
		{"上書きは位置を保つ", data.With("a", 9, "c", 3), `{"a": 9, "b": 2, "c": 3}`},
		{"何も足さない写し", data.With(), `{"a": 1, "b": 2}`},
		{"nil からの写し", (*Object)(nil).With("x", true), `{"x": true}`},
	}
	for _, c := range cases {
		if got, _ := Dumps(c.got, Options{}); got != c.want {
			t.Errorf("%s: %s, want %s", c.name, got, c.want)
		}
	}
	if got, _ := Dumps(data, Options{}); got != `{"a": 1, "b": 2}` {
		t.Errorf("元が変わった: %s", got)
	}
	popped := data.With("c", 3)
	if v, ok := popped.Pop("a"); !ok || v != 1 {
		t.Errorf("Pop = %v %v", v, ok)
	}
	if _, ok := popped.Pop("zz"); ok {
		t.Error("無いキーを除いた")
	}
	popped.Set("a", 5) // 除いたキーは後ろに足し直す
	if got, _ := Dumps(popped, Options{}); got != `{"b": 2, "c": 3, "a": 5}` {
		t.Errorf("Pop の後 = %s", got)
	}
}
