package start

import (
	"fmt"
	"math"
	"slices"
	"sort"
	"strings"
	"unicode"

	"ccnavi/internal/infra/pyre"
)

// 終了コード。unreadable は、応答が JSON でないか、読み方の前提の形でないこと。
const (
	ok         = 0
	unreadable = 1
)

// Issue は issue の 1 件の応答を読む。kind は github か gitlab。
//
// GitHub で PR の番号なら `pr` の 1 行だけ。それ以外は `title <タイトル>`（1 行目だけ。制御文字は空白にして前後の空白を
// 落とす。無ければ空）と `state open|closed|unknown`。
func Issue(kind string, data []byte) (string, int) {
	v, err := decode(data)
	if err != nil {
		return "", unreadable
	}
	if kind == "github" {
		pr, err := field(v, "pull_request")
		if err != nil {
			return "", unreadable
		}
		if truthy(pr) {
			return "pr\n", ok
		}
	}
	t, err := field(v, "title")
	if err != nil {
		return "", unreadable
	}
	title := ""
	if truthy(t) {
		title = issueTitle(text(t))
	}
	s, _ := field(v, "state")
	state := "unknown"
	switch text(s) {
	case "open", "opened":
		state = "open"
	case "closed":
		state = "closed"
	}
	return fmt.Sprintf("title %s\nstate %s\n", title, state), ok
}

// issueTitle は 1 行目を取り、ASCII の制御文字を空白にして前後の空白を落とす。
func issueTitle(raw string) string {
	line, _, _ := strings.Cut(strings.TrimRight(raw, "\n"), "\n")
	b := []byte(line)
	for i, c := range b {
		if (c >= 0x01 && c <= 0x1f) || c == 0x7f {
			b[i] = ' '
		}
	}
	return strings.Trim(string(b), " ")
}

// LinkedPage は、GitHub の GraphQL が返す closingIssuesReferences の 1 ページを読む。
//
// errors があるか、PR が見つからず nodes が列でなければ `bad` の 1 行。読めれば `issue <owner/repo>#<番号>` を 1 件 1 行で
// 出し、最後に `next <endCursor の JSON>`（次のページがある）か `done`。sh は next の値を次の問い合わせにそのまま渡す。
func LinkedPage(data []byte) (string, int) {
	v, err := decode(data)
	if err != nil {
		return "", unreadable
	}
	refs, err := path(v, "data", "repository", "pullRequest", "closingIssuesReferences")
	if err != nil {
		return "bad\n", ok
	}
	errs, err := field(v, "errors")
	nodes, nerr := field(refs, "nodes")
	list, isList := nodes.([]any)
	if err != nil || nerr != nil || truthy(errs) || !isList {
		return "bad\n", ok
	}
	var b strings.Builder
	for _, n := range list {
		repo, err := path(n, "repository", "nameWithOwner")
		num, nerr := field(n, "number")
		if err != nil || nerr != nil {
			return "", unreadable
		}
		fmt.Fprintf(&b, "issue %s#%s\n", text(repo), text(num))
	}
	more, _ := path(refs, "pageInfo", "hasNextPage")
	if more == true {
		cursor, _ := path(refs, "pageInfo", "endCursor")
		fmt.Fprintf(&b, "next %s\n", jsonText(cursor))
	} else {
		b.WriteString("done\n")
	}
	return b.String(), ok
}

// 本文の Closes の読み方。コードブロック・HTML の注釈・インラインのコード・引用の行を除いてから、閉じるキーワードの
// 後ろの参照を拾う。語の一部（`precloses`）と `not closes` は拾わない。
var (
	bodyFence   = pyre.MustCompile("```.*?```", pyre.DotAll)
	bodyComment = pyre.MustCompile("<!--.*?-->", pyre.DotAll)
	bodyCode    = pyre.MustCompile("`[^`\n]*`", 0)
	bodyQuote   = pyre.MustCompile(`^[ \t]*>[^\n]*`, pyre.Multiline)
	bodyClosing = pyre.MustCompile(`(?<![A-Za-z0-9_-])(?<!not )(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?[ \t]+`+
		`(?P<ref>(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#[0-9]+)(?![A-Za-z0-9_])`, pyre.IgnoreCase)
)

// LinkedBody は、GitHub の PR の応答の本文から、閉じる issue を `issue <owner/repo>#<番号>` で 1 件 1 行に出す。
// 番号だけの参照には repo（owner/repo）を前に付ける。GraphQL を断られたときの代わりの読み方。
func LinkedBody(repo string, data []byte) (string, int) {
	v, err := decode(data)
	if err != nil {
		return "", unreadable
	}
	raw, err := field(v, "body")
	if err != nil {
		return "", unreadable
	}
	body, err := str(raw)
	if err != nil {
		return "", unreadable
	}
	drop := func(pyre.Found) string { return "" }
	for _, re := range []*pyre.Regexp{bodyFence, bodyComment, bodyCode, bodyQuote} {
		body = re.Sub(body, drop)
	}
	var b strings.Builder
	for _, f := range bodyClosing.FindAll(body) {
		ref, _ := f.Named("ref")
		if strings.HasPrefix(ref, "#") {
			ref = repo + ref
		}
		fmt.Fprintf(&b, "issue %s\n", ref)
	}
	return b.String(), ok
}

// LinkedGitLab は、GitLab の closes_issues の応答から、閉じる issue を `issue <参照>` で 1 件 1 行に出す。
// 参照は references.full、無ければ `<repo>#<iid>`。
func LinkedGitLab(repo string, data []byte) (string, int) {
	v, err := decode(data)
	if err != nil {
		return "", unreadable
	}
	items, err := list(v)
	if err != nil {
		return "", unreadable
	}
	var b strings.Builder
	for _, it := range items {
		full, err := path(it, "references", "full")
		iid, ierr := field(it, "iid")
		if err != nil || ierr != nil {
			return "", unreadable
		}
		if truthy(full) {
			fmt.Fprintf(&b, "issue %s\n", text(full))
		} else {
			fmt.Fprintf(&b, "issue %s#%s\n", repo, text(iid))
		}
	}
	return b.String(), ok
}

// item は似たものを探す一覧の 1 件。
type item struct {
	kind, number, title, url string
	score                    float64
}

// openItems は GitHub の issue の一覧（PR を含む）か、GitLab の issue と MR の一覧を、1 つの列にする。
func openItems(kind string, inputs [][]byte) ([]item, error) {
	type source struct {
		data                   []byte
		kind, num, url, prKind string
	}
	var sources []source
	if kind == "github" {
		if len(inputs) != 1 {
			return nil, errShape
		}
		sources = []source{{inputs[0], "issue", "number", "html_url", "MR"}}
	} else {
		if len(inputs) != 2 {
			return nil, errShape
		}
		sources = []source{{inputs[0], "issue", "iid", "web_url", ""}, {inputs[1], "MR", "iid", "web_url", ""}}
	}
	var items []item
	for _, s := range sources {
		v, err := decode(s.data)
		if err != nil {
			return nil, err
		}
		list, err := list(v)
		if err != nil {
			return nil, err
		}
		for _, x := range list {
			k := s.kind
			if s.prKind != "" {
				pr, err := field(x, "pull_request")
				if err != nil {
					return nil, err
				}
				if truthy(pr) {
					k = s.prKind
				}
			}
			t, err := field(x, "title")
			if err != nil {
				return nil, err
			}
			title, err := cleanField(t, 200)
			if err != nil {
				return nil, err
			}
			num, _ := field(x, s.num)
			url, _ := field(x, s.url)
			items = append(items, item{kind: k, number: text(num), title: title, url: text(url)})
		}
	}
	return items, nil
}

// bigrams はタイトルの 2 文字組（英字は小文字、空白は除く）。2 文字に満たなければ、その文字列そのもの 1 つ。
func bigrams(s string) []string {
	var rs []rune
	for _, r := range s {
		if unicode.IsSpace(r) {
			continue
		}
		if r >= 'A' && r <= 'Z' {
			r += 'a' - 'A'
		}
		rs = append(rs, r)
	}
	if len(rs) < 2 {
		return []string{string(rs)}
	}
	var out []string
	for i := range len(rs) - 1 {
		out = append(out, string(rs[i:i+2]))
	}
	slices.Sort(out)
	return slices.Compact(out)
}

// dice は 2 文字組の一致率（Dice 係数）。
func dice(a, b string) float64 {
	x, y := bigrams(a), bigrams(b)
	if len(x) == 0 || len(y) == 0 {
		return 0
	}
	hit := 0
	for _, k := range x {
		if slices.Contains(y, k) {
			hit++
		}
	}
	return float64(hit*2) / float64(len(x)+len(y))
}

// Similar は、タイトルが title に似た open の issue・MR（一致率 0.5 以上、上位 5 件。mode が checked なら一致率 1 の
// 同じタイトルだけ）を、1 件 1 行で出す。無ければ何も出さない。
// GitHub は issue の一覧（PR を含む）、GitLab は issue の一覧と MR の一覧を渡す。
func Similar(kind, mode, title string, inputs [][]byte) (string, int) {
	items, err := openItems(kind, inputs)
	if err != nil {
		return "", unreadable
	}
	var found []item
	for _, it := range items {
		it.score = dice(title, it.title)
		if it.score >= 0.5 {
			found = append(found, it)
		}
	}
	sort.SliceStable(found, func(i, j int) bool { return found[i].score > found[j].score })
	found = found[:min(len(found), 5)]
	var b strings.Builder
	for _, it := range found {
		if mode == "checked" && it.score < 1 {
			continue
		}
		mark := "#"
		if it.kind == "MR" {
			mark = "!"
		}
		fmt.Fprintf(&b, "  %s %s%s  %s  %s  類似度 %d%%\n", it.kind, mark, it.number, it.title, it.url, int(math.Floor(it.score*100)))
	}
	return b.String(), ok
}

// listLimit は一覧に出す件数の上限。
const listLimit = 50

// ListOpen は open の issue と MR の一覧を出す（それぞれ先頭 50 件。タイトルは 200 字、本文は 500 字まで）。
// GitHub の issue の一覧に混ざる PR は除く。
func ListOpen(kind string, issuesData, mrsData []byte) (string, int) {
	num, branch, body := "number", []string{"head", "ref"}, "body"
	if kind != "github" {
		num, branch, body = "iid", []string{"source_branch"}, "description"
	}
	read := func(data []byte, dropPR bool) ([]any, error) {
		v, err := decode(data)
		if err != nil {
			return nil, err
		}
		l, err := list(v)
		if err != nil || !dropPR {
			return l, err
		}
		var kept []any
		for _, x := range l {
			pr, err := field(x, "pull_request")
			if err != nil {
				return nil, err
			}
			if !truthy(pr) {
				kept = append(kept, x)
			}
		}
		return kept, nil
	}
	issues, err := read(issuesData, kind == "github")
	if err != nil {
		return "", unreadable
	}
	mrs, err := read(mrsData, false)
	if err != nil {
		return "", unreadable
	}
	line := func(x any, isMR bool) (string, error) {
		n, _ := field(x, num)
		t, err := field(x, "title")
		if err != nil {
			return "", err
		}
		title, err := cleanField(t, 200)
		if err != nil {
			return "", err
		}
		bv, _ := field(x, body)
		bodyText, err := cleanField(bv, 500)
		if err != nil {
			return "", err
		}
		if !isMR {
			return fmt.Sprintf("issue #%s  %s\n  本文: %s\n", text(n), title, bodyText), nil
		}
		br, err := path(x, branch...)
		if err != nil {
			return "", err
		}
		brText, err := str(br)
		if err != nil {
			return "", err
		}
		return fmt.Sprintf("MR !%s  %s  ブランチ: %s\n  本文: %s\n", text(n), title, clean(brText), bodyText), nil
	}
	var b strings.Builder
	for _, part := range []struct {
		name, unit string
		items      []any
		isMR       bool
	}{{"issue", "issue", issues, false}, {"MR", "MR", mrs, true}} {
		fmt.Fprintf(&b, "open の %s（全 %d 件。先頭 50 件を出す）:\n", part.name, len(part.items))
		for _, x := range part.items[:min(len(part.items), listLimit)] {
			s, err := line(x, part.isMR)
			if err != nil {
				return "", unreadable
			}
			b.WriteString(s)
		}
		if len(part.items) > listLimit {
			fmt.Fprintf(&b, "（ほかに %d 件あります。一覧に無い古い %s は、この出力だけでは確かめられません）\n",
				len(part.items)-listLimit, part.unit)
		}
	}
	return b.String(), ok
}

// Pick は、MR・issue の応答から 1 件を選び、`number <番号>` と `url <URL>` の 2 行で出す。選ぶものが無ければ何も出さない。
//
//   - open-mr: 開いた MR の一覧の先頭。GitLab はフォークから出た MR を除く（source_project_id が pid のものだけ）
//   - merged-mr: マージ済みの MR。GitHub は閉じた PR の一覧のうち merged_at のあるもの、GitLab はマージ済みの一覧の先頭
//   - made: MR か issue を作った答え。番号が無ければ作れなかったとして unreadable
func Pick(what, kind, pid string, data []byte) (string, int) {
	v, err := decode(data)
	if err != nil {
		return "", unreadable
	}
	num, url := "number", "html_url"
	if kind != "github" {
		num, url = "iid", "web_url"
	}
	var chosen any
	switch what {
	case "made":
		n, err := field(v, num)
		if err != nil || n == nil {
			return "", unreadable
		}
		chosen = v
	case "open-mr", "merged-mr":
		l, err := list(v)
		if err != nil {
			return "", unreadable
		}
		for _, x := range l {
			if keep, err := picks(what, kind, pid, x); err != nil {
				return "", unreadable
			} else if keep {
				chosen = x
				break
			}
		}
		if !truthy(chosen) {
			return "", ok
		}
	default:
		return "", unreadable
	}
	n, _ := field(chosen, num)
	u, _ := field(chosen, url)
	return fmt.Sprintf("number %s\nurl %s\n", text(n), text(u)), ok
}

// picks は、一覧の x が選ぶ 1 件に当たるか。条件の無い一覧は先頭の 1 件を選ぶ。
func picks(what, kind, pid string, x any) (bool, error) {
	switch {
	case what == "open-mr" && kind != "github":
		src, err := field(x, "source_project_id")
		if err != nil {
			return false, err
		}
		got, isNum := number(src)
		want, wantNum := number(jsonNumber(pid))
		return isNum && wantNum && got == want, nil
	case what == "merged-mr" && kind == "github":
		at, err := field(x, "merged_at")
		return err == nil && at != nil, err
	}
	return true, nil
}
