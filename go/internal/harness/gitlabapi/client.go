package gitlabapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	// Attempts は GET を試す回数の上限。
	Attempts = 3
	// Timeout は 1 回の待ちの上限。起動直後の GitLab は応答が遅い。
	Timeout = 120 * time.Second
	// RetryWait は GET を試し直すまでの待ち。
	RetryWait = 10 * time.Second
)

// Event は Recorder に渡す呼び出し 1 回の記録。トークンは入れない。
type Event struct {
	// Kind は "result"（結果）か "retry"（GET の試し直し）。
	Kind    string
	Method  string
	Path    string
	Payload any
	Status  int
	Body    any
	Tag     string
	Error   string
	Attempt int
}

// Client は GitLab の根（`http://127.0.0.1:8929`。`/api/v4` は付けない）への呼び手。
type Client struct {
	// API は `/api/v4` まで含む根。
	API string
	// Recorder があれば、呼び出しごとに 1 回呼ぶ（応答を残す用）。
	Recorder func(Event)
	// Sleep は待ち。nil なら time.Sleep。手元で答える相手には何もしない関数を入れる。
	Sleep func(time.Duration)
	// HTTP は使う http.Client。nil なら Timeout つきの既定。
	HTTP *http.Client
}

// NewClient は base の GitLab を相手にする Client。
func NewClient(base string) *Client {
	return &Client{API: strings.TrimRight(base, "/") + "/api/v4"}
}

func (c *Client) sleep(d time.Duration) {
	if c.Sleep != nil {
		c.Sleep(d)
		return
	}
	time.Sleep(d)
}

func (c *Client) client() *http.Client {
	if c.HTTP != nil {
		return c.HTTP
	}
	return &http.Client{Timeout: Timeout}
}

func (c *Client) record(e Event) {
	if c.Recorder != nil {
		c.Recorder(e)
	}
}

// Call は path（`/api/v4` より後ろ）を呼び、(状態, 本文) を返す。接続できなければ状態 0。
func (c *Client) Call(method, path, token string, payload any, tag string) (int, any) {
	var data []byte
	if payload != nil {
		data, _ = json.Marshal(payload)
	}
	status, raw := 0, ""
	for attempt := 1; attempt <= Attempts; attempt++ {
		var body io.Reader
		if data != nil {
			body = bytes.NewReader(data)
		}
		req, err := http.NewRequest(method, c.API+path, body)
		if err != nil {
			status, raw = 0, errorBody(err)
			break
		}
		req.Header.Set("PRIVATE-TOKEN", token)
		req.Header.Set("Content-Type", "application/json")
		res, err := c.client().Do(req)
		if err == nil {
			b, _ := io.ReadAll(res.Body)
			res.Body.Close()
			status, raw = res.StatusCode, string(b)
			break
		}
		status, raw = 0, errorBody(err)
		if method != http.MethodGet {
			break
		}
		c.record(Event{Kind: "retry", Method: method, Path: path, Error: err.Error(), Attempt: attempt})
		if attempt < Attempts {
			c.sleep(RetryWait)
		}
	}
	decoded := decode(raw)
	c.record(Event{Kind: "result", Method: method, Path: path, Payload: payload, Status: status, Body: decoded, Tag: tag})
	return status, decoded
}

func errorBody(err error) string {
	b, _ := json.Marshal(map[string]string{"error": err.Error()})
	return string(b)
}

// decode は本文を JSON として読む。空は {}、読めなければ文字列のまま。
func decode(raw string) any {
	if raw == "" {
		return map[string]any{}
	}
	dec := json.NewDecoder(strings.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil || dec.More() {
		return raw
	}
	return v
}

// 以下、書き味のための薄い包み。引数 project は数字の ID でも、URL エンコード済みの `ns%2Fproj` でもよい。

// Version は GitLab の版。
func (c *Client) Version(token string) (int, any) {
	return c.Call("GET", "/version", token, nil, "version")
}

// User はトークンの持ち主。
func (c *Client) User(token string) (int, any) { return c.Call("GET", "/user", token, nil, "user") }

// CreateProject は使い捨てのプロジェクト（README なし）を作る。
func (c *Client) CreateProject(token, path string) (int, any) {
	return c.Call("POST", "/projects", token, map[string]any{
		"name": path, "path": path, "visibility": "private", "initialize_with_readme": false,
	}, "project-create")
}

// DeleteProject はプロジェクトを消す（GitLab は遅延削除）。
func (c *Client) DeleteProject(token string, id any) (int, any) {
	return c.Call("DELETE", fmt.Sprintf("/projects/%v", id), token, nil, "project-delete")
}

// AddMember はプロジェクトのメンバーを足す。
func (c *Client) AddMember(token string, id any, userID, level int) (int, any) {
	return c.Call("POST", fmt.Sprintf("/projects/%v/members", id), token,
		map[string]any{"user_id": userID, "access_level": level}, "member-add")
}

// CreateIssue は issue を作る。
func (c *Client) CreateIssue(token string, id any, title, description string) (int, any) {
	return c.Call("POST", fmt.Sprintf("/projects/%v/issues", id), token,
		map[string]any{"title": title, "description": description}, "issue-create")
}

// ListIssues は issue の一覧。perPage が 0 なら GitLab の既定（20 件）。
func (c *Client) ListIssues(token string, id any, state string, perPage int) (int, any) {
	q := "state=" + state + "&order_by=created_at&sort=desc"
	if perPage > 0 {
		q += fmt.Sprintf("&per_page=%d", perPage)
	}
	return c.Call("GET", fmt.Sprintf("/projects/%v/issues?%s", id, q), token, nil, "issues-list")
}

// CreateMR は MR を作る。
func (c *Client) CreateMR(token string, id any, source, target, title string) (int, any) {
	return c.CreateMRDescribed(token, id, source, target, title, "")
}

// CreateMRDescribed は説明つきの MR を作る（説明が空なら送らない）。
func (c *Client) CreateMRDescribed(token string, id any, source, target, title, description string) (int, any) {
	payload := map[string]any{"source_branch": source, "target_branch": target, "title": title}
	if description != "" {
		payload["description"] = description
	}
	return c.Call("POST", fmt.Sprintf("/projects/%v/merge_requests", id), token, payload, "mr-create-"+source)
}

// GetBranch はブランチ 1 本。
func (c *Client) GetBranch(token string, id any, branch string) (int, any) {
	return c.Call("GET", fmt.Sprintf("/projects/%v/repository/branches/%s", id, branch), token, nil, "branch-"+branch)
}

// FindMR は source_branch の MR（全状態）の先頭。無ければ nil。
func (c *Client) FindMR(token string, id any, branch string) map[string]any {
	_, found := c.Call("GET", fmt.Sprintf("/projects/%v/merge_requests?state=all&source_branch=%s", id, branch), token, nil, "mr-list-"+branch)
	if list := AsList(found); len(list) > 0 {
		return AsMap(list[0])
	}
	return nil
}

// ListMRs は MR の一覧（100 件まで）。
func (c *Client) ListMRs(token string, id any, state string) (int, any) {
	return c.Call("GET", fmt.Sprintf("/projects/%v/merge_requests?state=%s&per_page=100", id, state), token, nil, "mrs-list")
}

// GetMR は MR 1 件。
func (c *Client) GetMR(token string, id any, iid any) (int, any) {
	return c.Call("GET", fmt.Sprintf("/projects/%v/merge_requests/%v", id, iid), token, nil, fmt.Sprintf("mr-get-%v", iid))
}

// SetReviewers は MR のレビュアーを置き換える。
func (c *Client) SetReviewers(token string, id any, iid int, reviewers []int) (int, any) {
	return c.Call("PUT", fmt.Sprintf("/projects/%v/merge_requests/%d", id, iid), token,
		map[string]any{"reviewer_ids": reviewers}, "mr-set-reviewer")
}

// Notes は MR のメモ（古い順、100 件まで）。読めなければ空。
func (c *Client) Notes(token string, id any, iid int) []any {
	_, found := c.Call("GET", fmt.Sprintf("/projects/%v/merge_requests/%d/notes?per_page=100&sort=asc&order_by=created_at", id, iid), token, nil, fmt.Sprintf("notes-%d", iid))
	return AsList(found)
}

// CreateDiscussion は MR にスレッドを立てる。tag は記録の印（空なら discussion-create）。
func (c *Client) CreateDiscussion(token string, id any, iid any, body, tag string) (int, any) {
	if tag == "" {
		tag = "discussion-create"
	}
	return c.Call("POST", fmt.Sprintf("/projects/%v/merge_requests/%v/discussions", id, iid), token, map[string]any{"body": body}, tag)
}

// ResolveDiscussion はスレッドを解決する（resolved=false で戻す）。
func (c *Client) ResolveDiscussion(token string, id any, iid any, discussionID string, resolved bool) (int, any) {
	return c.Call("PUT", fmt.Sprintf("/projects/%v/merge_requests/%v/discussions/%s", id, iid, discussionID), token,
		map[string]any{"resolved": resolved}, "discussion-resolve")
}

// ListDiscussions はスレッドの一覧（notes を持つ）。読めなかったことと空を区別したいときのために、状態も返す。
func (c *Client) ListDiscussions(token string, id any, iid any) (int, any) {
	return c.Call("GET", fmt.Sprintf("/projects/%v/merge_requests/%v/discussions?per_page=100", id, iid), token, nil, fmt.Sprintf("discussions-%v", iid))
}

// Discussions は ListDiscussions の中身だけ。読めなければ空。
func (c *Client) Discussions(token string, id any, iid int) []any {
	_, found := c.ListDiscussions(token, id, iid)
	return AsList(found)
}

// ListApprovals は承認の状態（approved・approved_by）。CE でも 200 で返る。
func (c *Client) ListApprovals(token string, id any, iid any) (int, any) {
	return c.Call("GET", fmt.Sprintf("/projects/%v/merge_requests/%v/approvals", id, iid), token, nil, fmt.Sprintf("approvals-%v", iid))
}

// Tree はブランチの一覧（ファイルとディレクトリの名前。path を渡すとその下）。無いブランチ・読めないときは空。100 件まで。
func (c *Client) Tree(token string, id any, ref, path string, recursive bool) []string {
	q := "ref=" + url.QueryEscape(ref) + "&per_page=100"
	if recursive {
		q += "&recursive=true"
	}
	if path != "" {
		q += "&path=" + path
	}
	_, found := c.Call("GET", fmt.Sprintf("/projects/%v/repository/tree?%s", id, q), token, nil, "tree-"+ref)
	var out []string
	for _, item := range AsList(found) {
		out = append(out, Text(AsMap(item)["path"]))
	}
	return out
}

// RequestChanges は変更要求（GitLab EE の機能。CE では 404）。
func (c *Client) RequestChanges(token string, id any, iid any) (int, any) {
	return c.Call("POST", fmt.Sprintf("/projects/%v/merge_requests/%v/request_changes", id, iid), token, nil, "request-changes")
}

// Approve は MR を承認する。
func (c *Client) Approve(token string, id any, iid any) (int, any) {
	return c.Call("POST", fmt.Sprintf("/projects/%v/merge_requests/%v/approve", id, iid), token, nil, "approve")
}

// Reviewers は MR のレビュアー。
func (c *Client) Reviewers(token string, id any, iid int) (int, any) {
	return c.Call("GET", fmt.Sprintf("/projects/%v/merge_requests/%d/reviewers", id, iid), token, nil, "reviewers-after-approve")
}

// FirstCommit は空のリポジトリに最初のコミットを作り、branch を作る。
func (c *Client) FirstCommit(token string, id any, branch string) (int, any) {
	return c.CommitFile(token, id, branch, "README.md", "contract\n", "")
}

// CommitFile はファイル 1 つを足すコミット。startBranch があればそこから branch を切る。
func (c *Client) CommitFile(token string, id any, branch, filePath, content, startBranch string) (int, any) {
	payload := map[string]any{
		"branch":         branch,
		"commit_message": "contract " + branch,
		"actions":        []any{map[string]any{"action": "create", "file_path": filePath, "content": content}},
	}
	if startBranch != "" {
		payload["start_branch"] = startBranch
	}
	return c.Call("POST", fmt.Sprintf("/projects/%v/repository/commits", id), token, payload, "commit-"+branch)
}

// MergeMR は MR をマージし、merged になるまで待つ。mergeability の判定は非同期で、できていないうちは 405 / 406 が返るので、
// 5 秒おいて attempts 回まで試す。返すのは最後の (状態, 本文)。
func (c *Client) MergeMR(token string, id any, iid int, attempts int) (int, any) {
	status, body := 0, any(nil)
	for range attempts {
		status, body = c.Call("PUT", fmt.Sprintf("/projects/%v/merge_requests/%d/merge", id, iid), token, map[string]any{}, "merge")
		if status == http.StatusOK {
			break
		}
		c.sleep(5 * time.Second)
	}
	if status != http.StatusOK {
		return status, body
	}
	for range attempts {
		status, body = c.GetMR(token, id, iid)
		if status == http.StatusOK && Text(AsMap(body)["state"]) == "merged" {
			return status, body
		}
		c.sleep(2 * time.Second)
	}
	return status, body
}

// CreateIssueNote は issue にメモを足す。
func (c *Client) CreateIssueNote(token string, id any, iid int, body string) (int, any) {
	return c.Call("POST", fmt.Sprintf("/projects/%v/issues/%d/notes", id, iid), token, map[string]any{"body": body}, "issue-note")
}

// CreateMRNote は MR にメモを足す。
func (c *Client) CreateMRNote(token string, id any, iid int, body string) (int, any) {
	return c.Call("POST", fmt.Sprintf("/projects/%v/merge_requests/%d/notes", id, iid), token, map[string]any{"body": body}, "mr-note")
}

// WaitMRChecked は merge_status が確定し diff_refs が入るまで待つ。着かなければ最後の (状態, 本文)。判定は呼び手が本文を見て行う。
func (c *Client) WaitMRChecked(token string, id any, iid int, attempts int) (int, any) {
	status, body := 0, any(map[string]any{})
	for range attempts {
		status, body = c.GetMR(token, id, iid)
		m := AsMap(body)
		if status == http.StatusOK && m != nil {
			switch Text(m["merge_status"]) {
			case "checking", "unchecked", "":
			default:
				if v := m["diff_refs"]; v != nil && v != false {
					return status, body
				}
			}
		}
		c.sleep(5 * time.Second)
	}
	return status, body
}
