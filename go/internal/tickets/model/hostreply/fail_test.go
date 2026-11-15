package hostreply

import (
	"strings"
	"testing"
)

// 失敗の返事から HTTP の番号・GraphQL を使えない 403 か・本文・probe の分類を読む。
func TestFailReplies(t *testing.T) {
	cases := []struct {
		text, status string
		blocked      bool
		probe        string
	}{
		{"curl: (22) The requested URL returned error: 404\n", "404", false, "notfound"},
		{"curl: (22) The requested URL returned error: 403", "403", true, "auth"},
		{`{"message":"GitHub GraphQL is not available"}` + "\ngh: Forbidden (HTTP 403)", "403", true, "auth"},
		{`{"message":"Bad credentials"}` + "\ngh: Bad credentials (HTTP 401)", "401", false, "auth"},
		{"glab: HTTP 404", "404", false, "notfound"},
		// 本文の中の数字に引かれない（形の決まった書き方が無ければ、最後の空でない行だけを読む）
		{"line 500 of the body\nerror: 502 Bad Gateway\n\n", "502", false, "network"},
		{"curl: (6) Could not resolve host: h", "", false, "network"},
		{`{"error":"HTTP 500 in text"}` + "\nglab: 403 Forbidden", "403", false, "auth"},
		{`{"error":"The provided content-type '' is not supported."}` + "\nglab: HTTP 415", "415", false, "network"},
	}
	for _, c := range cases {
		if got := FailStatus(c.text); got != c.status {
			t.Errorf("FailStatus(%q) = %q, want %q", c.text, got, c.status)
		}
		if got := GraphQLBlocked(c.text); got != c.blocked {
			t.Errorf("GraphQLBlocked(%q) = %v", c.text, got)
		}
		if got := Probe(true, []byte(c.text)); got != c.probe {
			t.Errorf("Probe(failed, %q) = %q, want %q", c.text, got, c.probe)
		}
	}
	// 本文は 1 行・制御文字なし・先頭 200 文字（バイトでなく文字で切る）・トークンは伏せる
	bodies := map[string]string{
		"tok s3cr3t\n\x01\tend":  "tok *** end",
		"a\r\nb\x1b[31mc\td":     "a b [31mc d",
		strings.Repeat("あ", 300): strings.Repeat("あ", 200),
	}
	for text, want := range bodies {
		if got := FailBody(text, []string{"s3cr3t", ""}); got != want {
			t.Errorf("FailBody(%q) = %q, want %q", text, got, want)
		}
	}
	for data, want := range map[string]string{`{"id": 1}`: "ok", `[]`: "badjson", `<html>`: "badjson"} {
		if got := Probe(false, []byte(data)); got != want {
			t.Errorf("Probe(ok, %s) = %q, want %q", data, got, want)
		}
	}
}

// mr-state の答え（ボードとの約束）。失敗の種類の分け方と、URL が使えないときの組み方。
func TestMRState(t *testing.T) {
	cases := []struct {
		kind   string
		failed bool
		data   string
		state  string
		answer string
	}{
		{"github", false, `{"number": 9, "state": "open", "draft": true, "html_url": "https://u/9"}`, "open", "open 9 yes https://u/9"},
		{"github", false, `{"number": 9, "state": "closed", "merged_at": "x", "html_url": "https://a:b@u/9"}`, "merged", "merged 9 https://<伏せた>@u/9"},
		{"gitlab", false, `{"iid": 9, "state": "opened", "work_in_progress": true, "web_url": "a b"}`, "open", "open 9 yes https://h/g/p/-/merge_requests/9"},
		{"gitlab", false, `{"iid": 9, "state": "locked"}`, "unknown", "unknown api MR #9 はマージの途中（locked）"},
		{"github", false, `{"number": 10}`, "unknown", "unknown api ホストが別の MR（10）を返した"},
		{"github", false, `[]`, "unknown", "unknown api ホストの答えを読めなかった"},
		{"github", true, "curl: (22) The requested URL returned error: 404", "unknown", "unknown not-found MR #9 が見つからない（番号か、リポジトリへの権限を確かめる）（HTTP 404）"},
		{"github", true, "glab: 401 Unauthorized", "unknown", "unknown auth 認証に失敗した（トークンか gh / glab の認証を確かめる）（HTTP 401）"},
		{"github", true, "curl: (28) Operation timed out", "unknown", "unknown network ホストに届かない（ネットワークか、ホスト名を引けない）"},
		{"github", true, "API rate limit exceeded\ncurl: (22) The requested URL returned error: 403", "unknown", "unknown api ホストの API の利用上限に達した（HTTP 403）"},
	}
	for _, c := range cases {
		state, answer := MRState(c.kind, "https", "h", "g/p", "9", c.failed, []byte(c.data))
		if state != c.state || answer != c.answer {
			t.Errorf("MRState(%s, %q) = %q %q, want %q %q", c.kind, c.data, state, answer, c.state, c.answer)
		}
	}
}
