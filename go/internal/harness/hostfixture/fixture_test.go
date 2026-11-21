package hostfixture

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

func env(m map[string]string) Getenv { return func(k string) string { return m[k] } }

func read(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func asJSON(t *testing.T, v any) string {
	t.Helper()
	return string(marshal(v))
}

// 見本の根か場面が空なら、黙って 404・403 にせず、変数名を言って 1 で終わる。
func TestFixtureCurlNamesAMissingVariable(t *testing.T) {
	t.Parallel()
	var stdout, stderr strings.Builder
	if code := GitlabCurl([]string{"http://x/api/v4/user"}, strings.NewReader(""), &stdout, &stderr, env(nil)); code != 1 || !strings.Contains(stderr.String(), FixtureRootVar) {
		t.Errorf("code = %d, stderr = %q", code, stderr.String())
	}
	stderr.Reset()
	if code := GithubCurl([]string{"http://x/user"}, strings.NewReader(""), &stdout, &stderr, env(map[string]string{FixtureRootVar: "/r"})); code != 1 || !strings.Contains(stderr.String(), "FAKE_GITHUB_SCENE") {
		t.Errorf("code = %d, stderr = %q", code, stderr.String())
	}
}

// 場面の見本があり、場面の名前を数えられる。
func TestScenesExist(t *testing.T) {
	t.Parallel()
	for _, h := range []Host{GitLab, GitHub} {
		if names := SceneNames(t, h); len(names) < 3 {
			t.Errorf("%s の場面 = %v", h, names)
		}
	}
}

// GitLab の見本の規則: user・プロジェクト・MR の一覧（ブランチが合うときだけ）・discussions のページ・依頼の投稿（状態のファイル）。
func TestGitlabAnswerFollowsTheFixtureRules(t *testing.T) {
	t.Parallel()
	root, scene := base.Root(), "hostile"
	dir := SceneDir(GitLab, scene)
	state := filepath.Join(base.TempDir(t), "notes.json")
	getenv := env(map[string]string{"FAKE_GITLAB_POSTER": "someone"})
	proj := "/projects/acme%2Fwidgets"
	cases := []struct {
		name, method, rel, body string
		code                    int
		want                    string // 応答の JSON。空なら見ない
	}{
		{"user は見本そのもの", "GET", "/user", "", 200, read(t, filepath.Join(dir, "user.json"))},
		{"プロジェクト", "GET", proj, "", 200, `{"default_branch":"main","id":42}`},
		{"ブランチが合う MR", "GET", proj + "/merge_requests?state=opened&source_branch=i0001", "", 200, read(t, filepath.Join(dir, "mrs.json"))},
		{"ブランチが違えば空", "GET", proj + "/merge_requests?state=opened&source_branch=zzz", "", 200, `[]`},
		{"ページが無ければ空", "GET", proj + "/merge_requests/7/discussions?page=99", "", 200, `[]`},
		{"MR が見本に無ければ空", "GET", proj + "/merge_requests/9999/discussions", "", 200, `[]`},
		{"知らない経路", "GET", "/nothing", "", 404, ""},
		{"依頼の投稿の一覧は最初は空", "GET", proj + "/merge_requests/7/notes", "", 200, `[]`},
		{"依頼を投稿する", "POST", proj + "/merge_requests/7/notes", `{"body":"お願い"}`, 201, ""},
		{"投稿が溜まる", "GET", proj + "/merge_requests/7/notes", "", 200, ""},
	}
	for _, c := range cases {
		code, data := GitlabAnswer(getenv, root, scene, c.method, c.rel, c.body, state)
		if code != c.code {
			t.Errorf("%s: code = %d, want %d (%v)", c.name, code, c.code, data)
			continue
		}
		if c.want == "" {
			continue
		}
		if got := asJSON(t, data); !jsonEqual(got, c.want) {
			t.Errorf("%s: got %.200s, want %.200s", c.name, got, c.want)
		}
	}
	// 投稿者は FAKE_GITLAB_POSTER（id は 201）。
	var posted []struct {
		ID     int `json:"id"`
		Body   string
		Author struct {
			ID       int
			Username string
		}
	}
	if err := json.Unmarshal([]byte(read(t, state)), &posted); err != nil || len(posted) != 1 || posted[0].Body != "お願い" || posted[0].Author.Username != "someone" || posted[0].Author.ID != 201 {
		t.Errorf("状態 = %v (%v)", posted, err)
	}
}

func jsonEqual(a, b string) bool {
	var x, y any
	if json.Unmarshal([]byte(a), &x) != nil || json.Unmarshal([]byte(b), &y) != nil {
		return false
	}
	return string(marshal(x)) == string(marshal(y))
}

// GitHub の見本の規則: GraphQL の欄が問い合わせから欠けていれば答えない・変数が足りない mutation は errors・断る設定は 403/401。
func TestGithubAnswerFollowsTheFixtureRules(t *testing.T) {
	t.Parallel()
	root, scene := base.Root(), "changes-requested"
	graphql := func(query string, vars map[string]any) string {
		return asJSON(t, map[string]any{"query": query, "variables": vars})
	}
	threadsQuery := "query($n:Int!,$c:String){ reviewThreads pageInfo hasNextPage endCursor nodes id isResolved comments url path line body createdAt }"
	cases := []struct {
		name string
		env  map[string]string
		body string
		code int
		has  string // 応答に含まれる語
	}{
		{"threads の 1 ページ目", nil, graphql(threadsQuery, map[string]any{"n": 42, "c": nil}), 200, "nodes"},
		{"欄が欠けた問い合わせは答えない", nil, graphql(strings.Replace(threadsQuery, "isResolved", "", 1), map[string]any{"n": 42, "c": nil}), 200, "問い合わせに欄が無い: isResolved"},
		{"viewer", nil, graphql("query { viewer { login } }", nil), 200, `"login"`},
		{"変数が無い mutation", nil, graphql("mutation($id:ID!,$b:String!){ resolveReviewThread(input:{threadId:$id}){ thread{isResolved} } }", map[string]any{"id": "x"}), 200, "変数が無い: b"},
		{"返信を断る設定", map[string]string{"FAKE_GITHUB_FAIL_MUTATION": "reply"}, graphql("mutation($id:ID!){ addPullRequestReviewThreadReply(input:{}){ comment{id} } }", map[string]any{"id": "x"}), 200, "Could not resolve"},
		{"blocked は 403", map[string]string{"FAKE_GITHUB_GRAPHQL": "blocked"}, graphql("query { viewer { login } }", nil), 403, "not available from Claude Code"},
		{"unauthorized は 401", map[string]string{"FAKE_GITHUB_GRAPHQL": "unauthorized"}, graphql("query { viewer { login } }", nil), 401, "Bad credentials"},
	}
	for _, c := range cases {
		code, data := GithubAnswer(env(c.env), root, scene, "POST", "/graphql", c.body, "")
		if got := asJSON(t, data); code != c.code || !strings.Contains(got, c.has) {
			t.Errorf("%s: code = %d, body = %.200s", c.name, code, got)
		}
	}
	// 見本の外
	if code, _ := GithubAnswer(env(nil), root, scene, "GET", "/nothing", "", ""); code != 404 {
		t.Errorf("知らない経路 = %d", code)
	}
	// 持ち主が 403 を返す条件（user.json が無い場面は無いので、通常は 200）
	if code, _ := GithubAnswer(env(nil), root, scene, "GET", "/user", "", ""); code != 200 {
		t.Errorf("/user = %d", code)
	}
}

// REST の代わりの道は GraphQL が blocked のときだけ答え、通常の GitHub では 404。
func TestGithubRestFallbackIsServedOnlyWhenGraphqlIsBlocked(t *testing.T) {
	t.Parallel()
	root, scene := base.Root(), "changes-requested"
	rest := filepath.Join(base.TempDir(t), "rest.json")
	if err := os.WriteFile(rest, []byte(`{"review_threads":[{"id":1}],"comments":[],"body":"b","draft":true}`), 0o644); err != nil {
		t.Fatal(err)
	}
	path := "/repos/acme/widgets/pulls/42/ccr/review_threads"
	cases := []struct {
		name string
		env  map[string]string
		code int
	}{
		{"通常は 404", map[string]string{"FAKE_GITHUB_REST": rest}, 404},
		{"blocked は答える", map[string]string{"FAKE_GITHUB_REST": rest, "FAKE_GITHUB_GRAPHQL": "blocked"}, 200},
		{"blocked でも NO_CCR なら 404", map[string]string{"FAKE_GITHUB_REST": rest, "FAKE_GITHUB_GRAPHQL": "blocked", "FAKE_GITHUB_NO_CCR": "1"}, 404},
	}
	for _, c := range cases {
		if code, data := GithubAnswer(env(c.env), root, scene, "GET", path, "", ""); code != c.code {
			t.Errorf("%s: code = %d (%v)", c.name, code, data)
		}
	}
}

// PATH のスタブから起こした curl・gh が、見本と状態のファイルで答える（teststub の gitlab-host・github-host を通す）。
func TestHostStubsAnswerThroughTheShims(t *testing.T) {
	t.Parallel()
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh が無い")
	}
	tmp := base.TempDir(t)
	bin := filepath.Join(tmp, "bin")
	glEnv := Env(GitLab, "hostile")
	glEnv["FAKE_GITLAB_LOG"] = filepath.Join(tmp, "gl.log")
	Install(t, bin, GitLab)
	r := run.Sh(t, filepath.Join(bin, "curl"), []string{"-fsS", "-X", "GET", "-H", "A: b", gitlabAPI + "/user"}, run.Opts{Env: glEnv})
	if want := read(t, filepath.Join(SceneDir(GitLab, "hostile"), "user.json")); r.Code != 0 || !jsonEqual(r.Stdout, want) {
		t.Errorf("gitlab curl: code=%d stdout=%.100s stderr=%q", r.Code, r.Stdout, r.Stderr)
	}
	r = run.Sh(t, filepath.Join(bin, "curl"), []string{"-fsS", "https://example.com/x"}, run.Opts{Env: glEnv})
	if r.Code != 6 {
		t.Errorf("見本の外の URL = %d", r.Code)
	}
	if got := read(t, glEnv["FAKE_GITLAB_LOG"]); got != "GET /user\n" {
		t.Errorf("log = %q", got)
	}

	ghBin := filepath.Join(tmp, "ghbin")
	Install(t, ghBin, GitHub)
	InstallGh(t, ghBin)
	ghEnv := Env(GitHub, "changes-requested")
	r = run.Sh(t, filepath.Join(ghBin, "curl"), []string{"-fsS", "-X", "GET", githubAPI + "/user"}, run.Opts{Env: ghEnv})
	if want := read(t, filepath.Join(SceneDir(GitHub, "changes-requested"), "user.json")); r.Code != 0 || !jsonEqual(r.Stdout, want) {
		t.Errorf("github curl: code=%d stdout=%.100s stderr=%q", r.Code, r.Stdout, r.Stderr)
	}
	// gh は本文を標準出力に、雑音を標準エラーに出し、4xx では 1 で終わる。
	r = run.Sh(t, filepath.Join(ghBin, "gh"), []string{"api", "--hostname", "github.com", "nothing"}, run.Opts{Env: ghEnv})
	if r.Code != 1 || !strings.Contains(r.Stdout, "Not Found") || !strings.Contains(r.Stderr, "(HTTP 404)") || !strings.Contains(r.Stderr, "雑音") {
		t.Errorf("gh 404: code=%d stdout=%q stderr=%q", r.Code, r.Stdout, r.Stderr)
	}
}
