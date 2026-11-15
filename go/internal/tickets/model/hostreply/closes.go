package hostreply

import (
	"strconv"

	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyre"
)

// MR の本文の閉じる語の読み方。GitHub が本文から issue を閉じる書き方に合わせる: 語は close・closes・closed・fix・
// fixes・fixed・resolve・resolves・resolved（大文字小文字は問わない。語の一部の `precloses`・`bug-fix` は拾わない）、
// 続けて `:` を置いてもよく、空白のあとに 1 つの指定（`#<番号>`・`<owner>/<repo>#<番号>`・issue の URL）。
// GitHub は否定（`does not close #1`）を見分けずに閉じるので、ここも見分けない。コードブロック・インラインのコード・
// HTML の注釈・引用の行の中の文字は除いてから読む。sh の start（GraphQL を断られたときの代わり）と Chrome 拡張の
// 「始める」（WASM の closes）が同じこの読み方を使う。
var (
	bodyFence   = pyre.MustCompile("```.*?```", pyre.DotAll)
	bodyComment = pyre.MustCompile("<!--.*?-->", pyre.DotAll)
	bodyCode    = pyre.MustCompile("`[^`\n]*`", 0)
	bodyQuote   = pyre.MustCompile(`^[ \t]*>[^\n]*`, pyre.Multiline)
	// 入れ子のグループ（GitLab の group/sub/proj）も 1 つのリポジトリの指定として読む。
	bodyClosing = pyre.MustCompile(`(?<![A-Za-z0-9_-])(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?[ \t]+`+
		`(?:https?://[^\s/]+/(?P<urlrepo>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+?)/(?:-/)?issues/(?P<urlnum>[0-9]+)`+
		`|(?P<repo>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)?#(?P<num>[0-9]+))(?![A-Za-z0-9_])`, pyre.IgnoreCase)
)

// ClosingRefs は本文が閉じる issue を `<owner/repo>#<番号>` で返す（出てきた順）。番号だけの指定には repo を前に付ける。
func ClosingRefs(repo, body string) []string {
	drop := func(pyre.Found) string { return "" }
	for _, re := range []*pyre.Regexp{bodyFence, bodyComment, bodyCode, bodyQuote} {
		body = re.Sub(body, drop)
	}
	var out []string
	for _, f := range bodyClosing.FindAll(body) {
		path, num := repo, ""
		if n, ok := f.Named("urlnum"); ok && n != "" {
			path, _ = f.Named("urlrepo")
			num = n
		} else {
			num, _ = f.Named("num")
			if r, ok := f.Named("repo"); ok && r != "" {
				path = r
			}
		}
		out = append(out, path+"#"+num)
	}
	return out
}

// ClosesIssue は本文が repo の issue 番号 issue を閉じるか（リポジトリの名前は大文字小文字を区別しない）。
func ClosesIssue(repo string, issue int, body string) bool {
	want := pathcase.Fold(repo + "#" + strconv.Itoa(issue))
	for _, ref := range ClosingRefs(repo, body) {
		if pathcase.Fold(ref) == want {
			return true
		}
	}
	return false
}
