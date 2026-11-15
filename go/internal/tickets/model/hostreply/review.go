package hostreply

import (
	"bytes"
	"encoding/json"
	"errors"
	"regexp"
	"strings"
)

// レビューの結果（`ccnavi-review.sh fetch` の JSON。実行ファイルの `--result` で host.ResultOf が読む形）を、
// sh か Chrome 拡張の service worker（src/core/github.ts・gitlab.ts の reviewReplies）が取ってきたホストの応答から組む
// （拡張は WASM の copy から呼ぶ）。同じ見本（extensions/chrome/ccnavi-approval/test/fixtures/host/）から sh と拡張が同じ
// 結果になることを試験が見る。
// 欄の省き方は jq の `//`・`tostring` と同じで、型はそのまま残す（文字列の行番号を数に直さない）。

// ErrNotArrays は、GraphQL を使えない環境の REST の道で、スレッドかコメントの一覧の応答が配列でないこと。
// sh は REST の代わりの道も失敗したと言う。
var ErrNotArrays = errors.New("ccr/review_threads の返事か、コメント一覧の返事が配列でない")

// marker は ccnavi の投稿の本文の頭（host の markerPrefix と同じ）。
const marker = "<!-- ccnavi:"

// Copy は取ってきた時点の状態。
type Copy struct {
	Host      string `json:"host"`
	MR        copyMR `json:"mr"`
	Threads   []any  `json:"threads"`
	Reviews   []any  `json:"reviews"`
	FetchedAt string `json:"fetched_at"`
}

type copyMR struct {
	Number json.Number `json:"number"`
	URL    string      `json:"url"`
	// Draft は ready だけが足す（打ち直しの判断に使う）。Chrome が組む結果と形を揃えるため、ほかでは出さない。
	Draft *bool `json:"draft,omitempty"`
}

// ThreadsForm はスレッドの応答の形。
type ThreadsForm string

const (
	// FormGraphQL は GitHub の GraphQL の reviewThreads のページ（1 ページ 1 ファイル）。
	FormGraphQL ThreadsForm = "graphql"
	// FormREST は GraphQL を使えない環境の REST（ccr/review_threads とコメントの一覧の 2 つ）。
	FormREST ThreadsForm = "rest"
	// FormDiscussions は GitLab の discussions（全ページを繋いだ配列）。
	FormDiscussions ThreadsForm = "discussions"
)

// NewCopy は MR とスレッド・レビューの応答から、取ってきた時点の状態を組む。number は MR の番号（数の綴り）、
// draft は ready のときだけ渡す（それ以外は nil）。
func NewCopy(kind, number, url string, draft *bool, form ThreadsForm, threads [][]byte, reviews []byte, when string) (Copy, error) {
	ts, err := Threads(form, url, threads)
	if err != nil {
		return Copy{}, err
	}
	rs, err := Reviews(kind, url, reviews)
	if err != nil {
		return Copy{}, err
	}
	return Copy{Host: kind, MR: copyMR{json.Number(number), url, draft}, Threads: ts, Reviews: rs, FetchedAt: when}, nil
}

// JSON は Copy を 1 行の JSON にする（ASCII でない文字も HTML の字もそのまま）。
func (c Copy) JSON() string {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(c)
	return strings.TrimSuffix(b.String(), "\n")
}

// Threads はスレッドの応答を [{id, resolved, url, path, line, body, created_at, last_id, last_at, last_author}] にする
// （GitLab は author と own も）。id は GraphQL ならスレッドの node id、REST ならスレッドの先頭コメントの id（REST には
// スレッド自体の id が無い）、GitLab は discussion の id。受け入れ（返信と解決）は id の形で道を選ぶ。
func Threads(form ThreadsForm, mrURL string, pages [][]byte) ([]any, error) {
	switch form {
	case FormGraphQL:
		out := []any{}
		for _, p := range pages {
			got, err := graphQLThreads(p)
			if err != nil {
				return nil, err
			}
			out = append(out, got...)
		}
		return out, nil
	case FormREST:
		if len(pages) != 2 {
			return nil, ErrShape
		}
		return restThreads(pages[0], pages[1])
	case FormDiscussions:
		if len(pages) != 1 {
			return nil, ErrShape
		}
		return discussions(pages[0], mrURL)
	}
	return nil, ErrShape
}

type githubThread struct {
	ID         any `json:"id"`
	Resolved   any `json:"resolved"`
	URL        any `json:"url"`
	Path       any `json:"path"`
	Line       any `json:"line"`
	Body       any `json:"body"`
	CreatedAt  any `json:"created_at"`
	LastID     any `json:"last_id"`
	LastAt     any `json:"last_at"`
	LastAuthor any `json:"last_author"`
}

// at は v の path（無ければ fallback。jq の `(.a.b // fallback)`）。
func at(v any, fallback any, path ...string) (any, error) {
	got, err := Path(v, path...)
	if err != nil {
		return nil, err
	}
	return Alt(got, fallback), nil
}

// picker は at を順に当てる。最初の誤りを覚え、それより後は読まない。
type picker struct{ err error }

func (p *picker) at(v any, fallback any, path ...string) any {
	if p.err != nil {
		return nil
	}
	got, err := at(v, fallback, path...)
	p.err = err
	return got
}

// raw は v の name をそのまま（jq の `$t.id`。false も null に替えない）。
func (p *picker) raw(v any, name string) any {
	if p.err != nil {
		return nil
	}
	got, err := Field(v, name)
	p.err = err
	return got
}

// first は列 v の先頭（無ければ nil。jq の `.[0]`）。
func first(v any) (any, error) {
	l, err := List(v)
	if err != nil || len(l) == 0 {
		return nil, err
	}
	return l[0], nil
}

// graphQLThreads は reviewThreads の 1 ページを読む。
func graphQLThreads(page []byte) ([]any, error) {
	v, err := Decode(page)
	if err != nil {
		return nil, err
	}
	nodes, err := Path(v, "data", "repository", "pullRequest", "reviewThreads", "nodes")
	if err != nil {
		return nil, err
	}
	all, err := List(nodes)
	if err != nil {
		return nil, err
	}
	out := []any{}
	for _, t := range all {
		var p picker
		cs := p.at(t, nil, "comments", "nodes")
		ls := p.at(t, nil, "last", "nodes")
		if p.err != nil {
			return nil, p.err
		}
		c, err := first(cs)
		if err != nil {
			return nil, err
		}
		l, err := first(ls)
		if err != nil {
			return nil, err
		}
		c, l = Alt(c, map[string]any{}), Alt(l, map[string]any{})
		th := githubThread{
			ID: p.raw(t, "id"), Resolved: p.raw(t, "isResolved"),
			URL: p.at(c, "", "url"), Path: p.at(c, "", "path"), Line: p.at(c, json.Number("0"), "line"),
			Body: p.at(c, "", "body"), CreatedAt: p.at(c, "", "createdAt"),
			LastID: p.at(l, "", "id"), LastAt: p.at(l, "", "createdAt"), LastAuthor: p.at(l, "", "author", "login"),
		}
		if p.err != nil {
			return nil, p.err
		}
		out = append(out, th)
	}
	return out, nil
}

// restThreads は、GraphQL を使えない環境の REST の道で読む。ccr/review_threads が返すのは {resolved, outdated, path, line,
// comment_ids} だけで、スレッドの id・本文・url・作成者は含まない。コメントの一覧（全ページ）から、先頭のコメント（id・url・
// 本文・作成の時刻）と最後のコメント（last_*）を引く。スレッドの id は先頭コメントの id（文字列）。
func restThreads(threadsData, commentsData []byte) ([]any, error) {
	rt, err1 := Decode(threadsData)
	cs, err2 := Decode(commentsData)
	rts, isList1 := rt.([]any)
	all, isList2 := cs.([]any)
	if err1 != nil || err2 != nil || !isList1 || !isList2 {
		return nil, ErrNotArrays
	}
	// 同じ id が 2 度あれば後ろのもの（jq の from_entries と同じ）。
	byID := map[string]any{}
	for _, c := range all {
		id, err := Field(c, "id")
		if err != nil {
			return nil, err
		}
		byID[ToString(id)] = c
	}
	out := []any{}
	for _, t := range rts {
		raw, err := at(t, []any{}, "comment_ids")
		if err != nil {
			return nil, err
		}
		list, err := List(raw)
		if err != nil {
			return nil, err
		}
		head, last := "", ""
		if len(list) > 0 {
			head, last = ToString(list[0]), ToString(list[len(list)-1])
		}
		f := Alt(byID[head], map[string]any{})
		l := Alt(byID[last], map[string]any{})
		var p picker
		path := p.at(t, nil, "path")
		line := p.at(t, nil, "line")
		th := githubThread{
			ID: head, Resolved: p.at(t, false, "resolved"), URL: p.at(f, "", "html_url"),
			Path: Alt(path, p.at(f, "", "path")), Line: Alt(line, p.at(f, json.Number("0"), "line")),
			Body: p.at(f, "", "body"), CreatedAt: p.at(f, "", "created_at"),
			LastAt: p.at(l, "", "created_at"), LastAuthor: p.at(l, "", "user", "login"),
		}
		th.LastID = ToString(p.at(l, "", "id"))
		if p.err != nil {
			return nil, p.err
		}
		out = append(out, th)
	}
	return out, nil
}

type gitlabThread struct {
	ID         string `json:"id"`
	Resolved   bool   `json:"resolved"`
	URL        string `json:"url"`
	Path       any    `json:"path"`
	Line       any    `json:"line"`
	Body       any    `json:"body"`
	CreatedAt  any    `json:"created_at"`
	Author     string `json:"author"`
	Own        bool   `json:"own"`
	LastID     string `json:"last_id"`
	LastAt     any    `json:"last_at"`
	LastAuthor any    `json:"last_author"`
}

// markedNote は本文が ccnavi の目印で始まるか（文字列でない本文は目印ではない）。
func markedNote(n any) bool {
	body, _ := Field(n, "body")
	s, ok := Alt(body, "").(string)
	return ok && strings.HasPrefix(s, marker)
}

// writer はノートの書き手の id（取れなければ空）。
func writer(n any) string {
	id, _ := Path(n, "author", "id")
	return ToString(Alt(id, ""))
}

// discussions は GitLab の MR のスレッドを読む。
// 判定に使うノートは、discussion のノートから system: true を除いたもの（空なら discussion ごと除く）。対象は、
// そのノートが 1 つでも解決できる discussion。解決済みは、解決できるノートが全部解決しているとき。代表のノート
// （本文・位置・書き手・url）は、本文が目印で始まらない最初のノート（無ければ先頭）で、ccnavi の依頼コメントに
// ユーザが返信したスレッドは返信が代表になる。own は、ノートの本文が全部目印で始まり、書き手が全部代表と同じときだけ真
// （依頼の投稿者のスレッドに他人が目印で始まる返信を足しても、未解決を隠せないように）。last_* は最後のノート。
func discussions(data []byte, mrURL string) ([]any, error) {
	v, err := Decode(data)
	if err != nil {
		return nil, err
	}
	all, err := List(v)
	if err != nil {
		return nil, err
	}
	out := []any{}
	for _, d := range all {
		given, err := Field(d, "notes")
		if err != nil {
			return nil, err
		}
		raw, _ := given.([]any)
		var notes, resolvable []any
		for _, n := range raw {
			if sys, err := Field(n, "system"); err != nil {
				return nil, err
			} else if sys != true {
				notes = append(notes, n)
			}
		}
		for _, n := range notes {
			if r, _ := Field(n, "resolvable"); Truthy(r) {
				resolvable = append(resolvable, n)
			}
		}
		if len(resolvable) == 0 {
			continue
		}
		rep := notes[0]
		for _, n := range notes {
			if !markedNote(n) {
				rep = n
				break
			}
		}
		last := notes[len(notes)-1]
		resolved := true
		for _, n := range resolvable {
			if r, _ := Field(n, "resolved"); !Truthy(r) {
				resolved = false
			}
		}
		own := writer(rep) != ""
		for _, n := range notes {
			own = own && markedNote(n) && writer(n) == writer(rep)
		}
		id, _ := Field(d, "id")
		repID, _ := Field(rep, "id")
		var p picker
		th := gitlabThread{
			ID: ToString(id), Resolved: resolved, URL: mrURL + "#note_" + ToString(repID),
			Path: p.at(rep, "", "position", "new_path"), Line: p.at(rep, json.Number("0"), "position", "new_line"),
			Body: p.at(rep, "", "body"), CreatedAt: p.at(rep, "", "created_at"),
			Author: writer(rep), Own: own,
			LastAt: p.at(last, "", "created_at"), LastAuthor: ToString(p.at(last, "", "author", "username")),
		}
		th.LastID = ToString(p.at(last, "", "id"))
		if p.err != nil {
			return nil, p.err
		}
		out = append(out, th)
	}
	return out, nil
}

type review struct {
	State       any `json:"state"`
	URL         any `json:"url"`
	SubmittedAt any `json:"submitted_at"`
	Author      any `json:"author"`
}

// Reviews はレビュー（GitHub は reviews、GitLab は reviewers）の応答を [{state, url, submitted_at, author}] にする。
// 変更要求の状態は CHANGES_REQUESTED にそろえる（GitLab の requested_changes）。
func Reviews(kind, mrURL string, data []byte) ([]any, error) {
	v, err := Decode(data)
	if err != nil {
		return nil, err
	}
	all, err := List(v)
	if err != nil {
		return nil, err
	}
	out := []any{}
	for _, r := range all {
		var p picker
		var rv review
		if kind == "github" {
			who := p.at(r, nil, "user", "id")
			rv = review{
				State: p.at(r, "", "state"), URL: p.at(r, "", "html_url"), SubmittedAt: p.at(r, "", "submitted_at"),
				Author: ToString(Alt(who, p.at(r, "", "user", "login"))),
			}
		} else {
			state, err := gitlabState(r)
			if err != nil {
				return nil, err
			}
			who := p.at(r, nil, "user", "id")
			rv = review{
				State: state, URL: mrURL, SubmittedAt: Alt(p.at(r, nil, "updated_at"), p.at(r, "", "created_at")),
				Author: ToString(Alt(who, p.at(r, "", "user", "username"))),
			}
		}
		if p.err != nil {
			return nil, p.err
		}
		out = append(out, rv)
	}
	return out, nil
}

// gitlabState は reviewers の state。requested_changes は CHANGES_REQUESTED、ほかは ASCII の大文字（文字列でなければ読めない）。
func gitlabState(r any) (string, error) {
	raw, err := Field(r, "state")
	if err != nil {
		return "", err
	}
	if raw == "requested_changes" {
		return "CHANGES_REQUESTED", nil
	}
	s, ok := Alt(raw, "").(string)
	if !ok {
		return "", ErrShape
	}
	return strings.Map(func(r rune) rune {
		if r >= 'a' && r <= 'z' {
			return r - 'a' + 'A'
		}
		return r
	}, s), nil
}

// Marked は、目印 mark を本文に含む投稿の URL を `,` で繋いで返す（送ったかどうかの照合。`find_marked`）。
// kind は github か gitlab、list は issue かコメントの一覧（新しい順。途中で打ち切ったものでもよい）。
// who が空でなければ、その人の投稿だけ数える（GitHub は user.login、GitLab は author.username）。GitHub の issue の
// 一覧に混ざる PR は数えない。GitLab の note に web_url が無ければ、対象の URL（url）に `#note_<id>` を足す。
func Marked(kind, mark, who, url string, list []byte) (string, error) {
	if len(bytes.TrimSpace(list)) == 0 {
		return "", nil
	}
	v, err := Decode(list)
	if err != nil {
		return "", err
	}
	all, _ := v.([]any) // jq の `.[]?`（列でなければ何も無い）
	var urls []string
	for _, x := range all {
		var p picker
		var said, by, link any
		if kind == "github" {
			m, ok := x.(map[string]any)
			if !ok {
				return "", ErrShape
			}
			if _, isPull := m["pull_request"]; isPull {
				continue
			}
			said, by = p.at(x, "", "body"), p.at(x, "", "user", "login")
			link, _ = Field(x, "html_url")
		} else {
			d, isText1 := p.at(x, "", "description").(string)
			b, isText2 := p.at(x, "", "body").(string)
			if p.err == nil && (!isText1 || !isText2) {
				return "", ErrShape
			}
			said, by = d+b, p.at(x, "", "author", "username")
			link = p.at(x, nil, "web_url")
			if link == nil {
				id, _ := Field(x, "id")
				link = url + "#note_" + ToString(id)
			}
		}
		if p.err != nil {
			return "", p.err
		}
		s, ok := said.(string)
		if !ok {
			return "", ErrShape
		}
		if !strings.Contains(s, mark) || (who != "" && by != who) {
			continue
		}
		if link == nil {
			link = ""
		}
		urls = append(urls, oneLine(Text(link)))
	}
	return strings.Join(urls, ","), nil
}

// タイトルの頭の Draft の印（`Draft:`・`[Draft]`・`WIP:` など。大文字小文字は問わない）。行ごとに除く。
var draftMark = regexp.MustCompile(`^[\t\n\v\f\r ]*(\[?([Dd][Rr][Aa][Ff][Tt]|[Ww][Ii][Pp])\]?:?[\t\v\f\r ]*)+`)

// StripDraft はタイトルの頭の Draft の印を除く。
func StripDraft(title string) string {
	lines := strings.Split(title, "\n")
	for i, l := range lines {
		lines[i] = draftMark.ReplaceAllString(l, "")
	}
	return strings.Join(lines, "\n")
}

// UndraftTitle は MR 1 本の応答から、Draft を外すときのタイトルの扱いを返す（`undraft`）。答えは 1 行 1 項目で、
// GitHub は `node <node_id>`（あれば）、どちらも `title <none|same|empty|new>` と、new のとき `new <除いたタイトル>`。
// none はタイトルが無い、same は印が無い、empty は印を除くと空になる。
func UndraftTitle(kind string, data []byte) string {
	v, _ := Decode(data)
	var b strings.Builder
	if kind == "github" {
		if node, _ := Field(v, "node_id"); Truthy(node) {
			b.WriteString("node " + oneLine(Text(node)) + "\n")
		}
	}
	raw, _ := Field(v, "title")
	title := ""
	if Truthy(raw) {
		title = strings.TrimRight(Text(raw), "\n")
	}
	stripped := StripDraft(title)
	switch {
	case title == "":
		b.WriteString("title none\n")
	case stripped == title:
		b.WriteString("title same\n")
	case stripped == "":
		b.WriteString("title empty\n")
	default:
		b.WriteString("title new\nnew " + oneLine(stripped) + "\n")
	}
	return b.String()
}

// UndraftDone は Draft を外した後の応答から、まだ Draft か（`true`・`false`）を読む。form は graphql
// （markPullRequestReadyForReview の応答）・rest（PR 1 本の応答）・gitlab（MR を更新した応答）。読めなければ空。
func UndraftDone(form string, data []byte) string {
	v, err := Decode(data)
	if err != nil {
		return ""
	}
	var got any
	switch form {
	case "graphql":
		got, err = Path(v, "data", "markPullRequestReadyForReview", "pullRequest", "isDraft")
	case "rest":
		got, err = Field(v, "draft")
		got = Alt(got, false)
	case "gitlab":
		var wip any
		if got, err = Field(v, "draft"); err == nil {
			wip, _ = Field(v, "work_in_progress")
			got = Alt(Alt(got, wip), false)
		}
	default:
		return ""
	}
	if err != nil {
		return ""
	}
	return oneLine(Text(got))
}
