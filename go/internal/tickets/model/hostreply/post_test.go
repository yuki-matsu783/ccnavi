package hostreply

import (
	"errors"
	"testing"
)

func TestPostedFoundSent(t *testing.T) {
	const mr = "https://gitlab.example/g/p/-/merge_requests/7"
	posted := func(kind string, form PostForm, target, data string) func() (string, error) {
		return func() (string, error) {
			p, err := Posted(kind, form, target, []byte(data))
			return p.Lines(), err
		}
	}
	found := func(kind, mark, target, data string) func() (string, error) {
		return func() (string, error) {
			p, ok, err := Found(kind, mark, target, []byte(data))
			if !ok {
				return "", err
			}
			return p.Lines(), err
		}
	}
	sent := func(form PostForm, url string) func() (string, error) {
		return func() (string, error) { return Sent(form, url).Lines(), nil }
	}
	cases := []struct {
		name string
		run  func() (string, error)
		want string
		err  error
	}{
		{"GitHub のコメント", posted("github", PostComment, "-", `{"html_url":"https://h/c/1","created_at":"t","user":{"login":"me"}}`),
			"url https://h/c/1\ncreated_at t\nauthor me\n", nil},
		{"GitLab のコメントは対象の URL に note の id を足す", posted("gitlab", PostComment, mr, `{"id":5,"created_at":"t","author":{"id":3}}`),
			"url " + mr + "#note_5\ncreated_at t\nauthor 3\n", nil},
		{"URL の無い応答は投稿できていない", posted("github", PostComment, "-", `{"message":"x"}`), "", ErrNotPosted},
		{"GitLab の issue", posted("gitlab", PostIssue, "-", `{"iid":12,"web_url":"https://g/i/12"}`), "url https://g/i/12\nnumber 12\n", nil},
		{"目印で始まる最初のコメント", found("github", "<!-- ccnavi:request", "-",
			`[{"body":"x <!-- ccnavi:request","html_url":"a"},{"body":"<!-- ccnavi:request p","html_url":"b"},{"body":"<!-- ccnavi:request q","html_url":"c"}]`),
			"url b\n", nil},
		{"目印の無い一覧", found("gitlab", "<!-- ccnavi:", mr, `[{"body":null,"id":1}]`), "", nil},
		{"送り済みの issue は URL の最後の区切りが番号", sent(PostIssue, "https://h/o/r/issues/9"), "url https://h/o/r/issues/9\nnumber 9\n", nil},
		{"数でない区切りは 0", sent(PostIssue, "https://h/x"), "url https://h/x\nnumber 0\n", nil},
	}
	for _, c := range cases {
		got, err := c.run()
		if !errors.Is(err, c.err) || (c.err == nil && got != c.want) {
			t.Errorf("%s: %q（%v）、want %q（%v）", c.name, got, err, c.want, c.err)
		}
	}
}

func TestAccount(t *testing.T) {
	cases := []struct{ kind, data, want string }{
		{"github", `{"login":"octo-cat"}`, "octo-cat"},
		{"gitlab", `{"username":"a.b_c"}`, "a.b_c"},
		{"github", `{"login":"-x"}`, ""},
		{"github", `{"login":"a b"}`, ""},
		{"github", `{"message":"Bad credentials"}`, ""},
		{"gitlab", ``, ""},
	}
	for _, c := range cases {
		if got := Account(c.kind, []byte(c.data)); got != c.want {
			t.Errorf("Account(%s, %s) = %q、want %q", c.kind, c.data, got, c.want)
		}
	}
}
