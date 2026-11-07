package yaml

import (
	"errors"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyfmt"
)

// 期待値は PyYAML 6.0.3 の純 Python の読み手（Python の実行ファイルの yamlread）が返した repr と文。
// err が真なら want は読めないときの文（Python の str(exc)）。
var loadCases = []struct {
	name, doc, want string
	err             bool
}{
	{"empty", "", "None", false},
	{"flow", "a: 1\nb: [x, y]\nc: {d: null, e: true}\n", "{'a': 1, 'b': ['x', 'y'], 'c': {'d': None, 'e': True}}", false},
	{"date-octal-float", "title: 日本語\nwhen: 2026-10-04\nnum: 0o17\nf: 1.5e3\n", "{'title': '日本語', 'when': datetime.date(2026, 10, 4), 'num': '0o17', 'f': '1.5e3'}", false},
	{"folded", "list:\n  - a\n  - b: c\n    d: >-\n      折り返し\n      の行\n", "{'list': ['a', {'b': 'c', 'd': '折り返し の行'}]}", false},
	{"alias", "base: &b {x: 1}\nuse: *b\n", "{'base': {'x': 1}, 'use': {'x': 1}}", false},
	{"bool-words", "[yes, No, ON, off, True, FALSE, y, n, Yes, yES]\n", "[True, False, True, False, True, False, 'y', 'n', True, 'yES']", false},
	{"ints", "[0, 017, 0o17, 0x1F, 0b101, 1_000, +5, -0, 1:30, -1:00:00, 12345678901234567890123, 08]\n", "[0, 15, '0o17', 31, 5, 1000, 5, 0, 90, -3600, 12345678901234567890123, '08']", false},
	{"floats", "[1.5, 1e3, 1.5e3, 1.5e+3, .5, 1., -.inf, .Inf, 1_0.5, 1:30.5, 6.8523015e+5]\n", "[1.5, '1e3', '1.5e3', 1500.0, 0.5, 1.0, -inf, inf, 10.5, 90.5, 685230.15]", false},
	{"nan", "x: .NaN\n", "{'x': nan}", false},
	{"nulls", "[~, null, Null, NULL, nULL, '', ]\n", "[None, None, None, None, 'nULL', '']", false},
	{"explicit-tags", "[!!str 1, !!int '0x10', !!int ' 15 ', !!float '1e3', !!bool 'on', !!null x, ! yes, ! 'no', !!str ]\n", "['1', 16, 15, 1000.0, True, None, True, False, '']", false},
	{"timestamps", "[2026-01-02, 2026-1-2, 2026-1-2 3:04:05, 2001-12-14t21:59:43.10Z, 2001-12-14 21:59:43.10 -5, 2001-12-15T02:59:43.1234567+09:30, 2001-12-14 21:59:43.]\n", "[datetime.date(2026, 1, 2), '2026-1-2', datetime.datetime(2026, 1, 2, 3, 4, 5), datetime.datetime(2001, 12, 14, 21, 59, 43, 100000, tzinfo=datetime.timezone.utc), datetime.datetime(2001, 12, 14, 21, 59, 43, 100000, tzinfo=datetime.timezone(datetime.timedelta(days=-1, seconds=68400))), datetime.datetime(2001, 12, 15, 2, 59, 43, 123456, tzinfo=datetime.timezone(datetime.timedelta(seconds=34200))), datetime.datetime(2001, 12, 14, 21, 59, 43)]", false},
	{"dup-keys", "a: 1\nb: 2\na: 3\n", "{'a': 3, 'b': 2}", false},
	{"equal-keys", "{1: a, true: b, 1.0: c, 0: d, false: e, ~: f, null: g}\n", "{1: 'c', 0: 'e', None: 'g'}", false},
	{"merge", "base: &b {x: 1, y: 2}\nother: &o {z: 3}\nuse:\n  <<: [*b, *o]\n  y: 9\n", "{'base': {'x': 1, 'y': 2}, 'other': {'z': 3}, 'use': {'z': 3, 'x': 1, 'y': 9}}", false},
	{"merge-order", "- &a {k: a}\n- &b {k: b}\n- {<<: [*a, *b]}\n", "[{'k': 'a'}, {'k': 'b'}, {'k': 'a'}]", false},
	{"value-key", "{=: 1}\n", "{'=': 1}", false},
	{"non-string-keys", "{1: a, 2001-01-01: b, ~: c}\n", "{1: 'a', datetime.date(2001, 1, 1): 'b', None: 'c'}", false},
	{"binary", "x: !!binary aGVsbG8=\ny: !!binary |\n  aGk\n  x=\n", "{'x': b'hello', 'y': b'hi1'}", false},
	{"omap", "!!omap [a: 1, b: 2]\n", "[('a', 1), ('b', 2)]", false},
	{"pairs", "!!pairs [a: 1, a: 2]\n", "[('a', 1), ('a', 2)]", false},
	{"set", "!!set {a}\n", "{'a'}", false},
	{"bom-head", "\ufefftitle: x\n", "{'title': 'x'}", false},
	{"tab-in-quote", "a: 'x\ty'\nb: \"x\\ty\"\n", "{'a': 'x\\ty', 'b': 'x\\ty'}", false},
	{"tab-in-block", "a: |\n  x\ty\n", "{'a': 'x\\ty\\n'}", false},
	{"tab-in-comment", "a: 1 #\tc\n", "{'a': 1}", false},
	{"bang-alone", "title: !\n", "{'title': None}", false},
	{"bang-in-flow", "a: [!, x]\n", "could not determine a constructor for the tag '!,'\n  in \"<unicode string>\", line 1, column 5:\n    a: [!, x]\n        ^", true},
	{"bang-end", "a: !", "{'a': None}", false},
	{"directive", "%foo bar\n---\na: 1\n", "{'a': 1}", false},
	{"flow-colon", "{a:}\n", "{'a': None}", false},
	{"flow-question", "[? ]\n", "[{None: None}]", false},
	{"question-quoted", "[\"a?b\"]\n", "['a?b']", false},
	{"question-block", "- a?b\n", "['a?b']", false},
	{"url", "k: http://x/?q=1\n", "{'k': 'http://x/?q=1'}", false},
	{"crlf", "a: 1\r\nb: 'x\r\n  y'\r\n", "{'a': 1, 'b': 'x y'}", false},
	{"unicode-breaks", "a: x\u2028b\n", "while scanning a simple key\n  in \"<unicode string>\", line 2, column 1:\n    b\n    ^\ncould not find expected ':'\n  in \"<unicode string>\", line 3, column 1:\n    \n    ^", true},
	{"explicit-doc", "--- a\n...\n", "'a'", false},
	{"comment-only", "# c\n", "None", false},
	{"anchor-on-scalar", "a: &x 1\nb: *x\n", "{'a': 1, 'b': 1}", false},
	{"multiline-plain", "a: x\n  y\n\n  z\n", "{'a': 'x y\\nz'}", false},
	{"block-scalars", "a: |+\n  x\n\nb: >-\n  p\n  q\n\n  r\nc: |2\n   s\n", "{'a': 'x\\n\\n', 'b': 'p q\\nr', 'c': ' s\\n'}", false},
	{"escapes", "a: \"\\x41\\u00e9\\U0001F600\\N\\_\\L\\P\\e\\0\\ \\/\"\n", "{'a': 'Aé😀\\x85\\xa0\\u2028\\u2029\\x1b\\x00 /'}", false},
	{"single-quote", "a: 'it''s'\n", "{'a': \"it's\"}", false},
	{"unbuildable-int-tab", "ticket: !!int \t\n", "while scanning for the next token\nfound character '\\t' that cannot start any token\n  in \"<unicode string>\", line 1, column 15:\n    ticket: !!int \t\n                  ^", true},
	{"unbuildable-int-empty", "ticket: !!int\n", "値を組み立てられない (IndexError: string index out of range)", true},
	{"unbuildable-int-word", "ticket: !!int abc\n", "値を組み立てられない (ValueError: invalid literal for int() with base 10: 'abc')", true},
	{"unbuildable-bool-tab", "ticket: !!bool \t\n", "while scanning for the next token\nfound character '\\t' that cannot start any token\n  in \"<unicode string>\", line 1, column 16:\n    ticket: !!bool \t\n                   ^", true},
	{"unbuildable-bool-word", "ticket: !!bool maybe\n", "値を組み立てられない (KeyError: 'maybe')", true},
	{"unbuildable-float-word", "x: !!float abc\n", "値を組み立てられない (ValueError: could not convert string to float: 'abc')", true},
	{"unbuildable-timestamp", "when: !!timestamp 2020-13-45\n", "値を組み立てられない (ValueError: month must be in 1..12)", true},
	{"unbuildable-tz-offset", "when: !!timestamp 2001-12-14t21:59:43.10+99:00\n", "値を組み立てられない (ValueError: offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not datetime.timedelta(days=4, seconds=10800).)", true},
	{"bool-key-error", "x: !!bool maybe\n", "値を組み立てられない (KeyError: 'maybe')", true},
	{"int-octal", "x: !!int 09\n", "値を組み立てられない (ValueError: invalid literal for int() with base 8: '09')", true},
	{"int-hex-empty", "x: !!int 0x\n", "値を組み立てられない (ValueError: invalid literal for int() with base 16: '')", true},
	{"int-sexagesimal-left-first", "x: !!int a:b\n", "値を組み立てられない (ValueError: invalid literal for int() with base 10: 'a')", true},
	{"int-digits", "x: 99999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999\n", "値を組み立てられない (ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit)", true},
	{"float-empty", "x: !!float\n", "値を組み立てられない (IndexError: string index out of range)", true},
	{"timestamp-day", "w: !!timestamp 2020-02-30\n", "値を組み立てられない (ValueError: day is out of range for month)", true},
	{"timestamp-year", "w: !!timestamp 0000-01-01\n", "値を組み立てられない (ValueError: year 0 is out of range)", true},
	{"timestamp-hour", "w: !!timestamp 2001-12-14 25:00:00\n", "値を組み立てられない (ValueError: hour must be in 0..23)", true},
	{"timestamp-form", "w: !!timestamp abc\n", "値を組み立てられない (AttributeError: 'NoneType' object has no attribute 'groupdict')", true},
	{"timestamp-tz-negative", "w: !!timestamp 2001-12-14 01:00:00 -24:00\n", "値を組み立てられない (ValueError: offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not datetime.timedelta(days=-1).)", true},
	{"chr-range", "x: \"\\U00110000\"\n", "値を組み立てられない (ValueError: chr() arg not in range(0x110000))", true},
	{"flow-unclosed", "a: [1, 2\n", "while parsing a flow sequence\n  in \"<unicode string>\", line 1, column 4:\n    a: [1, 2\n       ^\nexpected ',' or ']', but got '<stream end>'\n  in \"<unicode string>\", line 2, column 1:\n    \n    ^", true},
	{"mapping-in-value", "a: b: c\n", "mapping values are not allowed here\n  in \"<unicode string>\", line 1, column 5:\n    a: b: c\n        ^", true},
	{"tab-eol", "a: 1\t\nb: 2\n", "while scanning for the next token\nfound character '\\t' that cannot start any token\n  in \"<unicode string>\", line 1, column 5:\n    a: 1\t\n        ^", true},
	{"tab-after-colon", "a:\t1\n", "while scanning for the next token\nfound character '\\t' that cannot start any token\n  in \"<unicode string>\", line 1, column 3:\n    a:\t1\n      ^", true},
	{"tab-after-quote", "a: 'x'\t\n", "while scanning for the next token\nfound character '\\t' that cannot start any token\n  in \"<unicode string>\", line 1, column 7:\n    a: 'x'\t\n          ^", true},
	{"tab-in-value", "a: x\ty\n", "while scanning for the next token\nfound character '\\t' that cannot start any token\n  in \"<unicode string>\", line 1, column 5:\n    a: x\ty\n        ^", true},
	{"bom-middle", "title: x\n\ufeffticket: T-1\n", "{'title': 'x', '\\ufeffticket': 'T-1'}", false},
	{"bom-in-value", "title: a\ufeffb\n", "{'title': 'a\\ufeffb'}", false},
	{"block-hash", "a: |#\n", "while scanning a block scalar\n  in \"<unicode string>\", line 1, column 4:\n    a: |#\n       ^\nexpected chomping or indentation indicators, but found '#'\n  in \"<unicode string>\", line 1, column 5:\n    a: |#\n        ^", true},
	{"flow-plain-question", "paths: [src/a?.py]\n", "while parsing a flow sequence\n  in \"<unicode string>\", line 1, column 8:\n    paths: [src/a?.py]\n           ^\nexpected ',' or ']', but got '?'\n  in \"<unicode string>\", line 1, column 14:\n    paths: [src/a?.py]\n                 ^", true},
	{"flow-url-query", "k: [http://x/?q=1]\n", "while parsing a flow sequence\n  in \"<unicode string>\", line 1, column 4:\n    k: [http://x/?q=1]\n       ^\nexpected ',' or ']', but got '?'\n  in \"<unicode string>\", line 1, column 14:\n    k: [http://x/?q=1]\n                 ^", true},
	{"flow-key-question", "k: {a?b: 1}\n", "while parsing a flow mapping\n  in \"<unicode string>\", line 1, column 4:\n    k: {a?b: 1}\n       ^\nexpected ',' or '}', but got '?'\n  in \"<unicode string>\", line 1, column 6:\n    k: {a?b: 1}\n         ^", true},
	{"flow-question-at", "[x?@]\n", "while scanning for the next token\nfound character '@' that cannot start any token\n  in \"<unicode string>\", line 1, column 4:\n    [x?@]\n       ^", true},
	{"control-char", "x\x00y", "unacceptable character #x0000: special characters are not allowed\n  in \"<unicode string>\", position 1", true},
	{"two-documents", "a: 1\n---\nb: 2\n", "expected a single document in the stream\n  in \"<unicode string>\", line 1, column 1:\n    a: 1\n    ^\nbut found another document\n  in \"<unicode string>\", line 2, column 1:\n    ---\n    ^", true},
	{"undefined-alias", "a: *x\n", "found undefined alias 'x'\n  in \"<unicode string>\", line 1, column 4:\n    a: *x\n       ^", true},
	{"duplicate-anchor", "[&a 1, &a 2]\n", "found duplicate anchor 'a'; first occurrence\n  in \"<unicode string>\", line 1, column 2:\n    [&a 1, &a 2]\n     ^\nsecond occurrence\n  in \"<unicode string>\", line 1, column 8:\n    [&a 1, &a 2]\n           ^", true},
	{"unknown-tag", "x: !foo 1\n", "could not determine a constructor for the tag '!foo'\n  in \"<unicode string>\", line 1, column 4:\n    x: !foo 1\n       ^", true},
	{"python-tag", "x: !!python/object/apply:os.system ['echo hi']\n", "could not determine a constructor for the tag 'tag:yaml.org,2002:python/object/apply:os.system'\n  in \"<unicode string>\", line 1, column 4:\n    x: !!python/object/apply:os.system  ... \n       ^", true},
	{"unhashable-key", "{[1]: 2}\n", "while constructing a mapping\n  in \"<unicode string>\", line 1, column 1:\n    {[1]: 2}\n    ^\nfound unhashable key\n  in \"<unicode string>\", line 1, column 2:\n    {[1]: 2}\n     ^", true},
	{"scalar-tag-on-seq", "x: !!int [1]\n", "expected a scalar node, but found sequence\n  in \"<unicode string>\", line 1, column 4:\n    x: !!int [1]\n       ^", true},
	{"map-tag-on-scalar", "x: !!map 1\n", "expected a mapping node, but found scalar\n  in \"<unicode string>\", line 1, column 4:\n    x: !!map 1\n       ^", true},
	{"value-tag", "x: =\n", "could not determine a constructor for the tag 'tag:yaml.org,2002:value'\n  in \"<unicode string>\", line 1, column 4:\n    x: =\n       ^", true},
	{"merge-scalar", "a: {<<: 1}\n", "while constructing a mapping\n  in \"<unicode string>\", line 1, column 4:\n    a: {<<: 1}\n       ^\nexpected a mapping or list of mappings for merging, but found scalar\n  in \"<unicode string>\", line 1, column 9:\n    a: {<<: 1}\n            ^", true},
	{"binary-padding", "x: !!binary aGk\n", "failed to decode base64 data: Incorrect padding\n  in \"<unicode string>\", line 1, column 4:\n    x: !!binary aGk\n       ^", true},
	{"binary-ascii", "x: !!binary é\n", "failed to convert base64 data into ascii: 'ascii' codec can't encode character '\\xe9' in position 0: ordinal not in range(128)\n  in \"<unicode string>\", line 1, column 4:\n    x: !!binary é\n       ^", true},
	{"simple-key-long", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: 1\n", "mapping values are not allowed here\n  in \"<unicode string>\", line 1, column 1031:\n     ... aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: 1\n                                         ^", true},
	{"long-line-snippet", "k: [x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, \n", "while parsing a flow node\nexpected the node content, but found '<stream end>'\n  in \"<unicode string>\", line 2, column 1:\n    \n    ^", true},
	{"duplicate-yaml-directive", "%YAML 1.1\n%YAML 1.1\n--- a\n", "found duplicate YAML directive\n  in \"<unicode string>\", line 2, column 1:\n    %YAML 1.1\n    ^", true},
	{"yaml-version", "%YAML 2.0\n--- a\n", "found incompatible YAML document (version 1.* is required)\n  in \"<unicode string>\", line 1, column 1:\n    %YAML 2.0\n    ^", true},
	{"undefined-handle", "a: !x!y 1\n", "while parsing a node\nfound undefined tag handle '!x!'\n  in \"<unicode string>\", line 1, column 4:\n    a: !x!y 1\n       ^", true},
	{"uri-escape", "a: !<%ff> 1\n", "while scanning a tag\n  in \"<unicode string>\", line 1, column 4:\n    a: !<%ff> 1\n       ^\n'utf-8' codec can't decode byte 0xff in position 0: invalid start byte\n  in \"<unicode string>\", line 1, column 6:\n    a: !<%ff> 1\n         ^", true},
	{"indent-zero", "a: |0\n  x\n", "while scanning a block scalar\n  in \"<unicode string>\", line 1, column 4:\n    a: |0\n       ^\nexpected indentation indicator in the range 1-9, but found 0\n  in \"<unicode string>\", line 1, column 5:\n    a: |0\n        ^", true},
	{"doc-separator-in-quote", "a: 'x\n---\n'\n", "while scanning a quoted scalar\n  in \"<unicode string>\", line 1, column 4:\n    a: 'x\n       ^\nfound unexpected document separator\n  in \"<unicode string>\", line 2, column 1:\n    ---\n    ^", true},
}

var aliasCases = []struct {
	name, doc, want string
	err             bool
}{
	{"plain", "a: 1\n", "{'a': 1}", false},
	{"alias", "base: &b {x: 1}\nuse: *b\n", "別名", true},
	{"merge", "base: &b {type: prompt}\nnodes:\n  - {<<: *b, id: s}\n", "別名", true},
	{"flow", "nodes: [*x]\n", "別名", true},
	{"later-document", "a: 1\n---\nb: *x\n", "別名", true},
	{"anchor-only", "nodes: &n\n  - {id: s}\n", "{'nodes': [{'id': 's'}]}", false},
	{"error-before-alias", "a: [1\nb: *x\n", "while parsing a flow sequence\n  in \"<unicode string>\", line 1, column 4:\n    a: [1\n       ^\nexpected ',' or ']', but got ':'\n  in \"<unicode string>\", line 2, column 2:\n    b: *x\n     ^", true},
}

// outcome は読めたら値の repr、読めなければ文。読めない文書は必ず errUnreadable を満たす。
func outcome(t *testing.T, load func(string) (any, error), doc string) (string, bool) {
	t.Helper()
	v, err := load(doc)
	if err != nil {
		if !errors.Is(err, errUnreadable) {
			t.Fatalf("読めない文書が ErrUnreadable でない: %v", err)
		}
		return err.Error(), true
	}
	return pyfmt.Repr(v), false
}

// YAML 1.1 の読み（真偽・数・時刻・null・合流・重複したキー）、字句の境界（タブ・BOM・"!" 単体・
// フローの "?"）、組み立ての途中の失敗と構文の誤りの文が、Python の yaml.safe_load と同じ。
func TestLoadMatchesPyYAML(t *testing.T) {
	for _, c := range loadCases {
		t.Run(c.name, func(t *testing.T) {
			got, isErr := outcome(t, Load, c.doc)
			if got != c.want || isErr != c.err {
				t.Errorf("Load(%q)\n got %q (err=%v)\nwant %q (err=%v)", c.doc, got, isErr, c.want, c.err)
			}
		})
	}
}

// 別名を拒む読み方は、どこに別名があっても（合流・フロー・後ろの文書）ErrAlias で断る。
func TestLoadRefusingAliases(t *testing.T) {
	for _, c := range aliasCases {
		t.Run(c.name, func(t *testing.T) {
			got, isErr := outcome(t, LoadRefusingAliases, c.doc)
			if got != c.want || isErr != c.err {
				t.Errorf("LoadRefusingAliases(%q)\n got %q (err=%v)\nwant %q (err=%v)", c.doc, got, isErr, c.want, c.err)
			}
			_, err := LoadRefusingAliases(c.doc)
			if errors.Is(err, ErrAlias) != (c.want == ErrAlias.Error()) {
				t.Errorf("ErrAlias の見分けが違う: %v", err)
			}
		})
	}
}

// 組み立ての途中の失敗は、別名を拒む読み方でも読めない文書として上がる。
func TestRefusingAliasesStillWrapsBuildErrors(t *testing.T) {
	if _, err := LoadRefusingAliases("ticket: !!int\n"); !errors.Is(err, errUnreadable) || errors.Is(err, ErrAlias) {
		t.Errorf("got %v", err)
	}
}

// 入れ子が深い文書は、少し深いだけなら読み、数万段でも異常終了せずに読めない文書として断る。
func TestDeepNesting(t *testing.T) {
	n := 120
	v, err := Load(strings.Repeat("[", n) + strings.Repeat("]", n))
	if err != nil {
		t.Fatalf("%d 段が読めない: %v", n, err)
	}
	for range n - 1 {
		v = v.([]any)[0]
	}
	if l := v.([]any); len(l) != 0 {
		t.Errorf("一番内側が空でない: %v", l)
	}
	n = 100000
	for _, load := range []func(string) (any, error){Load, LoadRefusingAliases} {
		_, err = load(strings.Repeat("[", n) + strings.Repeat("]", n))
		if !errors.Is(err, errUnreadable) || err.Error() != msgTooDeep {
			t.Errorf("%d 段: %v", n, err)
		}
	}
}

// 自分を指す別名は Go の値にできないので、深すぎる入れ子として断る（Python は自分を含む値を作る）。
func TestRecursiveAliasIsRefused(t *testing.T) {
	for _, doc := range []string{"&a [*a]\n", "&a {k: *a}\n"} {
		if _, err := Load(doc); err == nil || err.Error() != msgTooDeep {
			t.Errorf("Load(%q) = %v", doc, err)
		}
	}
}

// 取り出しは Python の str(m.get(k) or "") と isinstance の確かめに合わせ、欄が無い・型が違えば既定の値。
func TestMapAccessors(t *testing.T) {
	v, err := Load("id: 12\nflag: false\nwhen: 2026-01-02\nat: 2026-01-02 03:04:05+09:00\n" +
		"tags: [a, b]\nmixed: [a, 1]\nsub: {x: y}\nempty: ''\non: yes\n")
	if err != nil {
		t.Fatal(err)
	}
	m := v.(*Map)
	texts := map[string]string{"id": "12", "flag": "", "when": "2026-01-02", "at": "2026-01-02 03:04:05+09:00",
		"tags": "['a', 'b']", "empty": "", "missing": ""}
	for key, want := range texts {
		if got := m.Text(key); got != want {
			t.Errorf("Text(%q) = %q, want %q", key, got, want)
		}
	}
	if tags, ok := Strings(m.Get("tags")); !ok || len(tags) != 2 {
		t.Errorf("Strings(tags) = %v %v", tags, ok)
	}
	if _, ok := Strings(m.Get("mixed")); ok {
		t.Error("文字列でない要素のある列を文字列の列として読んだ")
	}
	if got, _ := m.Map("sub").String("x"); got != "y" {
		t.Errorf("Map(sub).String(x) = %q", got)
	}
	if m.Map("missing").Text("x") != "" || m.Map("id").Len() != 0 {
		t.Error("無いマップの取り出しが既定の値を返さない")
	}
	at := m.Get("at").(DateTime)
	if at.ISOFormat() != "2026-01-02T03:04:05+09:00" || at.Time().UTC().Hour() != 18 {
		t.Errorf("ISOFormat = %q", at.ISOFormat())
	}
	// YAML 1.1 では on も真偽なので、キー on は文字列でなく true になる。
	if v, ok := m.Lookup(true); !ok || v != true || m.Has("on") {
		t.Errorf("キー on = %v %v", v, ok)
	}
	if _, ok := m.StringKeys(); ok {
		t.Error("真偽のキーがあるのに StringKeys が真")
	}
}

// 期待値は Python の yamlread.safe_load が上げた例外の type(exc).__name__。
func TestErrorClassMatchesPyYAML(t *testing.T) {
	cases := []struct {
		doc  string
		want Class
	}{
		{"a: \x01", readerError},
		{"a: b: c", scannerError},
		{"a:\tb", scannerError},
		{"a: '", scannerError},
		{"a: [", parserError},
		{"- a\nb: 1", parserError},
		{"%YAML 1.1\n%YAML 1.1\n--- a", parserError},
		{"a: *x", composerError},
		{"a: &x 1\nb: &x 2", composerError},
		{"--- a\n--- b", composerError},
		{"a: !!python/name:os.system x", ConstructorError},
		{"? [a]\n: 1", ConstructorError},
		{"a: !!int abc", loadError},
		{"a: !!bool maybe", loadError},
		{"a: !!timestamp 2020-13-01", loadError},
		{"a: " + strings.Repeat("[", 3000), loadError},
	}
	for _, c := range cases {
		_, err := Load(c.doc)
		var yerr *Error
		if !errors.As(err, &yerr) || yerr.Class() != c.want {
			t.Errorf("Load(%q) = %v, want %s", c.doc, err, c.want)
		}
	}
}

// 期待は Python の yaml.safe_load(a) == yaml.safe_load(b)。
func TestEqualMatchesPython(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"{a: 1, b: [1, 2]}", "{b: [1, 2.0], a: true}", true},
		{"{a: .nan}", "{a: .nan}", true},
		{".nan", ".nan", false},
		{"[1, 2]", "[1, 2, 3]", false},
		{"{a: 1}", "{a: 1, b: 2}", false},
		{"2001-01-01", "2001-01-01", true},
		{"2001-01-01", "2001-01-01 00:00:00", false},
		{"2001-01-01 00:00:00+09:00", "2000-12-31 15:00:00Z", true},
		{"2001-01-01 00:00:00", "2001-01-01 00:00:00Z", false},
		{"x", "x", true},
		{`"1"`, "1", false},
		{"null", "~", true},
		{"9007199254740993", "9007199254740992.0", false},
		{"{1: a}", "{1.0: a}", true},
		{"[.inf]", "[.inf]", true},
		{"!!binary YQ==", "!!binary YQ==", true},
	}
	for _, c := range cases {
		a, errA := Load(c.a)
		b, errB := Load(c.b)
		if errA != nil || errB != nil {
			t.Fatal(errA, errB)
		}
		if got := Equal(a, b); got != c.want {
			t.Errorf("Equal(%q, %q) = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}

func TestMapWithout(t *testing.T) {
	v, err := Load("{b: 1, a: 2, 3: x, c: 4}")
	if err != nil {
		t.Fatal(err)
	}
	got := v.(*Map).Without("a", "c", "3")
	if keys := got.Keys(); len(keys) != 2 || keys[0] != "b" || keys[1] != 3 {
		t.Errorf("順を保ち、文字列のキーだけを外す: %v", keys)
	}
	if (*Map)(nil).Without("a").Len() != 0 {
		t.Error("nil の Map は空")
	}
}
