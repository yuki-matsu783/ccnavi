package host

import "testing"

// origin の URL の読み方。トークンを埋めた形（`https://oauth2:<token>@host/...`）で、ユーザ情報が
// ホストに入り込まないこと。sh の側は tests/sh/test_review_origin.py が確かめる。
func TestClassifyReadsTheHostThroughUserinfoAndPort(t *testing.T) {
	cases := map[string]Kind{
		"https://github.com/o/r.git":                           GitHub,
		"git@github.com:o/r.git":                               GitHub,
		"https://oauth2:glpat-secret@github.com/o/r.git":       GitHub,
		"http://localhost:8929/root/p.git":                     GitLab,
		"http://oauth2:glpat-secret@localhost:8929/root/p.git": GitLab,
		"ssh://git@gitlab.example.com:2222/g/p.git":            GitLab,
		"git@gitlab.example.com:g/p.git":                       GitLab,
		// ユーザ情報に `@` が入る。git は最後の `@` で切るので、こちらもそう読む。
		"https://user:p@ss@github.com/o/r.git":               GitHub,
		"https://oauth2:glpat-A@B@localhost:8929/root/p.git": GitLab,
		// パスに `@` があっても authority の外なのでホストに入らない。
		"git@github.com:o/r@x.git": GitHub,
		"https://[::1]:9/g/p.git":  GitLab,
		// scheme の大文字小文字は区別しない。
		"HTTPS://github.com/o/r.git": GitHub,
		// sh が読めない表記は、こちらも読めない扱い。
		"ssh://user@host.example.com/g/p.git": "",
		"not a url":                           "",
	}
	for url, want := range cases {
		if got := classify(url).kind; got != want {
			t.Errorf("Classify(%q).Kind = %q, want %q", url, got, want)
		}
	}
}
