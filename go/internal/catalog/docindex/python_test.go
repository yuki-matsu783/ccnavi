package docindex

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
)

// Python の docsearch_index.front_matter と突き合わせる。YAML の型（日時・バイト列・集合・無限・キーの型）を
// JSON に載せる規則、読めない・閉じていない・深すぎるものの境界を見る。

var frontInputs = []string{
	"---\ntype: guide\ntitle: はじめに\ntags: [a, b]\n---\n",
	"---\ntype: guide\n...\nbody",
	"\ufeff---\ntype: a\n---\n",
	"---  \ntype: a\n---\t\n",
	"--- \n---\n",
	"---\n---\n",
	"---\n# comment only\n---\n",
	"---\nnull\n---\n",
	"---\n[a]\n---\n",
	"---\nwhen: 2001-12-14t21:59:43.10-05:00\nday: 2002-12-14\nnaive: 2001-12-15 2:59:43.1\n---\n",
	"---\nbin: !!binary aGVsbG8=\nbad: !!binary //79\n---\n",
	"---\ns: !!set {a}\n---\n", // 集合の str() の順は Python のハッシュで変わるので 1 要素
	"---\ninf: .inf\nninf: -.inf\nnan: .NaN\nf: 1.5\ne: 1e3\n---\n",
	"---\n1: one\n2.5: x\ntrue: t\n~: n\n2001-01-01: d\n---\n",
	"---\n1: one\n\"1\": two\n---\n",
	"---\nyes: no\non: off\noct: 0o17\nold: 017\nhex: 0x1f\nbig: 123456789012345678901234567890\n---\n",
	"---\nomap: !!omap [a: 1, b: 2]\npairs: !!pairs [a: 1, a: 2]\n---\n",
	"---\nn: " + strings.Repeat("[", 31) + strings.Repeat("]", 31) + "\n---\n",
	"---\nn: " + strings.Repeat("[", 32) + strings.Repeat("]", 32) + "\n---\n",
	"---\nn: 1" + strings.Repeat("9", 4299) + "\n---\n",
	"---\nn: 1" + strings.Repeat("9", 4300) + "\n---\n",
	"---\na: &x 1\nb: *x\n---\n",
	"---\ntitle: \"x\\Ly\\u0085z\"\n---\n",
	"---\ntitle: bad\xff\xfebytes\n---\n",
	"---\r\ntype: crlf\r\n---\r\n",
	"---\ntype: a\n" + strings.Repeat("x: "+strings.Repeat("y", 100)+"\n", 700) + "---\n",
	"---\nk: {a: [1, {b: null}]}\n---\n",
	"---\n? [a, b]\n: c\n---\n",
	"---\nkey: value\n  bad indent\n---\n",
	"---\ntab:\tvalue\n---\n",
	"---\nx: !!int\n---\n",
}

func TestFrontMatterMatchesPython(t *testing.T) {
	want := golden.Load[[]*string](t, "front_matter")
	for i, text := range frontInputs {
		var got *string
		if front := FrontMatter([]byte(text)); front != nil {
			line, err := Compact(front)
			if err != nil {
				t.Fatal(err)
			}
			got = &line
		}
		if (got == nil) != (want[i] == nil) || (got != nil && *got != *want[i]) {
			t.Errorf("%.60q:\nGo:     %v\nPython: %v", text, show(got), show(want[i]))
		}
	}
}

func show(s *string) string {
	if s == nil {
		return "None"
	}
	return *s
}
