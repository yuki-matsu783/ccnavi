package redact

import "regexp"

// URL に埋めた資格情報。scheme のある形（`https://user:token@host/...`）と、scheme の無い scp の形
// （`user:token@host:path`）の 2 つ。トークンに `@` が入ることがあるので、ホストの手前の最後の `@` までを伏せる
// （`[^/]*` が `@` も食うので、最後の `@` で止まる）。sh の ccnavi_mask_url と同じ式で、一致は
// tests/fixtures の表で確かめる。
var (
	urlCredentials = regexp.MustCompile(`^([A-Za-z][A-Za-z0-9+.-]*://)[^/]*@`)
	scpCredentials = regexp.MustCompile(`^[^/:@]*:[^/]*@`)
)

// URLCredentials は、URL に埋めた資格情報を `<伏せた>@` にする。画面や苦情の文に origin の URL を出す前に通す。
// Redact と違い、記録に書く文字列ではなく、利用者に見せる URL の 1 つを相手にする（伏せた後の形も URL のまま読める）。
func URLCredentials(url string) string {
	url = urlCredentials.ReplaceAllString(url, "${1}<伏せた>@")
	return scpCredentials.ReplaceAllLiteralString(url, "<伏せた>@")
}
