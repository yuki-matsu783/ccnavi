package digest

import "testing"

// 期待は Python 3.13 の base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))。nil は binascii.Error か ValueError。
func TestURLSafeB64DecodeMatchesPython(t *testing.T) {
	cases := []struct {
		in   string
		want []byte
	}{
		{"YWJj", []byte("abc")}, {"YWI", []byte("ab")}, {"YQ", []byte("a")}, {"Y", nil},
		{"YW-_", []byte("ao\xbf")}, {"a!b@YWJj", nil}, {"YQ==YWJj", nil}, {"YWJjZA", []byte("abcd")},
		{"", []byte{}}, {"日本", nil},
		{"YQ==", []byte("a")}, {"YQ==!", []byte("a")}, {"YQ==YQ==", []byte("a\x06\x10")}, {"YQ=a", nil},
		{"YWJ=YQ==", nil}, {"=YQ==", []byte("a")}, {"YWI=x", []byte("ab1")}, {"YQ=x=", []byte("a\x0c")},
		{"Y=Q=", nil}, {"YQ=!=", []byte("a")}, {"YW=Jj", []byte("abc")}, {"====", []byte{}},
	}
	for _, c := range cases {
		got, ok := URLSafeB64Decode(c.in)
		if c.want == nil {
			if ok {
				t.Errorf("%q: 読めないはずが %q", c.in, got)
			}
			continue
		}
		if !ok || string(got) != string(c.want) {
			t.Errorf("%q: %q, %v, want %q", c.in, got, ok, c.want)
		}
	}
}
