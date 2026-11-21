package gitlabapi

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
)

type seen struct {
	method, path, token, body string
}

// serve は handler を httptest で立て、受けた要求を記録する。
func serve(t *testing.T, handler func(w http.ResponseWriter, r *http.Request, n int)) (*Client, func() []seen) {
	t.Helper()
	var mu sync.Mutex
	var got []seen
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		mu.Lock()
		got = append(got, seen{r.Method, r.URL.RequestURI(), r.Header.Get("PRIVATE-TOKEN"), string(b)})
		n := len(got)
		mu.Unlock()
		handler(w, r, n)
	}))
	t.Cleanup(srv.Close)
	c := NewClient(srv.URL)
	c.Sleep = func(time.Duration) {}
	return c, func() []seen {
		mu.Lock()
		defer mu.Unlock()
		return append([]seen(nil), got...)
	}
}

func TestCallStatusAndBody(t *testing.T) {
	t.Parallel()
	c, requests := serve(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		switch r.URL.Path {
		case "/api/v4/json":
			fmt.Fprint(w, `{"id": 12345678901, "name": "x"}`)
		case "/api/v4/empty":
		case "/api/v4/text":
			fmt.Fprint(w, "not json")
		default:
			w.WriteHeader(http.StatusNotFound)
			fmt.Fprint(w, `{"message": "404"}`)
		}
	})
	cases := []struct {
		path   string
		status int
		check  func(any) bool
	}{
		{"/json", 200, func(b any) bool { n, ok := Int(AsMap(b)["id"]); return ok && n == 12345678901 }},
		{"/empty", 200, func(b any) bool { return len(AsMap(b)) == 0 && AsMap(b) != nil }},
		{"/text", 200, func(b any) bool { return b == "not json" }},
		{"/missing", 404, func(b any) bool { return Text(AsMap(b)["message"]) == "404" }},
	}
	for _, tc := range cases {
		status, body := c.Call("GET", tc.path, "tok", nil, "")
		if status != tc.status || !tc.check(body) {
			t.Errorf("%s: status = %d, body = %#v", tc.path, status, body)
		}
	}
	for _, r := range requests() {
		if r.token != "tok" {
			t.Errorf("%s: PRIVATE-TOKEN = %q", r.path, r.token)
		}
	}
}

func TestPayloadIsSentAsJSON(t *testing.T) {
	t.Parallel()
	c, requests := serve(t, func(w http.ResponseWriter, r *http.Request, _ int) { w.WriteHeader(201) })
	if status, _ := c.CreateDiscussion("t", 7, 3, "直して", ""); status != 201 {
		t.Fatalf("status = %d", status)
	}
	got := requests()[0]
	if got.method != "POST" || got.path != "/api/v4/projects/7/merge_requests/3/discussions" || got.body != `{"body":"直して"}` {
		t.Errorf("request = %+v", got)
	}
}

// GET は接続の失敗を Attempts 回まで試し、書き込みは二重に作らないよう 1 回で止める。
func TestConnectionFailureRetriesOnlyReads(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		method    string
		wantTries int
	}{{"GET", Attempts}, {"POST", 1}} {
		t.Run(tc.method, func(t *testing.T) {
			t.Parallel()
			srv := httptest.NewServer(http.NotFoundHandler())
			base := srv.URL
			srv.Close() // 閉じたポートへは接続できない
			c := NewClient(base)
			var events []Event
			var waits int
			c.Recorder = func(e Event) { events = append(events, e) }
			c.Sleep = func(time.Duration) { waits++ }
			status, body := c.Call(tc.method, "/x", "tok", map[string]any{}, "tag")
			if status != 0 || AsMap(body)["error"] == nil {
				t.Errorf("status = %d, body = %#v", status, body)
			}
			retries := 0
			for _, e := range events {
				if e.Kind == "retry" {
					retries++
				}
			}
			if tc.method == "GET" && (retries != tc.wantTries || waits != tc.wantTries-1) {
				t.Errorf("retries = %d, waits = %d", retries, waits)
			}
			if tc.method == "POST" && retries != 0 {
				t.Errorf("POST を試し直した（%d 回）", retries)
			}
			if last := events[len(events)-1]; last.Kind != "result" || last.Tag != "tag" || last.Status != 0 {
				t.Errorf("last event = %+v", last)
			}
		})
	}
}

func TestRecorderNeverSeesTheToken(t *testing.T) {
	t.Parallel()
	c, _ := serve(t, func(w http.ResponseWriter, r *http.Request, _ int) { fmt.Fprint(w, `{}`) })
	var events []Event
	c.Recorder = func(e Event) { events = append(events, e) }
	c.Version("secret-token")
	if len(events) != 1 || strings.Contains(fmt.Sprintf("%+v", events), "secret-token") {
		t.Errorf("events = %+v", events)
	}
}

func TestMergeWaitsUntilMerged(t *testing.T) {
	t.Parallel()
	c, requests := serve(t, func(w http.ResponseWriter, r *http.Request, n int) {
		switch {
		case r.Method == "PUT" && n < 3: // 判定が終わるまで 405
			w.WriteHeader(405)
			fmt.Fprint(w, `{}`)
		case r.Method == "PUT":
			fmt.Fprint(w, `{"state": "opened"}`)
		case n < 6:
			fmt.Fprint(w, `{"state": "opened"}`)
		default:
			fmt.Fprint(w, `{"state": "merged"}`)
		}
	})
	status, body := c.MergeMR("t", 1, 2, 12)
	if status != 200 || Text(AsMap(body)["state"]) != "merged" {
		t.Errorf("status = %d, body = %#v", status, body)
	}
	if n := len(requests()); n != 6 {
		t.Errorf("requests = %d, want 6", n)
	}
	c2, _ := serve(t, func(w http.ResponseWriter, r *http.Request, _ int) { w.WriteHeader(406) })
	if status, _ := c2.MergeMR("t", 1, 2, 2); status != 406 {
		t.Errorf("never mergeable: status = %d", status)
	}
}

func TestReadEnvFile(t *testing.T) {
	t.Parallel()
	path := filepath.Join(base.TempDir(t), "tokens.env")
	if err := os.WriteFile(path, []byte("GITLAB_TOKEN=a=b\nnoequals\nCCNAVI_PROBE_REVIEWER_ID=5\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	got := ReadEnvFile(path)
	if got[TokenKey] != "a=b" || got[ReviewerIDKey] != "5" || len(got) != 2 {
		t.Errorf("got = %v", got)
	}
	if len(ReadEnvFile(filepath.Join(base.TempDir(t), "none"))) != 0 {
		t.Error("無いファイルは空")
	}
}
