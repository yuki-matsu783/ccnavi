package hostreply

import (
	"strconv"

	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyre"
)

// MR の本文の閉じる語の読み方。ホストが本文から issue を閉じる書き方に合わせ、ホストの種類（github・gitlab）で分ける。
//
//   - GitHub: 語は close・closes・closed・fix・fixes・fixed・resolve・resolves・resolved。続けて `:` を置いてもよく、
//     空白のあとに 1 つの指定
//   - GitLab: 既定の閉じる語の形（default closing pattern）。語は GitHub の語に closing・fixing・resolving と
//     implement・implements・implemented・implementing を足したもの。`:` と空白のあとに `issue`・`issues` を挟んでよく、
//     指定を `,`・`and`・空白で区切って並べられる（`Closes #1, #2 and #3`）
//
// 指定は `#<番号>`・`<owner>/<repo>#<番号>`・issue の URL。語の大文字小文字は問わず、語の一部（`precloses`・`bug-fix`）は
// 拾わない。否定（`does not close #1`）はホストと同じく見分けない。コードブロック・インラインのコード・HTML の注釈・
// 引用の行の中の文字は除いてから読む。sh の start（GitHub で GraphQL を断られたときの代わり）と Chrome 拡張の
// 「始める」（WASM の closes。GitHub と GitLab）が同じこの読み方を使う。
var (
	bodyFence   = pyre.MustCompile("```.*?```", pyre.DotAll)
	bodyComment = pyre.MustCompile("<!--.*?-->", pyre.DotAll)
	bodyCode    = pyre.MustCompile("`[^`\n]*`", 0)
	bodyQuote   = pyre.MustCompile(`^[ \t]*>[^\n]*`, pyre.Multiline)

	// 指定 1 つ。入れ子のグループ（GitLab の group/sub/proj）も 1 つのリポジトリの指定として読む。
	closingRef = pyre.MustCompile(`(?:https?://[^\s/]+/(?P<urlrepo>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+?)/(?:-/)?issues/(?P<urlnum>[0-9]+)`+
		`|(?P<repo>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)?#(?P<num>[0-9]+))(?![A-Za-z0-9_])`, pyre.IgnoreCase)
	// 語と指定の並び（1 つの節）を拾う式。指定の中身は節を拾ってから closingRef で読む（並べた指定のそれぞれを取るため）。
	closingClause = map[string]*pyre.Regexp{
		"github": clause(`close[sd]?|fix(?:e[sd])?|resolve[sd]?`, ""),
		"gitlab": clause(`clos(?:e[sd]?|ing)|fix(?:e[sd]|ing)?|resolv(?:e[sd]?|ing)|implement(?:s|ed|ing)?`,
			`(?:(?:[ \t]*,?[ \t]+and[ \t]+|[ \t]*,?[ \t]*)(?:issues?[ \t]+)?`+refBare+`)*`),
	}
)

// refBare は closingRef と同じ形の、名前付きのグループを持たない指定 1 つ（節の式に埋める）。
const refBare = `(?:https?://[^\s/]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+?/(?:-/)?issues/[0-9]+` +
	`|(?:[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)?#[0-9]+)(?![A-Za-z0-9_])`

// clause は、語の並び words と、最初の指定のあとに続けてよい形 more から節の式を組む。GitHub は `issue` を挟めず
// 指定は 1 つ（more は空）。
func clause(words, more string) *pyre.Regexp {
	lead := ""
	if more != "" {
		lead = `(?:issues?[ \t]+)?`
	}
	return pyre.MustCompile(`(?<![A-Za-z0-9_-])(?:`+words+`):?[ \t]+`+lead+refBare+more, pyre.IgnoreCase)
}

// ClosesHost は、閉じる語の読み方があるホストの種類か（github・gitlab）。
func ClosesHost(kind string) bool { return closingClause[kind] != nil }

// ClosingRefs は、ホストの種類 kind（github・gitlab。ClosesHost で確かめる）の書き方で本文が閉じる issue を
// `<owner/repo>#<番号>` で返す（出てきた順）。番号だけの指定には repo を前に付ける。
func ClosingRefs(kind, repo, body string) []string {
	drop := func(pyre.Found) string { return "" }
	for _, re := range []*pyre.Regexp{bodyFence, bodyComment, bodyCode, bodyQuote} {
		body = re.Sub(body, drop)
	}
	var out []string
	for _, c := range closingClause[kind].FindAll(body) {
		for _, f := range closingRef.FindAll(c.Text()) {
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
	}
	return out
}

// ClosesIssue は、ホストの種類 kind の書き方で本文が repo の issue 番号 issue を閉じるか（リポジトリの名前は大文字小文字を
// 区別しない）。
func ClosesIssue(kind, repo string, issue int, body string) bool {
	want := pathcase.Fold(repo + "#" + strconv.Itoa(issue))
	for _, ref := range ClosingRefs(kind, repo, body) {
		if pathcase.Fold(ref) == want {
			return true
		}
	}
	return false
}
