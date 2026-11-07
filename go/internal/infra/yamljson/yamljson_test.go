package yamljson

import (
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
)

// 期待は、YAML を読んだ値を default=str で JSON にして読み直し、ASCII でない字をそのまま書いたもの。
func TestToJSONMatchesExpected(t *testing.T) {
	cases := map[string]string{
		"{a: 1, 2: [x, 2001-01-02], true: null, ~: 1.5, .inf: b, 1.0: c}": `{"a": 1, "2": ["x", "2001-01-02"], "true": "c", "null": 1.5, "Infinity": "b"}`,
		"!!set {a: null}":           `"{'a'}"`,
		"!!binary YQ==":             `"b'a'"`,
		"2001-01-01 10:00:00+09:00": `"2001-01-01 10:00:00+09:00"`,
		"[{x: .nan}]":               `[{"x": NaN}]`,
		"{日本: [1, {b: -.inf}]}":     `{"日本": [1, {"b": -Infinity}]}`,
	}
	for src, want := range cases {
		v, err := yaml.Load(src)
		if err != nil {
			t.Fatal(err)
		}
		got, err := pyjson.Dumps(ToJSON(v), pyjson.Options{RawUnicode: true})
		if err != nil || got != want {
			t.Errorf("ToJSON(%q) = %s, %v, want %s", src, got, err, want)
		}
	}
}

// JSON の形で組んだ値は、順を保ったまま yaml.Dump に渡せる形になる。
func TestFromJSON(t *testing.T) {
	v := FromJSON([]any{"a", pyjson.NewObject("type", "x", "after", []any{1, 2}, "review", true)})
	got, err := yaml.Dump(v, yaml.DumpOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if want := "- a\n- type: x\n  after:\n  - 1\n  - 2\n  review: true\n"; got != want {
		t.Errorf("%q, want %q", got, want)
	}
}
