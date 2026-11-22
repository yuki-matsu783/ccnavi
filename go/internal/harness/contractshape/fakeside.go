package contractshape

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"ccnavi/internal/harness/fakegitlab"
)

// Project は模擬の場面のプロジェクト。
const Project = "ns/proj"

const iso = "2026-01-02T03:04:05.000Z"

// SceneState は模擬の側の場面。open の MR 1 本（スレッド・レビュアー・メモ 2 件）、merged の MR、issue 1 件。
// 本物の側は、録る側が同じ場面を作る。
func SceneState() fakegitlab.State {
	user := func() map[string]any { return map[string]any{"id": 1, "username": "fake-user"} }
	note := func(id int, body string) map[string]any {
		return map[string]any{"id": id, "body": body, "created_at": iso, "author": user()}
	}
	return fakegitlab.State{
		"project": Project,
		"seq":     100,
		"issues": []any{map[string]any{
			"iid": 1, "title": "scene issue", "description": "scene", "state": "opened", "created_at": iso,
			"web_url": "http://x/issues/1", "author": user(),
			"notes": []any{note(90, "scene note")},
		}},
		"mrs": []any{
			map[string]any{
				"iid": 1, "state": "opened", "source_branch": "feature", "target_branch": "main", "title": "scene mr",
				"description": "Closes #1", "web_url": "http://x/merge_requests/1", "created_at": iso,
				"discussions": []any{
					// 実物のスレッド一覧には、システムのメモ（レビュアーの割り当てなど）も混じる
					map[string]any{"id": "d40", "individual_note": true, "notes": []any{map[string]any{
						"id": 40, "body": "assigned", "system": true, "resolvable": false, "type": nil, "created_at": iso, "author": user(),
					}}},
					map[string]any{"id": "d50", "individual_note": false, "notes": []any{map[string]any{
						"id": 50, "body": "thread", "system": false, "resolvable": true, "resolved": false,
						"type": "DiscussionNote", "created_at": iso, "author": user(),
					}}},
				},
				"reviewers": []any{map[string]any{"user": user(), "state": "unreviewed", "created_at": iso}},
				"notes":     []any{note(60, "one"), note(61, "two")},
			},
			map[string]any{
				"iid": 2, "state": "merged", "source_branch": "done", "target_branch": "main", "title": "scene merged",
				"description": "", "web_url": "http://x/merge_requests/2", "created_at": iso, "merged_at": iso,
				"discussions": []any{}, "reviewers": []any{}, "notes": []any{},
				// 本物では、レビュアーが merge の前に承認した
				"approved_by": []any{map[string]any{"approved_at": iso, "user": map[string]any{"id": 2, "username": "reviewer"}}},
			},
		},
	}
}

// SceneContext は要求の `${名前}` に入れる場面の値。
func SceneContext() map[string]string {
	return map[string]string{
		"project":          url.PathEscape(Project),
		"mr.iid":           "1",
		"mr.branch":        "feature",
		"mr.merged_branch": "done",
		"mr.merged_iid":    "2",
		"mr.new_branch":    "fresh",
		"issue.iid":        "1",
		"discussion.id":    "d50",
	}
}

// LoadAllRequests は全要求（requests.json）。書式の検査と、本物の側の記録が使う。
func LoadAllRequests() ([]Request, error) {
	var doc struct {
		Requests []Request `json:"requests"`
	}
	if err := LoadData("requests.json", &doc); err != nil {
		return nil, err
	}
	return doc.Requests, nil
}

// LoadRequests は模擬に送る要求。used_by に sh か scenarios を持つもの（模擬は拡張だけの要求に答えない）。
func LoadRequests() ([]Request, error) {
	all, err := LoadAllRequests()
	if err != nil {
		return nil, err
	}
	var out []Request
	for _, r := range all {
		if r.UsedByAny("sh", "scenarios") {
			out = append(out, r)
		}
	}
	return out, nil
}

// Run は要求の列を模擬に送り、名前ごとのステータスと形を返す。tamper（nil でもよい）は、応答の本文を形にする前に書き換える。
func Run(requests []Request, tamper func(name string, body any) any) (map[string]Entry, error) {
	tmp, err := os.MkdirTemp("", "cc-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tmp)
	server, err := fakegitlab.New(fakegitlab.Config{StatePath: filepath.Join(tmp, "state.json"), Initial: SceneState(), Skew: 0})
	if err != nil {
		return nil, err
	}
	defer server.Close()
	context := SceneContext()
	out := map[string]Entry{}
	for _, req := range requests {
		status, body, err := send(server, req, context)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", req.Name, err)
		}
		for variable, key := range req.Save {
			fields, _ := body.(map[string]any)
			value, ok := fields[key]
			if !ok {
				return nil, fmt.Errorf("%s: 応答に %s が無い", req.Name, key)
			}
			context[variable] = fmt.Sprint(value)
		}
		if tamper != nil {
			body = tamper(req.Name, body)
		}
		out[req.Name] = Entry{Status: status, Shape: Normalize(body)}
	}
	return out, nil
}

func send(server *fakegitlab.Server, req Request, context map[string]string) (int, any, error) {
	path, raw, err := req.Resolve(context)
	if err != nil {
		return 0, nil, err
	}
	var reader io.Reader
	if raw != nil {
		reader = bytes.NewReader(raw)
	}
	httpReq, err := http.NewRequest(req.Method, server.URL+"/api/v4"+path, reader)
	if err != nil {
		return 0, nil, err
	}
	if req.Token == "main" {
		httpReq.Header.Set("PRIVATE-TOKEN", server.Token)
	}
	if reader != nil {
		httpReq.Header.Set("Content-Type", "application/json")
	}
	res, err := (&http.Client{Timeout: 60 * time.Second}).Do(httpReq)
	if err != nil {
		return 0, nil, err
	}
	defer res.Body.Close()
	raw, err = io.ReadAll(res.Body)
	if err != nil {
		return 0, nil, err
	}
	var body any
	if len(bytes.TrimSpace(raw)) > 0 {
		if err := DecodeJSON(raw, &body); err != nil {
			return 0, nil, err
		}
	}
	return res.StatusCode, body, nil
}

// SampleDocument は模擬の応答から作るサンプル（recorded.sample.json）。
func SampleDocument(shapes map[string]Entry) (Document, error) {
	var allow struct {
		NotRecorded []struct {
			What string `json:"what"`
		} `json:"not_recorded"`
	}
	if err := LoadData("allow.json", &allow); err != nil {
		return Document{}, err
	}
	doc := Document{Version: 1, Source: "sample", NotRecorded: []string{}, Requests: shapes}
	for _, n := range allow.NotRecorded {
		doc.NotRecorded = append(doc.NotRecorded, n.What)
	}
	return doc, nil
}
