package start

import (
	"fmt"
	"math"
	"slices"
	"sort"
	"strings"
	"unicode"

	"ccnavi/internal/tickets/model/hostreply"
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
	v, err := hostreply.Decode(data)
	if err != nil {
		return "", unreadable
	}
	if kind == "github" {
		pr, err := hostreply.Field(v, "pull_request")
		if err != nil {
			return "", unreadable
		}
		if hostreply.Truthy(pr) {
			return "pr\n", ok
		}
	}
	t, err := hostreply.Field(v, "title")
	if err != nil {
		return "", unreadable
	}
	title := ""
	if hostreply.Truthy(t) {
		title = issueTitle(hostreply.Text(t))
	}
	s, _ := hostreply.Field(v, "state")
	state := "unknown"
	switch hostreply.Text(s) {
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
	v, err := hostreply.Decode(data)
	if err != nil {
		return "", unreadable
	}
	refs, err := hostreply.Path(v, "data", "repository", "pullRequest", "closingIssuesReferences")
	if err != nil {
		return "bad\n", ok
	}
	errs, err := hostreply.Field(v, "errors")
	nodes, nerr := hostreply.Field(refs, "nodes")
	list, isList := nodes.([]any)
	if err != nil || nerr != nil || hostreply.Truthy(errs) || !isList {
		return "bad\n", ok
	}
	var b strings.Builder
	for _, n := range list {
		repo, err := hostreply.Path(n, "repository", "nameWithOwner")
		num, nerr := hostreply.Field(n, "number")
		if err != nil || nerr != nil {
			return "", unreadable
		}
		fmt.Fprintf(&b, "issue %s#%s\n", hostreply.Text(repo), hostreply.Text(num))
	}
	more, _ := hostreply.Path(refs, "pageInfo", "hasNextPage")
	if more == true {
		cursor, _ := hostreply.Path(refs, "pageInfo", "endCursor")
		fmt.Fprintf(&b, "next %s\n", hostreply.JSONText(cursor))
	} else {
		b.WriteString("done\n")
	}
	return b.String(), ok
}

// LinkedBody は、GitHub の PR の応答の本文から、閉じる issue を `issue <owner/repo>#<番号>` で 1 件 1 行に出す
// （読み方は hostreply.ClosingRefs の GitHub の書き方）。GraphQL を断られたときの代わりの読み方で、GitHub だけが使う
// （GitLab は closes_issues の API を読む。LinkedGitLab）。
func LinkedBody(repo string, data []byte) (string, int) {
	v, err := hostreply.Decode(data)
	if err != nil {
		return "", unreadable
	}
	raw, err := hostreply.Field(v, "body")
	if err != nil {
		return "", unreadable
	}
	body, err := hostreply.Str(raw)
	if err != nil {
		return "", unreadable
	}
	var b strings.Builder
	for _, ref := range hostreply.ClosingRefs("github", repo, body) {
		fmt.Fprintf(&b, "issue %s\n", ref)
	}
	return b.String(), ok
}

// LinkedGitLab は、GitLab の closes_issues の応答から、閉じる issue を `issue <参照>` で 1 件 1 行に出す。
// 参照は references.full、無ければ `<repo>#<iid>`。
func LinkedGitLab(repo string, data []byte) (string, int) {
	v, err := hostreply.Decode(data)
	if err != nil {
		return "", unreadable
	}
	items, err := hostreply.List(v)
	if err != nil {
		return "", unreadable
	}
	var b strings.Builder
	for _, it := range items {
		full, err := hostreply.Path(it, "references", "full")
		iid, ierr := hostreply.Field(it, "iid")
		if err != nil || ierr != nil {
			return "", unreadable
		}
		if hostreply.Truthy(full) {
			fmt.Fprintf(&b, "issue %s\n", hostreply.Text(full))
		} else {
			fmt.Fprintf(&b, "issue %s#%s\n", repo, hostreply.Text(iid))
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
			return nil, hostreply.ErrShape
		}
		sources = []source{{inputs[0], "issue", "number", "html_url", "MR"}}
	} else {
		if len(inputs) != 2 {
			return nil, hostreply.ErrShape
		}
		sources = []source{{inputs[0], "issue", "iid", "web_url", ""}, {inputs[1], "MR", "iid", "web_url", ""}}
	}
	var items []item
	for _, s := range sources {
		v, err := hostreply.Decode(s.data)
		if err != nil {
			return nil, err
		}
		list, err := hostreply.List(v)
		if err != nil {
			return nil, err
		}
		for _, x := range list {
			k := s.kind
			if s.prKind != "" {
				pr, err := hostreply.Field(x, "pull_request")
				if err != nil {
					return nil, err
				}
				if hostreply.Truthy(pr) {
					k = s.prKind
				}
			}
			t, err := hostreply.Field(x, "title")
			if err != nil {
				return nil, err
			}
			title, err := hostreply.CleanField(t, 200)
			if err != nil {
				return nil, err
			}
			num, _ := hostreply.Field(x, s.num)
			url, _ := hostreply.Field(x, s.url)
			items = append(items, item{kind: k, number: hostreply.Text(num), title: title, url: hostreply.Text(url)})
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
		v, err := hostreply.Decode(data)
		if err != nil {
			return nil, err
		}
		l, err := hostreply.List(v)
		if err != nil || !dropPR {
			return l, err
		}
		var kept []any
		for _, x := range l {
			pr, err := hostreply.Field(x, "pull_request")
			if err != nil {
				return nil, err
			}
			if !hostreply.Truthy(pr) {
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
		n, _ := hostreply.Field(x, num)
		t, err := hostreply.Field(x, "title")
		if err != nil {
			return "", err
		}
		title, err := hostreply.CleanField(t, 200)
		if err != nil {
			return "", err
		}
		bv, _ := hostreply.Field(x, body)
		bodyText, err := hostreply.CleanField(bv, 500)
		if err != nil {
			return "", err
		}
		if !isMR {
			return fmt.Sprintf("issue #%s  %s\n  本文: %s\n", hostreply.Text(n), title, bodyText), nil
		}
		br, err := hostreply.Path(x, branch...)
		if err != nil {
			return "", err
		}
		brText, err := hostreply.Str(br)
		if err != nil {
			return "", err
		}
		return fmt.Sprintf("MR !%s  %s  ブランチ: %s\n  本文: %s\n", hostreply.Text(n), title, hostreply.Clean(brText), bodyText), nil
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
//   - open-mr: 開いた MR の一覧から hostreply.OpenMR が選ぶもの（GitLab はフォークから出た MR を除く）
//   - merged-mr: マージ済みの MR の一覧から hostreply.MergedMR が選ぶもの（pid を渡さないので、GitLab は一覧の先頭）
//   - made: MR か issue を作った答え。番号が無ければ作れなかったとして unreadable
func Pick(what, kind, pid string, data []byte) (string, int) {
	v, err := hostreply.Decode(data)
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
		n, err := hostreply.Field(v, num)
		if err != nil || n == nil {
			return "", unreadable
		}
		chosen = v
	case "open-mr", "merged-mr":
		l, err := hostreply.List(v)
		if err != nil {
			return "", unreadable
		}
		pickOne := hostreply.OpenMR
		if what == "merged-mr" {
			pickOne = hostreply.MergedMR
		}
		if chosen, err = pickOne(kind, pid, l); err != nil {
			return "", unreadable
		}
		if !hostreply.Truthy(chosen) {
			return "", ok
		}
	default:
		return "", unreadable
	}
	n, _ := hostreply.Field(chosen, num)
	u, _ := hostreply.Field(chosen, url)
	return fmt.Sprintf("number %s\nurl %s\n", hostreply.Text(n), hostreply.Text(u)), ok
}
