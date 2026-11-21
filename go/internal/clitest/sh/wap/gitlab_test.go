package wap

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// postMode は GitLab の代わりの投稿（POST）の答え方。
type postMode int

const (
	postOK      postMode = iota
	postHTTP500          // 500
	postNotJSON          // 201 だが本文が JSON でない
	postNoID             // 201 で JSON だが投稿の id が無い
)

// gitlab は GitLab の代わり。MR 1 つ、コメント（notes）の投稿と一覧、Draft 外し。
type gitlab struct {
	mu        sync.Mutex
	notes     []map[string]any
	keepDraft bool // 真なら Draft を外す PUT に Draft のままと答える（外し損ねの試験）
	mode      postMode
	srv       *httptest.Server
}

func newGitlab(t *testing.T) *gitlab {
	g := &gitlab{}
	g.srv = httptest.NewServer(http.HandlerFunc(g.serve))
	t.Cleanup(g.srv.Close)
	return g
}

func (g *gitlab) repoURL() string { return g.srv.URL + "/demo/greeter.git" }

func (g *gitlab) noteCount() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return len(g.notes)
}

func (g *gitlab) reset() {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.notes = nil
}

func (g *gitlab) setMode(m postMode) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.mode = m
}

func reply(w http.ResponseWriter, code int, payload any) {
	body, _ := json.Marshal(payload)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", fmt.Sprint(len(body)))
	w.WriteHeader(code)
	_, _ = w.Write(body)
}

func (g *gitlab) serve(w http.ResponseWriter, r *http.Request) {
	g.mu.Lock()
	defer g.mu.Unlock()
	path := r.URL.Path
	switch r.Method {
	case http.MethodGet:
		switch {
		case strings.HasSuffix(path, "/merge_requests"):
			reply(w, 200, []any{map[string]any{
				"iid": 1, "web_url": g.srv.URL + "/demo/greeter/-/merge_requests/1",
				"source_project_id": 1, "source_branch": parentID, "target_branch": "main", "draft": true,
			}})
		case strings.HasSuffix(path, "/notes"):
			if strings.Contains(r.URL.RawQuery, "page=1") {
				reply(w, 200, append([]map[string]any{}, g.notes...))
			} else {
				reply(w, 200, []any{})
			}
		case strings.HasSuffix(path, "/merge_requests/1"):
			reply(w, 200, map[string]any{"iid": 1, "title": "Draft: x"})
		case strings.Contains(r.URL.RawQuery, "per_page"):
			reply(w, 200, []any{})
		default:
			reply(w, 200, map[string]any{"id": 1})
		}
	case http.MethodPut:
		_, _ = io.Copy(io.Discard, r.Body)
		reply(w, 200, map[string]any{"draft": g.keepDraft})
	case http.MethodPost:
		raw, _ := io.ReadAll(r.Body)
		switch g.mode {
		case postHTTP500:
			reply(w, 500, map[string]any{"message": "boom"})
			return
		case postNotJSON:
			body := "<html>gateway</html>"
			w.Header().Set("Content-Length", fmt.Sprint(len(body)))
			w.WriteHeader(201)
			_, _ = io.WriteString(w, body)
			return
		case postNoID:
			reply(w, 201, map[string]any{"message": "ok"})
			return
		}
		var data map[string]any
		_ = json.Unmarshal(raw, &data)
		if strings.HasSuffix(path, "/notes") {
			body, _ := data["body"].(string)
			note := map[string]any{"id": len(g.notes) + 1, "body": body, "created_at": "t"}
			g.notes = append(g.notes, note)
			reply(w, 201, note)
		} else {
			reply(w, 201, map[string]any{"iid": 1, "web_url": "u"})
		}
	default:
		reply(w, 405, map[string]any{"message": "405"})
	}
}

// hostKit は GitLab の代わりと実行ファイルの代わり（half）を持つ木。
type hostKit struct {
	*kit
	gl   *gitlab
	path string // git の代わり（origin を GitLab の代わりの URL で答える）を先頭に足した PATH
	half string
}

// newHostKit は GitLab の代わりをつなぐ。template は実行ファイルの代わり（hostHalf か half）。
func newHostKit(t *testing.T, template string) *hostKit {
	need(t, "jq", "curl")
	k := newKit(t)
	h := &hostKit{kit: k, gl: newGitlab(t)}
	h.path = k.rawShim("shim", fill(urlGit, "URL", h.gl.repoURL(), "GIT", k.gitPath))
	h.half = k.installHalf("half", template)
	return h
}

// review は ccnavi-review.sh（o.script があればその sh）を、GitLab の代わりと half につないで起こす。
func (h *hostKit) review(o opts, args ...string) run.Result {
	h.t.Helper()
	extra := map[string]string{env.Path: h.path, env.GitLabToken: "t0k", env.EntryPath: h.half, "HALF_TREE": h.tree}
	for name, value := range o.env {
		extra[name] = value
	}
	script := o.script
	if script == "" {
		script = "ccnavi-review.sh"
	}
	return h.sh(script, opts{dir: orDir(o.dir, h.tree), input: o.input, env: extra}, args...)
}

// carried は子の状態が親のワークツリーにコミットされ、push され、残りが無い。
func (h *hostKit) carried(rel string) {
	h.t.Helper()
	has(h.t, strings.Join(h.committed("HEAD"), "\n"), rel)
	eq(h.t, "リモートの親", h.remoteParent(), h.head())
	eq(h.t, "未コミット", h.dirty(), "")
}

// carriedAfterDrop は ready が親のワークツリーを消したあと。親のブランチ（ワークスペースの側）で確かめる。
func (h *hostKit) carriedAfterDrop(rel string) {
	h.t.Helper()
	if exists(h.tree) {
		h.t.Errorf("親のワークツリーが残っている")
	}
	has(h.t, strings.Join(h.committedIn(h.ws, parentID), "\n"), rel)
	eq(h.t, "リモートの親", h.remoteParent(), h.sha(h.ws, parentID))
}
