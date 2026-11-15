package hostreply

import (
	"bytes"
	"encoding/json"
	"regexp"
	"slices"
	"strings"
)

// Chrome 拡張の decide がホストへ書く前の確かめ。拡張の service worker が取ってきた応答（WriteReplies）から、
// スレッドごとに返信・解決できるか、PAT かロールに足りない権限を読み（WriteInfoOf）、ccnavi の decide が出した
// 書く計画と突き合わせる（Precheck）。

// WriteReplies は service worker が取ってきた応答（読まずに渡したもの）。
//   - GitHub: pages は書く前の確かめの GraphQL（repository の hasIssuesEnabled・isArchived と reviewThreads）の応答を
//     ページごとに並べたもの、scopes は classic の PAT の X-OAuth-Scopes（無ければ null）
//   - GitLab: discussions は全ページを繋いだ一覧、token は personal_access_tokens/self の {status, body}、project は
//     プロジェクトの応答
type WriteReplies struct {
	Host        string          `json:"host"`
	Pages       []any           `json:"pages"`
	Scopes      *string         `json:"scopes"`
	Discussions []any           `json:"discussions"`
	Token       json.RawMessage `json:"token"`
	Project     json.RawMessage `json:"project"`
}

// ThreadAbility は書く前に確かめる 1 つのスレッド。Kind は ok・pending（GitHub の保留中のレビュー）・not-resolvable
// （解決できない種類）・non-diff（GitLab の差分以外の discussion）。Unsupported は ok でないときの、画面に出す名前。
type ThreadAbility struct {
	ID          string   `json:"id"`
	Kind        string   `json:"kind"`
	CanReply    bool     `json:"canReply"`
	CanResolve  bool     `json:"canResolve"`
	ReplyOps    []string `json:"replyOps"`
	Unsupported string   `json:"unsupported,omitempty"`
}

// Missing は足りないと分かった権限。For はその権限が要る操作（all・issue・reply・resolve・comment）。
type Missing struct {
	Need string `json:"need"`
	For  string `json:"for"`
}

// WriteInfo は書く前の確かめの材料。Unknown は書く前には確かめられなかった権限の名前。
type WriteInfo struct {
	Host    string          `json:"host"`
	Threads []ThreadAbility `json:"threads"`
	Missing []Missing       `json:"missing"`
	Unknown []string        `json:"unknown"`
}

// GitLab のロールの数値（Developer = 30。Reporter = 20）。
const (
	developer = 30
	reporter  = 20
)

// 返信の印（`<!-- ccnavi:reply op=<32 桁> key=… -->`）。同じ返信を二度付けないために、既存のコメントから操作 ID を集める。
var replyMark = regexp.MustCompile(`<!-- ccnavi:reply op=([0-9a-f]{32}) key=\S* -->`)

func replyOpsOf(bodies []string) []string {
	out := []string{}
	for _, b := range bodies {
		for _, m := range replyMark.FindAllStringSubmatch(b, -1) {
			out = append(out, m[1])
		}
	}
	return out
}

// unsupportedName は Chrome で扱えない種類のスレッドの名前。
func unsupportedName(kind string) string {
	switch kind {
	case "ok":
		return ""
	case "pending":
		return "保留中のレビューのスレッド"
	case "non-diff":
		return "差分以外の discussion"
	}
	return "解決できない種類のスレッド"
}

// bodyText は本文（文字列でなければ空）。
func bodyText(n any) string {
	b, _ := Field(n, "body")
	s, _ := b.(string)
	return s
}

// present は JS の `alt(v, false) !== false`（null・無い・false のほか）。
func present(v any) bool { return v != nil && v != false }

// WriteInfoOf は取ってきた応答（WriteReplies の JSON）から書く前の確かめの材料を読む。
func WriteInfoOf(data []byte) (WriteInfo, error) {
	// 数は綴りのまま持つ（ほかの読みと同じく Number・ToString で読む）
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var r WriteReplies
	if err := dec.Decode(&r); err != nil {
		return WriteInfo{}, err
	}
	var info WriteInfo
	var err error
	switch r.Host {
	case "github":
		info, err = githubWriteInfo(r)
	case "gitlab":
		info, err = gitlabWriteInfo(r)
	default:
		return WriteInfo{}, ErrShape
	}
	for i := range info.Threads {
		info.Threads[i].Unsupported = unsupportedName(info.Threads[i].Kind)
	}
	return info, err
}

func githubWriteInfo(r WriteReplies) (WriteInfo, error) {
	info := WriteInfo{Host: "github", Threads: []ThreadAbility{}, Missing: []Missing{}, Unknown: []string{}}
	for i, page := range r.Pages {
		repo, err := Path(page, "data", "repository")
		if err != nil {
			return WriteInfo{}, err
		}
		if i == 0 {
			if v, _ := Field(repo, "isArchived"); v == true {
				info.Missing = append(info.Missing, Missing{"リポジトリのアーカイブの解除（読み取り専用です）", "all"})
			}
			if v, _ := Field(repo, "hasIssuesEnabled"); v == false {
				info.Missing = append(info.Missing, Missing{"リポジトリの Issues の有効化", "issue"})
			}
		}
		nodes, err := Path(repo, "pullRequest", "reviewThreads", "nodes")
		if err != nil {
			return WriteInfo{}, err
		}
		all, err := List(nodes)
		if err != nil {
			return WriteInfo{}, err
		}
		for _, t := range all {
			firsts, _ := Path(t, "comments", "nodes")
			first, _ := firstOf(firsts)
			state, _ := Field(first, "state")
			recentNodes, _ := Path(t, "recent", "nodes")
			recent, _ := recentNodes.([]any)
			bodies := make([]string, 0, len(recent))
			for _, c := range recent {
				bodies = append(bodies, bodyText(c))
			}
			id, _ := Field(t, "id")
			reply, _ := Field(t, "viewerCanReply")
			resolve, _ := Field(t, "viewerCanResolve")
			kind := "ok"
			if state == "PENDING" {
				kind = "pending"
			}
			info.Threads = append(info.Threads, ThreadAbility{ID: Text(id), Kind: kind, CanReply: reply == true, CanResolve: resolve == true, ReplyOps: replyOpsOf(bodies)})
		}
	}
	if r.Scopes != nil {
		// classic の PAT: repo（公開リポジトリだけなら public_repo）が無ければ、issue もコメントも書けない
		have := strings.Split(*r.Scopes, ",")
		for i := range have {
			have[i] = strings.TrimSpace(have[i])
		}
		if !slices.Contains(have, "repo") && !slices.Contains(have, "public_repo") {
			info.Missing = append(info.Missing, Missing{"repo スコープ（classic の PAT）", "all"})
		}
	} else {
		// fine-grained の PAT の権限は、書く前には読み取れない
		info.Unknown = append(info.Unknown, "Issues: Read and write", "Pull requests: Read and write")
	}
	return info, nil
}

// firstOf は列の先頭（列でなければ nil）。
func firstOf(v any) (any, bool) {
	l, ok := v.([]any)
	if !ok || len(l) == 0 {
		return nil, false
	}
	return l[0], true
}

func gitlabWriteInfo(r WriteReplies) (WriteInfo, error) {
	info := WriteInfo{Host: "gitlab", Threads: []ThreadAbility{}, Missing: []Missing{}, Unknown: []string{}}
	for _, d := range r.Discussions {
		given, _ := Field(d, "notes")
		raw, _ := given.([]any)
		var notes []any
		for _, n := range raw {
			if sys, _ := Field(n, "system"); sys != true {
				notes = append(notes, n)
			}
		}
		if len(notes) == 0 {
			continue
		}
		bodies := make([]string, 0, len(notes))
		var resolvable []any
		for _, n := range notes {
			bodies = append(bodies, bodyText(n))
			if v, _ := Field(n, "resolvable"); present(v) {
				resolvable = append(resolvable, n)
			}
		}
		id, _ := Field(d, "id")
		t := ThreadAbility{ID: ToString(id), Kind: "not-resolvable", CanReply: true, ReplyOps: replyOpsOf(bodies)}
		if len(resolvable) > 0 {
			onDiff := false
			for _, n := range resolvable {
				typ, _ := Field(n, "type")
				pos, _ := Field(n, "position")
				onDiff = onDiff || typ == "DiffNote" || pos != nil
			}
			t.Kind, t.CanResolve = "non-diff", onDiff
			if onDiff {
				t.Kind = "ok"
			}
		}
		info.Threads = append(info.Threads, t)
	}
	var token struct {
		Status int `json:"status"`
		Body   any `json:"body"`
	}
	_ = json.Unmarshal(r.Token, &token)
	scopes, _ := Field(token.Body, "scopes")
	if list, ok := scopes.([]any); token.Status == 200 && ok {
		if !slices.Contains(list, any("api")) {
			info.Missing = append(info.Missing, Missing{"api スコープ（read_api では書けません）", "all"})
		}
	} else {
		info.Unknown = append(info.Unknown, "api スコープ")
	}
	project, _ := Decode(r.Project)
	level := 0.0
	for _, path := range [][]string{{"permissions", "project_access", "access_level"}, {"permissions", "group_access", "access_level"}} {
		v, _ := Path(project, path...)
		if n, ok := Number(v); ok && n > level {
			level = n
		}
	}
	switch {
	case level == 0:
		info.Unknown = append(info.Unknown, "プロジェクトのロール（Developer 以上）")
	case level < reporter:
		info.Missing = append(info.Missing, Missing{"プロジェクトのロール（Reporter 以上。スレッドの解決は Developer 以上）", "all"})
	case level < developer:
		info.Missing = append(info.Missing, Missing{"プロジェクトのロール（スレッドの解決は Developer 以上）", "resolve"})
	}
	enabled, _ := Field(project, "issues_enabled")
	access, _ := Field(project, "issues_access_level")
	if enabled == false || access == "disabled" {
		info.Missing = append(info.Missing, Missing{"プロジェクトの Issues の有効化", "issue"})
	}
	if archived, _ := Field(project, "archived"); archived == true {
		info.Missing = append(info.Missing, Missing{"プロジェクトのアーカイブの解除（読み取り専用です）", "all"})
	}
	if level > 0 && level < developer {
		for i := range info.Threads {
			info.Threads[i].CanResolve = false
		}
	}
	return info, nil
}

// PlanTarget は計画の返信か解決 1 つ（キーとスレッドの id）。
type PlanTarget struct {
	Key      string `json:"key"`
	ThreadID string `json:"thread_id"`
}

// WritePlan は Precheck が見る、ccnavi の decide が出した書く計画の分（形の検査は拡張が書く前に済ませている）。
type WritePlan struct {
	Issues   []json.RawMessage `json:"issues"`
	Replies  []PlanTarget      `json:"replies"`
	Resolves []PlanTarget      `json:"resolves"`
	Comment  *string           `json:"comment"`
}

// PrecheckResult は書く前の確かめの答え。Ok なら Unknown（確かめられなかった権限）を残して書いてよい。Ok でなければ
// Kind（unsupported: Chrome で扱えない種類のスレッド、unreadable: 決めたスレッドがホストに無い、permission: 権限が
// 足りない）と、画面にそのまま出す Reason、足りない権限の名前 Missing、問題のあったキー Keys。
type PrecheckResult struct {
	Ok      bool     `json:"ok"`
	Kind    string   `json:"kind,omitempty"`
	Reason  string   `json:"reason,omitempty"`
	Missing []string `json:"missing"`
	Keys    []string `json:"keys"`
	Unknown []string `json:"unknown"`
}

// Precheck は計画と書く前の確かめの材料を突き合わせる。足りなければ何も書かない答えにする。
func Precheck(plan WritePlan, info WriteInfo) PrecheckResult {
	threads := map[string]ThreadAbility{}
	for _, t := range info.Threads {
		threads[t.ID] = t
	}
	var unsupported, why, gone, lacking, keys []string
	add := func(list []string, s string) []string {
		if slices.Contains(list, s) {
			return list
		}
		return append(list, s)
	}
	flag := func(target PlanTarget, need string) {
		t, ok := threads[target.ThreadID]
		switch {
		case !ok:
			gone = add(gone, target.Key)
		case t.Kind != "ok":
			unsupported = add(unsupported, target.Key)
			why = add(why, unsupportedName(t.Kind))
		case need == "reply" && !t.CanReply, need == "resolve" && !t.CanResolve:
			if info.Host == "gitlab" {
				lacking = add(lacking, "api スコープと、Developer 以上のロール")
			} else {
				lacking = add(lacking, "Pull requests: Read and write")
			}
			keys = add(keys, target.Key)
		}
	}
	for _, r := range plan.Replies {
		flag(r, "reply")
	}
	for _, r := range plan.Resolves {
		flag(r, "resolve")
	}
	if len(unsupported) > 0 {
		return PrecheckResult{Kind: "unsupported", Reason: "Chrome では扱えない。VS Code で行ってください（" + strings.Join(why, "・") + "があります。何も書きませんでした）", Missing: []string{}, Keys: unsupported, Unknown: []string{}}
	}
	if len(gone) > 0 {
		return PrecheckResult{Kind: "unreadable", Reason: "決めたスレッドがホストに見つかりませんでした（消えたか、別の MR のものです）。MR を読み直してください（何も書きませんでした）", Missing: []string{}, Keys: gone, Unknown: []string{}}
	}
	needs := []string{"all"}
	if len(plan.Issues) > 0 {
		needs = append(needs, "issue")
	}
	if len(plan.Replies) > 0 {
		needs = append(needs, "reply")
	}
	if len(plan.Resolves) > 0 {
		needs = append(needs, "resolve")
	}
	if plan.Comment != nil && *plan.Comment != "" {
		needs = append(needs, "comment")
	}
	for _, m := range info.Missing {
		if slices.Contains(needs, m.For) {
			lacking = add(lacking, m.Need)
		}
	}
	if len(lacking) > 0 {
		if keys == nil {
			keys = []string{}
		}
		return PrecheckResult{Kind: "permission", Reason: "PAT の権限が足りないため、何も書きませんでした。必要な権限: " + strings.Join(lacking, "、") + "。設定画面で PAT を作り直してください", Missing: lacking, Keys: keys, Unknown: []string{}}
	}
	unknown := info.Unknown
	if unknown == nil {
		unknown = []string{}
	}
	return PrecheckResult{Ok: true, Missing: []string{}, Keys: []string{}, Unknown: unknown}
}
