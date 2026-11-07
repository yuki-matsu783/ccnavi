package yaml

import "testing"

// 囲いの読み方の違い（頭の空白・`...` の閉じ）。読み手ごとの答えは各パッケージの Python との突き合わせが見る。
func TestFrontBody(t *testing.T) {
	plain, document, bom := FrontStyle{Indented: true}, FrontStyle{DocEnd: true}, FrontStyle{SkipBOM: true}
	cases := []struct {
		text  string
		style FrontStyle
		body  string
		state FrontState
	}{
		{"---\na: 1\n---\nbody", plain, "a: 1", FrontClosed},
		{" ---\na: 1\n --- \n", plain, "a: 1", FrontClosed},
		{" ---\na: 1\n---\n", document, "", FrontAbsent},
		{"---\na: 1\n...\n", document, "a: 1", FrontClosed},
		{"---\na: 1\n...\n", plain, "", FrontUnclosed},
		{"---\r\na: 1\r\n---\r\n", document, "a: 1", FrontClosed},
		{"", plain, "", FrontAbsent},
		{BOM + "---\na: 1\n---\n", bom, "a: 1", FrontClosed},
		{BOM + BOM + "---\na: 1\n---\n", bom, "", FrontAbsent},
		{BOM + "---\na: 1\n---\n", plain, "", FrontAbsent},
	}
	for _, c := range cases {
		body, state := FrontBody(c.text, c.style)
		if body != c.body || state != c.state {
			t.Errorf("FrontBody(%q, %+v) = %q, %v", c.text, c.style, body, state)
		}
	}
}
