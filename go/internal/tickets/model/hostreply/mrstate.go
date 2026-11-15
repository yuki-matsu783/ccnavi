package hostreply

import (
	"fmt"
	"regexp"
	"strings"
)

// MR の今の状態（`ccnavi-review.sh mr-state`。ボードとの約束）。答えの 2 行目の形は
//
//	open <番号> <yes|no> <URL> / merged <番号> <URL> / closed <番号> <URL> / unknown <種類> <理由>
//
// で、種類は no-tool・auth・network・api・not-found。理由は 1 行の日本語で、ホストの返事の本文はコピーしない
// （資格情報が入りうるため）。

// MRState は、MR 1 本の応答（failed なら失敗の返事）を読み、状態（open・merged・closed・unknown）と答えの 2 行目を返す。
// kind は github か gitlab、scheme・host・path は origin から読んだもの（URL が使えないときに組む）、n は聞いた番号。
func MRState(kind, scheme, host, path, n string, failed bool, data []byte) (string, string) {
	if failed {
		kindOf, why := classifyFailure(string(data), n)
		return unknown(kindOf, why)
	}
	got, err := mrFields(kind, data)
	if err != nil {
		return unknown("api", "ホストの答えを読めなかった")
	}
	if got.number != n {
		return unknown("api", fmt.Sprintf("ホストが別の MR（%s）を返した", got.number))
	}
	url := mrURL(kind, scheme, host, path, n, got.url)
	if got.merged {
		return "merged", fmt.Sprintf("merged %s %s", n, url)
	}
	switch got.state {
	case "open", "opened":
		draft := "no"
		if got.draft {
			draft = "yes"
		}
		return "open", fmt.Sprintf("open %s %s %s", n, draft, url)
	case "closed":
		return "closed", fmt.Sprintf("closed %s %s", n, url)
	case "locked":
		return unknown("api", fmt.Sprintf("MR #%s はマージの途中（locked）", n))
	}
	return unknown("api", fmt.Sprintf("ホストが想定外の state（%s）を返した", got.state))
}

// unknown は unknown の答え。理由は 1 行にそろえる（制御文字を空白にし、空白の並びを 1 つにして前後を落とす）。
func unknown(kind, why string) (string, string) {
	why = strings.Join(strings.FieldsFunc(why, func(r rune) bool { return r == ' ' || (r >= 0x01 && r <= 0x1f) || r == 0x7f }), " ")
	if why == "" {
		why = "理由は取れなかった"
	}
	return "unknown", "unknown " + kind + " " + why
}

type mrSeen struct {
	number, state, url string
	merged, draft      bool
}

// mrFields は MR 1 本の応答の番号・state・マージ済みか・Draft か・URL。表でなければ ErrShape。
func mrFields(kind string, data []byte) (mrSeen, error) {
	v, err := Decode(data)
	if err != nil {
		return mrSeen{}, err
	}
	m, ok := v.(map[string]any)
	if !ok {
		return mrSeen{}, ErrShape
	}
	state := Text(Alt(m["state"], "-"))
	if kind == "github" {
		return mrSeen{
			number: Text(Alt(m["number"], "-")),
			state:  state,
			url:    Text(Alt(m["html_url"], "")),
			merged: m["merged"] == true || Truthy(m["merged_at"]),
			draft:  m["draft"] == true,
		}, nil
	}
	return mrSeen{
		number: Text(Alt(m["iid"], "-")),
		state:  state,
		url:    Text(Alt(m["web_url"], "")),
		merged: m["state"] == "merged",
		draft:  Alt(m["draft"], m["work_in_progress"]) == true,
	}, nil
}

// URL に埋めた資格情報（sh の ccnavi_mask_url と同じ式）。
var (
	urlCredentials = regexp.MustCompile(`^([A-Za-z][A-Za-z0-9+.-]*://)[^/]*@`)
	scpCredentials = regexp.MustCompile(`^[^/:@]*:[^/]*@`)
)

// MaskURL は URL に埋めた資格情報を伏せる（sh の ccnavi_mask_url と同じ）。
func MaskURL(url string) string {
	url = urlCredentials.ReplaceAllString(url, "${1}<伏せた>@")
	return scpCredentials.ReplaceAllLiteralString(url, "<伏せた>@")
}

// mrURL は答えに書く URL。資格情報を伏せ、空・空白や制御文字を含むときは、ホスト名とパスから組む。
func mrURL(kind, scheme, host, path, n, raw string) string {
	url := MaskURL(raw)
	if url != "" && !strings.ContainsFunc(url, func(r rune) bool { return r <= 0x20 || r == 0x7f }) {
		return url
	}
	if kind == "github" {
		return fmt.Sprintf("%s://%s/%s/pull/%s", scheme, host, path, n)
	}
	return fmt.Sprintf("%s://%s/%s/-/merge_requests/%s", scheme, host, path, n)
}

// HTTP の番号の書き方（gh の `HTTP 404`、curl の `error: 404`、glab の `: 404`）。小文字にした返事から読む。
var failCode = regexp.MustCompile(`(?:http |error: |: )([0-9]{3})(?: |$)`)

// 届かなかったことを表す言葉（小文字）。curl は 404 なども `error: 404` と書くので、状態コードより先に見る。
var networkWords = []string{
	"could not resolve", "couldn't resolve", "no such host", "failed to connect", "couldn't connect",
	"connection refused", "connection reset", "timed out", "timeout", "network is unreachable", "dial tcp",
	"i/o timeout", "curl: (6)", "curl: (7)", "curl: (28)", "curl: (35)", "curl: (52)", "curl: (56)", "curl: (60)",
}

const (
	whyNotFound = "MR #%s が見つからない（番号か、リポジトリへの権限を確かめる）"
	whyAuth     = "認証に失敗した（トークンか gh / glab の認証を確かめる）"
)

// classifyFailure は API の失敗の返事から、unknown の種類と理由を決める。返事の本文は理由にコピーしない。
func classifyFailure(text, n string) (string, string) {
	low := asciiLower(text)
	code := ""
	for _, line := range strings.Split(low, "\n") {
		if m := failCode.FindStringSubmatch(line); m != nil {
			code = m[1]
			break
		}
	}
	with := ""
	if code != "" {
		with = "（HTTP " + code + "）"
	}
	for _, w := range networkWords {
		if strings.Contains(low, w) {
			return "network", "ホストに届かない（ネットワークか、ホスト名を引けない）"
		}
	}
	if strings.Contains(low, "rate limit") {
		return "api", "ホストの API の利用上限に達した" + with
	}
	switch code {
	case "404":
		return "not-found", fmt.Sprintf(whyNotFound, n) + with
	case "401", "403":
		return "auth", whyAuth + with
	}
	switch {
	case strings.Contains(low, "not found"):
		return "not-found", fmt.Sprintf(whyNotFound, n)
	case strings.Contains(low, "bad credentials"), strings.Contains(low, "unauthorized"),
		strings.Contains(low, "forbidden"), strings.Contains(low, "authentication"):
		return "auth", whyAuth
	}
	return "api", "ホストの API が失敗した" + with
}

// asciiLower は ASCII の英大文字だけを小文字にする（sh の tr 'A-Z' 'a-z' と同じ）。
func asciiLower(s string) string {
	return strings.Map(func(r rune) rune {
		if r >= 'A' && r <= 'Z' {
			return r + 'a' - 'A'
		}
		return r
	}, s)
}
