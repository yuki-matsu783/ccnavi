package fakegitlab

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptrace"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/fakegitlab"
)

const (
	project = "ns/proj"
	enc     = "ns%2Fproj"
)

func state(extra map[string]any) fakegitlab.State {
	s := fakegitlab.State{"project": project, "seq": 100, "issues": []any{}, "mrs": []any{}}
	for k, v := range extra {
		s[k] = v
	}
	return s
}

// do は 1 回叩いて (状態, JSON) を返す。token が空なら模擬のトークンを使う。
func do(t testing.TB, srv *fakegitlab.Server, method, path string, body any, token ...string) (int, any) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, srv.URL+path, reader)
	if err != nil {
		t.Fatal(err)
	}
	tok := srv.Token
	if len(token) > 0 {
		tok = token[0]
	}
	req.Header.Set("PRIVATE-TOKEN", tok)
	client := &http.Client{Timeout: 5 * time.Second}
	res, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	raw, _ := io.ReadAll(res.Body)
	var got any
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &got); err != nil {
			t.Fatalf("%s %s: JSON でない応答 %q", method, path, raw)
		}
	}
	return res.StatusCode, got
}

func field(v any, keys ...string) any {
	for _, k := range keys {
		m, _ := v.(map[string]any)
		v = m[k]
	}
	return v
}

func iids(v any) []int {
	out := []int{}
	for _, x := range v.([]any) {
		out = append(out, int(field(x, "iid").(float64)))
	}
	return out
}

func readState(t testing.TB, path string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	return got
}

// 起動して要求が通り、トークンが違えば 401、止めたあとはつながらない（2 回目の Close は何もしない）。
func TestItServesAndStops(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, state(nil))
	if srv.Port() <= 0 || srv.URL != fmt.Sprintf("http://127.0.0.1:%d", srv.Port()) || srv.Token != fakegitlab.Token {
		t.Errorf("port=%d url=%q token=%q", srv.Port(), srv.URL, srv.Token)
	}
	if code, got := do(t, srv, "GET", "/api/v4/version", nil); code != 200 || field(got, "version") == nil {
		t.Errorf("version = %d %v", code, got)
	}
	if code, got := do(t, srv, "GET", "/api/v4/projects/"+enc, nil); code != 200 || field(got, "path_with_namespace") != project {
		t.Errorf("project = %d %v", code, got)
	}
	if code, _ := do(t, srv, "GET", "/api/v4/projects/"+enc, nil, "x"); code != 401 {
		t.Errorf("トークン違いが %d", code)
	}
	if code, _ := do(t, srv, "GET", "/api/v4/user", nil, "x"); code != 401 {
		t.Errorf("/user のトークン違いが %d", code)
	}
	srv.Close()
	client := &http.Client{Timeout: 2 * time.Second}
	if res, err := client.Get(srv.URL + "/"); err == nil {
		res.Body.Close()
		t.Error("止めたあとにつながった")
	}
	srv.Close()
}

// アクセスのログは `METHOD パス` を順に残す。
func TestTheAccessLogKeepsTheMethodAndPath(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, state(nil))
	do(t, srv, "GET", "/api/v4/version", nil)
	do(t, srv, "GET", "/api/v4/projects/"+enc, nil)
	if want := []string{"GET /api/v4/version", "GET /api/v4/projects/" + enc}; !slices.Equal(srv.Requests(), want) {
		t.Errorf("requests = %v, want %v", srv.Requests(), want)
	}
}

// 初期の状態は、起こす前に状態ファイルへ書く。状態ファイルが無く、初期の状態も無ければ起こさない。
func TestTheStateFileIsWrittenBeforeStartAndRequiredWithoutAnInitialState(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	path := filepath.Join(dir, "written.json")
	srv, err := fakegitlab.New(fakegitlab.Config{StatePath: path, Initial: state(map[string]any{"seq": 7})})
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	if got := readState(t, path)["seq"]; got != float64(7) {
		t.Errorf("seq = %v", got)
	}
	if _, err := fakegitlab.New(fakegitlab.Config{StatePath: filepath.Join(dir, "none.json")}); err == nil {
		t.Error("状態ファイルが無いのに起こせた")
	}
}

// 手で使う入口の形: すでにある状態ファイルから起こし、人間役の POST が状態ファイルに残り、要求を Echo に書く。
func TestNewServesFromAnExistingStateFile(t *testing.T) {
	t.Parallel()
	path := filepath.Join(base.TempDir(t), "state.json")
	first, err := fakegitlab.New(fakegitlab.Config{StatePath: path, Initial: state(nil)})
	if err != nil {
		t.Fatal(err)
	}
	first.Close()
	var echo bytes.Buffer
	srv, err := fakegitlab.New(fakegitlab.Config{StatePath: path, Echo: &echo})
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	if code, got := do(t, srv, "POST", "/api/v4/projects/"+enc+"/merge_requests", map[string]any{"title": "by-hand"}); code != 201 || field(got, "title") != "by-hand" {
		t.Errorf("POST = %d %v", code, got)
	}
	if _, got := do(t, srv, "GET", "/api/v4/projects/"+enc+"/merge_requests", nil); len(got.([]any)) != 1 {
		t.Errorf("一覧 = %v", got)
	}
	if mrs := readState(t, path)["mrs"].([]any); len(mrs) != 1 {
		t.Errorf("状態ファイルの MR = %v", mrs)
	}
	if want := "fake-gitlab POST /api/v4/projects/" + enc + "/merge_requests\n"; !strings.HasPrefix(echo.String(), want) {
		t.Errorf("echo = %q", echo.String())
	}
}

// 401 と 404 を返したあとも、本文を読み捨ててあるので、同じクライアントの次の要求が通る。
func TestARejectedRequestWithABodyDoesNotBreakTheNextOne(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, state(nil))
	cases := []struct {
		name, path, token string
		want              int
	}{
		{"401", "/api/v4/projects/" + enc + "/merge_requests", "wrong", 401},
		{"別のプロジェクト", "/api/v4/projects/x%2Fy/merge_requests", "", 404},
		{"知らないルート", "/api/v4/nothing", "", 404},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var tok []string
			if c.token != "" {
				tok = []string{c.token}
			}
			if code, _ := do(t, srv, "POST", c.path, map[string]any{"title": strings.Repeat("t", 50)}, tok...); code != c.want {
				t.Errorf("code = %d, want %d", code, c.want)
			}
			if code, got := do(t, srv, "GET", "/api/v4/projects/"+enc, nil); code != 200 || field(got, "path_with_namespace") != project {
				t.Errorf("次の要求 = %d %v", code, got)
			}
			if !nextRequestReusesTheConnection(t, srv) {
				t.Error("次の要求が同じ接続で通らない（本文を読み捨てていない）")
			}
		})
	}
}

// nextRequestReusesTheConnection は GET を打ち、直前の要求の接続を使い回したかを返す。
// 接続が空きの置き場に戻るのは非同期なので、使い回しに当たるまで数回打つ（接続を切る模擬なら毎回当たらない）。
func nextRequestReusesTheConnection(t testing.TB, srv *fakegitlab.Server) bool {
	t.Helper()
	for range 10 {
		if getReusingTheConnection(t, srv) {
			return true
		}
	}
	return false
}

func getReusingTheConnection(t testing.TB, srv *fakegitlab.Server) bool {
	t.Helper()
	req, err := http.NewRequest("GET", srv.URL+"/api/v4/projects/"+enc, nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("PRIVATE-TOKEN", srv.Token)
	reused := false
	trace := &httptrace.ClientTrace{GotConn: func(info httptrace.GotConnInfo) { reused = info.Reused }}
	res, err := http.DefaultClient.Do(req.WithContext(httptrace.WithClientTrace(req.Context(), trace)))
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, res.Body)
	res.Body.Close()
	return reused
}

// act で MR を引くすべての要求（method, path の MR 以降, 本文）。
var mrRequests = []struct {
	method, tail string
	body         any
}{
	{"GET", "", nil},
	{"PUT", "", map[string]any{"title": "x"}},
	{"GET", "/discussions", nil},
	{"POST", "/discussions", map[string]any{"body": "b"}},
	{"PUT", "/discussions/d1?resolved=true", nil},
	{"GET", "/reviewers", nil},
	{"PUT", "/reviewers/5?state=reviewed", nil},
	{"GET", "/notes", nil},
	{"POST", "/notes", map[string]any{"body": "b"}},
	{"PUT", "/merge", nil},
	{"GET", "/closes_issues", nil},
	{"GET", "/approvals", nil},
	{"POST", "/approve", nil},
	{"POST", "/discussions/d1/notes", map[string]any{"body": "b"}},
}

// 存在しない MR への要求は、MR を引く全ルートで「MR が無い」404 になり、状態を変えない。
func TestAMissingMergeRequestIsA404OnEveryRoute(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, state(nil))
	for _, c := range mrRequests {
		t.Run(c.method+" "+c.tail, func(t *testing.T) {
			code, got := do(t, srv, c.method, "/api/v4/projects/"+enc+"/merge_requests/99"+c.tail, c.body)
			if code != 404 {
				t.Fatalf("code = %d, body = %v", code, got)
			}
			// プロジェクトや経路の 404 ではなく、MR が無い 404 であること
			if msg, _ := field(got, "message").(string); !strings.Contains(msg, "99") {
				t.Errorf("message = %q", msg)
			}
			if code, _ := do(t, srv, "GET", "/api/v4/projects/"+enc, nil); code != 200 {
				t.Errorf("次の要求 = %d", code)
			}
		})
	}
	t.Run("状態は変わらない", func(t *testing.T) {
		// サブテストは順に走る（並行にしない）ので、ここは全部の要求のあと
		saved := readState(t, srv.StatePath)
		if mrs := saved["mrs"].([]any); len(mrs) != 0 || saved["seq"] != float64(100) {
			t.Errorf("mrs = %v, seq = %v", mrs, saved["seq"])
		}
	})
}

// 2 つ同時に立てても、状態ファイルは干渉しない。片方を止めても、もう片方は動く。
func TestTwoServersDoNotInterfere(t *testing.T) {
	t.Parallel()
	a := fakegitlab.Start(t, state(map[string]any{"project": "a/one"}))
	b := fakegitlab.Start(t, state(map[string]any{"project": "b/two"}))
	if a.Port() == b.Port() || a.StatePath == b.StatePath {
		t.Fatalf("ポートか状態ファイルが同じ: %d %d", a.Port(), b.Port())
	}
	code, made := do(t, a, "POST", "/api/v4/projects/a%2Fone/merge_requests", map[string]any{"title": "only-a"})
	if code != 201 || field(made, "title") != "only-a" {
		t.Fatalf("POST = %d %v", code, made)
	}
	// b には届かず、プロジェクトの名前も別
	if _, got := do(t, b, "GET", "/api/v4/projects/b%2Ftwo/merge_requests", nil); len(got.([]any)) != 0 {
		t.Errorf("b の MR = %v", got)
	}
	if code, _ := do(t, b, "GET", "/api/v4/projects/a%2Fone", nil); code != 404 {
		t.Errorf("b が a のプロジェクトに %d", code)
	}
	if n := len(readState(t, a.StatePath)["mrs"].([]any)); n != 1 {
		t.Errorf("a の状態の MR = %d", n)
	}
	if n := len(readState(t, b.StatePath)["mrs"].([]any)); n != 0 {
		t.Errorf("b の状態の MR = %d", n)
	}
	a.Close()
	if code, _ := do(t, b, "GET", "/api/v4/version", nil); code != 200 {
		t.Errorf("b = %d", code)
	}
}

// skew は返す時刻をずらす。既定は 3 分で、0 も負も渡せる。
func TestSkewMovesTheClock(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		skew []time.Duration
		want time.Duration
	}{
		{"既定", nil, 3 * time.Minute},
		{"進める", []time.Duration{time.Hour}, time.Hour},
		{"戻す", []time.Duration{-10 * time.Minute}, -10 * time.Minute},
		{"ゼロ", []time.Duration{0}, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			srv := fakegitlab.Start(t, state(nil), c.skew...)
			_, made := do(t, srv, "POST", "/api/v4/projects/"+enc+"/merge_requests", map[string]any{"title": "t"})
			stamp, err := time.Parse(time.RFC3339Nano, field(made, "created_at").(string))
			if err != nil {
				t.Fatal(err)
			}
			if diff := time.Until(stamp) - c.want; diff < -30*time.Second || diff > 30*time.Second {
				t.Errorf("ずれ = %v, want %v", time.Until(stamp), c.want)
			}
		})
	}
}

// 実コードの sh が打つルート: closes_issues・user・member・issue・スレッドへの返信・承認・レビュアー。
func scriptState(description string) fakegitlab.State {
	mr := map[string]any{
		"iid": 1, "state": "opened", "source_branch": "b", "target_branch": "main", "title": "t", "description": description,
		"discussions": []any{}, "reviewers": []any{}, "notes": []any{},
	}
	issues := []any{}
	for n := 1; n <= 3; n++ {
		issues = append(issues, map[string]any{"iid": n, "title": fmt.Sprintf("i%d", n), "state": "opened"})
	}
	return state(map[string]any{"mrs": []any{mr}, "issues": issues})
}

func TestClosesIssuesReturnsTheIssuesNamedByAClosingWord(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, description string
		want              []int
	}{
		{"閉じる語", "説明\n\nCloses #2\nfixes: #3 and Resolved #9", []int{2, 3}},
		{"閉じる語が無い", "Related to #2", []int{}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			srv := fakegitlab.Start(t, scriptState(c.description))
			code, got := do(t, srv, "GET", "/api/v4/projects/"+enc+"/merge_requests/1/closes_issues", nil)
			if code != 200 || !slices.Equal(iids(got), c.want) {
				t.Fatalf("code = %d, iids = %v, want %v", code, got, c.want)
			}
			// 実物は references を返さない。sh は代替の `<パス>#<iid>` で受ける
			if len(c.want) > 0 && field(got.([]any)[0], "references") != nil {
				t.Error("references を返した")
			}
		})
	}
}

func TestUserAndMember(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, scriptState(""))
	if code, got := do(t, srv, "GET", "/api/v4/user", nil); code != 200 || field(got, "id") != float64(1) {
		t.Errorf("user = %d %v", code, got)
	}
	if code, got := do(t, srv, "GET", "/api/v4/projects/"+enc+"/members/all/1", nil); code != 200 || field(got, "access_level") != float64(40) {
		t.Errorf("member = %d %v", code, got)
	}
}

func TestIssueGetNotesAndRelatedMergeRequests(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, scriptState("Closes #2"))
	base := "/api/v4/projects/" + enc + "/issues"
	if code, got := do(t, srv, "GET", base+"/2", nil); code != 200 || field(got, "iid") != float64(2) {
		t.Errorf("issue = %d %v", code, got)
	}
	if code, got := do(t, srv, "GET", base+"/2/related_merge_requests", nil); code != 200 || !slices.Equal(iids(got), []int{1}) {
		t.Errorf("related(2) = %d %v", code, got)
	}
	if code, got := do(t, srv, "GET", base+"/1/related_merge_requests", nil); code != 200 || len(got.([]any)) != 0 {
		t.Errorf("related(1) = %d %v", code, got)
	}
	if code, got := do(t, srv, "POST", base+"/2/notes", map[string]any{"body": "hi"}); code != 201 || field(got, "body") != "hi" {
		t.Errorf("POST notes = %d %v", code, got)
	}
	code, got := do(t, srv, "GET", base+"/2/notes", nil)
	if notes := got.([]any); code != 200 || len(notes) != 1 || field(notes[0], "body") != "hi" {
		t.Errorf("GET notes = %d %v", code, got)
	}
	// メモは状態が内部に持つだけで、issue の応答には出さない
	if _, got := do(t, srv, "GET", base+"/2", nil); field(got, "notes") != nil {
		t.Errorf("issue に notes が出た: %v", got)
	}
	if code, _ := do(t, srv, "GET", base+"/99", nil); code != 404 {
		t.Errorf("無い issue = %d", code)
	}
	if code, got := do(t, srv, "PUT", base+"/2", map[string]any{"state_event": "close"}); code != 200 || field(got, "state") != "closed" {
		t.Errorf("close = %d %v", code, got)
	}
}

func TestANoteCanBeAddedToADiscussion(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, scriptState(""))
	mr := "/api/v4/projects/" + enc + "/merge_requests/1"
	_, disc := do(t, srv, "POST", mr+"/discussions", map[string]any{"body": "first"})
	id := field(disc, "id").(string)
	if code, got := do(t, srv, "POST", mr+"/discussions/"+id+"/notes", map[string]any{"body": "reply"}); code != 201 || field(got, "body") != "reply" {
		t.Errorf("reply = %d %v", code, got)
	}
	_, discs := do(t, srv, "GET", mr+"/discussions", nil)
	if n := len(field(discs.([]any)[0], "notes").([]any)); n != 2 {
		t.Errorf("スレッドのノート = %d", n)
	}
	if code, _ := do(t, srv, "POST", mr+"/discussions/nope/notes", map[string]any{"body": "x"}); code != 404 {
		t.Errorf("無いスレッド = %d", code)
	}
	// 解決は ?resolved=true（既定）か false
	if _, got := do(t, srv, "PUT", mr+"/discussions/"+id, nil); field(got, "notes").([]any)[0].(map[string]any)["resolved"] != true {
		t.Errorf("解決されない: %v", got)
	}
	if _, got := do(t, srv, "PUT", mr+"/discussions/"+id+"?resolved=false", nil); field(got, "notes").([]any)[0].(map[string]any)["resolved"] != false {
		t.Errorf("未解決に戻らない: %v", got)
	}
}

// 承認は同じ人の 2 回目で増えず、レビュアーは無ければ足して有れば直す。
func TestApprovalIsIdempotentAndReviewersAreUpserted(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, scriptState(""))
	mr := "/api/v4/projects/" + enc + "/merge_requests/1"
	if _, got := do(t, srv, "GET", mr+"/approvals", nil); field(got, "approved") != false {
		t.Errorf("承認前 = %v", got)
	}
	for i := range 2 {
		code, got := do(t, srv, "POST", mr+"/approve", nil)
		if n := len(field(got, "approved_by").([]any)); code != 201 || n != 1 || field(got, "approved") != true {
			t.Errorf("%d 回目の承認 = %d %v", i+1, code, got)
		}
	}
	if code, got := do(t, srv, "PUT", mr+"/reviewers/5?state=requested_changes", nil); code != 201 || field(got, "state") != "requested_changes" {
		t.Errorf("レビュアーを足す = %d %v", code, got)
	}
	if code, got := do(t, srv, "PUT", mr+"/reviewers/5", nil); code != 200 || field(got, "state") != "reviewed" {
		t.Errorf("レビュアーを直す = %d %v", code, got)
	}
	if _, got := do(t, srv, "GET", mr+"/reviewers", nil); len(got.([]any)) != 1 {
		t.Errorf("レビュアー = %v", got)
	}
}

// 存在する MR は、存在しない MR の隣でも答える。マージと Draft の判定を MR の見た目に出す。
func TestMergeRequestViewShowsDraftAndMerge(t *testing.T) {
	t.Parallel()
	srv := fakegitlab.Start(t, scriptState(""))
	base := "/api/v4/projects/" + enc + "/merge_requests"
	if code, got := do(t, srv, "GET", base+"/1", nil); code != 200 || field(got, "draft") != false || field(got, "source_project_id") != float64(1) {
		t.Errorf("MR = %d %v", code, got)
	}
	if code, _ := do(t, srv, "GET", base+"/2", nil); code != 404 {
		t.Errorf("無い MR = %d", code)
	}
	if _, got := do(t, srv, "PUT", base+"/1", map[string]any{"title": "Draft: x"}); field(got, "draft") != true {
		t.Errorf("Draft の判定 = %v", got)
	}
	if _, got := do(t, srv, "PUT", base+"/1/merge", nil); field(got, "state") != "merged" {
		t.Errorf("merge = %v", got)
	}
	if _, got := do(t, srv, "GET", base+"?state=opened", nil); len(got.([]any)) != 0 {
		t.Errorf("マージ済みが opened の一覧に出た: %v", got)
	}
}
