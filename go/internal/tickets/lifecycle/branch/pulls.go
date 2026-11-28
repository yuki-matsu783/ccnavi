package branch

import (
	"fmt"
	"math/big"
	"os"
	"regexp"
	"strconv"

	"ccnavi/internal/hostreply"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// Source は sh が渡すホストの結果（`branches <種類> <番号> [<渡し方> <引数>...] [--result <ファイル>]`）。
//
//   - checked <github|gitlab> <ホスト> <リポジトリ> <プロジェクトの id か ->: --result はホストの応答のまま
//     （MR は MR 1 本、issue は GitHub なら開いた PR の一覧、GitLab なら related_merge_requests の一覧）
//   - unchecked <理由>: ホストを見ていない
//   - failed <呼び出し>: API が失敗した。--result はホストの返事（失敗の文）
//
// 渡し方が無ければ、ホストの結果が渡されていないとして手元の候補だけを出す。
type Source struct {
	How    string
	Args   []string
	Result string
}

const (
	howChecked   = "checked"
	howUnchecked = "unchecked"
	howFailed    = "failed"
)

// SourceArgs は渡し方ごとの引数の数。
var SourceArgs = map[string]int{howChecked: 4, howUnchecked: 1, howFailed: 1}

// readHost は sh が渡したホストの結果を読む。無い・読めない・形が違うなら「見ていない」として理由を付ける。
func readHost(from Source, kind Kind, n int, prefixes []string) host {
	switch from.How {
	case howUnchecked:
		return unchecked(from.Args[0])
	case howFailed:
		return unchecked(apiFailed(from.Args[0], from.Result))
	case howChecked:
	default:
		return host{reason: "ホストの結果が渡されていない（--result がありません）"}
	}
	hostKind, name, repo, pid := from.Args[0], from.Args[1], from.Args[2], from.Args[3]
	value, err := fsio.ReadJSON(from.Result)
	if from.Result == "" || err != nil {
		return host{reason: "ホストの結果を読めない"}
	}
	list, ok := hostMRs(hostKind, kind, n, repo, pid, value, prefixes)
	if !ok {
		return host{reason: "ホストの結果を読めない"}
	}
	var mrs []*pyjson.Object
	for _, raw := range list {
		if m, ok := mrOf(raw); ok {
			mrs = append(mrs, m)
		}
	}
	return host{checked: true, name: clean(name, cleanLimit), repo: clean(repo, cleanLimit), mrs: mrs}
}

func unchecked(reason string) host {
	reason = clean(reason, cleanLimit)
	if reason == "" {
		reason = "理由は分からない"
	}
	return host{reason: reason}
}

// apiFailed は API が失敗したときの理由。ホストが HTTP のエラーを返したなら、その番号と本文（先頭 200 文字、1 行。
// トークンは伏せる）も言う。
func apiFailed(what, failure string) string {
	if what == "" {
		what = "?"
	}
	data, _ := os.ReadFile(failure)
	status := hostreply.FailStatus(string(data))
	if status == "" {
		return fmt.Sprintf("ホストの API が失敗した（%s）。番号が無いか、権限が足りないか、ホストに届かない", what)
	}
	body := hostreply.FailBody(string(data), []string{os.Getenv(env.GitHubToken), os.Getenv(env.GitLabToken)})
	if body == "" {
		body = "（本文なし）"
	}
	return fmt.Sprintf("ホストの API が失敗した（%s）。HTTP %s: %s", what, status, body)
}

// mrOf は mrs の 1 件を出力の形に整える。番号が整数でないか、元ブランチが無ければ使わない。
func mrOf(raw any) (*pyjson.Object, bool) {
	m, ok := raw.(*pyjson.Object)
	if !ok {
		return nil, false
	}
	number := m.Value("number")
	switch number.(type) {
	case int, *big.Int:
	default:
		return nil, false
	}
	branch := clean(m.Value("branch"), cleanBranchLimit)
	if branch == "" {
		return nil, false
	}
	fork, _ := m.Value("fork").(bool)
	return pyjson.NewObject(
		"number", number,
		"branch", branch,
		"state", clean(m.Value("state"), cleanStateLimit),
		"url", clean(m.Value("url"), cleanURLLimit),
		"title", clean(m.Value("title"), cleanLimit),
		"base", clean(m.Value("base"), cleanBranchLimit),
		"fork", fork,
	), true
}

// hostMRs はホストの応答を mrs の形（number・branch・state・url・title・base・fork）の列にする。
// GitHub の MR はマージ済みなら state を merged にし、元ブランチのリポジトリが repo と違えばフォーク。
// GitLab はこのプロジェクト（pid）から出ていなければフォークで、issue の一覧は開いた（opened）ものだけ。
func hostMRs(hostKind string, kind Kind, n int, repo, pid string, value any, prefixes []string) ([]any, bool) {
	switch {
	case hostKind == "github" && kind == issue:
		list, ok := value.([]any)
		return relatedPulls(list, n, repo, prefixes), ok
	case hostKind == "github":
		pr, ok := value.(*pyjson.Object)
		if !ok {
			return nil, false
		}
		state := pr.Value("state")
		if pyfmt.Truthy(pr.Value("merged_at")) {
			state = "merged"
		}
		return []any{pullOf(pr, state, repo)}, true
	case kind == issue:
		list, ok := value.([]any)
		out := []any{}
		for _, raw := range list {
			if m, isMap := raw.(*pyjson.Object); isMap && m.Value("state") == "opened" {
				out = append(out, gitlabMR(m, pid))
			}
		}
		return out, ok
	}
	m, ok := value.(*pyjson.Object)
	if !ok {
		return nil, false
	}
	return []any{gitlabMR(m, pid)}, true
}

// gitlabMR は GitLab の MR 1 本を mrs の形にする。
func gitlabMR(m *pyjson.Object, pid string) *pyjson.Object {
	src, isInt := m.Value("source_project_id").(int)
	want, err := strconv.Atoi(pid)
	return pyjson.NewObject(
		"number", m.Value("iid"),
		"branch", m.Value("source_branch"),
		"state", m.Value("state"),
		"url", m.Value("web_url"),
		"title", m.Value("title"),
		"base", text(m, "target_branch"),
		"fork", !isInt || err != nil || src != want,
	)
}

// relatedPulls は、開いた PR の一覧（ページを繋いだ配列）のうち issue n に関係するものを、mrs の形にして返す。
// 関係するのは、タイトルか本文に `#<番号>` か `issues/<番号>` を含む PR と、元ブランチ名が番号を含む PR（namesNumber）。
func relatedPulls(list []any, n int, repo string, prefixes []string) []any {
	num := strconv.Itoa(n)
	// 本文の `#12` は、英数字・`_`・`&`・`/` の直後のもの（`a#12`・`&#12`・`x/#12`）を参照と読まない。
	hash := regexp.MustCompile(`(^|[^0-9A-Za-z_&/])#` + num + `([^0-9]|$)`)
	link := regexp.MustCompile(`issues/` + num + `([^0-9]|$)`)
	out := []any{}
	for _, raw := range list {
		pr, ok := raw.(*pyjson.Object)
		if !ok {
			continue
		}
		head, _ := pr.Value("head").(*pyjson.Object)
		said := text(pr, "title") + " " + text(pr, "body")
		if !hash.MatchString(said) && !link.MatchString(said) && !namesNumber(text(head, "ref"), n, prefixes) {
			continue
		}
		out = append(out, pullOf(pr, pr.Value("state"), repo))
	}
	return out
}

// pullOf は GitHub の PR 1 本を mrs の形にする。フォークかは、元ブランチのリポジトリ（head.repo.full_name）が repo と
// 違うかで決める（大文字小文字は区別しない）。
func pullOf(pr *pyjson.Object, state any, repo string) *pyjson.Object {
	head, _ := pr.Value("head").(*pyjson.Object)
	base, _ := pr.Value("base").(*pyjson.Object)
	var from string
	if head != nil {
		fromRepo, _ := head.Value("repo").(*pyjson.Object)
		from = text(fromRepo, "full_name")
	}
	return pyjson.NewObject(
		"number", pr.Value("number"),
		"branch", text(head, "ref"),
		"state", state,
		"url", pr.Value("html_url"),
		"title", pr.Value("title"),
		"base", text(base, "ref"),
		"fork", pyfmt.Casefold(from) != pyfmt.Casefold(repo),
	)
}

// text は o の key の文字列。o が無い・文字列でなければ空。
func text(o *pyjson.Object, key string) string {
	if o == nil {
		return ""
	}
	s, _ := o.Value(key).(string)
	return s
}
