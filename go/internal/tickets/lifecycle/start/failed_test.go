package start

import "testing"

func TestAPIFailed(t *testing.T) {
	for _, c := range []struct {
		name                    string
		what, status, body, why string
		want                    string
	}{
		{"権限が無い", "GET repos/a/b/issues/7", "403", "Forbidden", "",
			"reason ホストの API が失敗した（GET repos/a/b/issues/7）。HTTP 403: Forbidden\nrefused 権限がありません（HTTP 403）\n"},
		{"認証が通らない", "GET x", "401", "", "",
			"reason ホストの API が失敗した（GET x）。HTTP 401: （本文なし）\nrefused 権限がありません（HTTP 401）\n"},
		{"見つからない", "GET x", "404", "Not Found", "",
			"reason ホストの API が失敗した（GET x）。HTTP 404: Not Found\nrefused 見つからない（HTTP 404）\n"},
		{"消された", "GET x", "410", "Gone", "", "reason ホストの API が失敗した（GET x）。HTTP 410: Gone\nrefused 見つからない（HTTP 410）\n"},
		{"ほかの番号は断りに数えない", "GET x", "500", "oops", "", "reason ホストの API が失敗した（GET x）。HTTP 500: oops\n"},
		{"届かない", "GET x", "", "timeout", "", "reason ホストの API が失敗した（GET x）。ホストの返事: timeout\n"},
		{"返事も無い", "GET x", "", "", "", "reason ホストの API が失敗した（GET x）\n"},
		{"呼び出しが無ければ前の理由", "", "", "", "紐づく issue が多すぎて読み切れない", "reason 紐づく issue が多すぎて読み切れない\n"},
		{"何も無い", "", "", "", "", "reason ホストの API が失敗した（?）\n"},
	} {
		if got := APIFailed(c.what, c.status, c.body, c.why); got != c.want {
			t.Errorf("%s: %q", c.name, got)
		}
	}
}
