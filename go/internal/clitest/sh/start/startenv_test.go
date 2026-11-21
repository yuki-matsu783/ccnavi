package start

import (
	"encoding/json"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
)

// ccnavi-start.sh の受入テストの土台（start_new_test.go・start_adopt_test.go が使う）。
//
// 使い捨てのワークスペースと、origin の代わりの bare リポジトリを組み、sh を外から呼ぶ。origin の URL は GitHub / GitLab の形のままにする。
// PATH の先頭に置いた git のスタブが、fetch / push / pull / ls-remote のときだけ bare リポジトリへ向ける
// （`git remote get-url` が書き換えた URL を返すと、ホストを見分けられないため）。ホストは PATH の先頭の curl のスタブ
// （hoststub。routes.json で答え、POST の本文も記録する）が答える。gh / glab は使えないスタブにして、curl とトークンの経路に切り替える。

const (
	ghURL  = "https://github.com/acme/widgets.git"
	glURL  = "https://gitlab.com/acme/widgets.git"
	ghAPI  = "https://api.github.com/repos/acme/widgets"
	glAPI  = hoststub.API
	glProj = hoststub.Project
	// dummyToken は出力に漏れていないことを確かめるダミーのトークン。一時ディレクトリ名に含まれにくい綴りにする。
	dummyToken  = "tok-start-sh-test-7f3a91c2e5"
	branch      = "feature-152-add-login"
	initSubject = "chore: #152 の作業を開始する"
	graphql     = "POST https://api.github.com/graphql"
	pulls       = "GET " + ghAPI + "/pulls?state=open&per_page=100&page=1"
)

// routes と route は routes.json の表と 1 つの答え。
type (
	routes = hoststub.Routes
	route  = hoststub.Route
)

func reply(status int, body any) route { return route{Status: status, Body: body} }

func ok(body any) route { return reply(200, body) }

// obj は JSON の object。
type obj = map[string]any

// arr は JSON の配列。
type arr = []any

func pr(number int, ref, fullName, body string) obj {
	return obj{
		"number": number, "title": "PR " + itoa(number), "body": body, "state": "open", "merged_at": nil,
		"html_url": "https://github.com/acme/widgets/pull/" + itoa(number),
		"head":     obj{"ref": ref, "repo": obj{"full_name": fullName}},
		"base":     obj{"ref": "main"},
	}
}

// prOf は acme/widgets の PR（本文なし）。
func prOf(number int, ref string) obj { return pr(number, ref, "acme/widgets", "") }

func prBody(number int, ref, body string) obj { return pr(number, ref, "acme/widgets", body) }

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

// closing は GitHub の GraphQL（closingIssuesReferences）の答え。issues は {番号, リポジトリ} の並び。
func closing(more bool, issues ...[2]any) obj {
	nodes := arr{}
	for _, i := range issues {
		nodes = append(nodes, obj{"number": i[0], "repository": obj{"nameWithOwner": i[1]}})
	}
	cursor := any(nil)
	if more {
		cursor = "c"
	}
	return obj{"data": obj{"repository": obj{"pullRequest": obj{"closingIssuesReferences": obj{
		"nodes": nodes, "pageInfo": obj{"hasNextPage": more, "endCursor": cursor},
	}}}}}
}

// closedPulls は同じブランチ名の閉じた PR の一覧（マージ済みの確認）。found はマージ済みの PR。
func closedPulls(branch string, found ...obj) routes {
	list := arr{}
	for _, f := range found {
		list = append(list, f)
	}
	return routes{"GET " + ghAPI + "/pulls?state=closed&head=acme%3A" + url.QueryEscape(branch) + "&per_page=100&page=1": ok(list)}
}

// combine は表を左から重ねる（あとが優先）。
func combine(parts ...routes) routes {
	out := routes{}
	for _, p := range parts {
		for k, v := range p {
			out[k] = v
		}
	}
	return out
}

// nexts は行頭が `次: ` の行。
func nexts(text string) []string {
	var out []string
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "次: ") {
			out = append(out, line)
		}
	}
	return out
}

// lookGit は PATH の git の場所。
func lookGit(t testing.TB) string {
	t.Helper()
	p, err := exec.LookPath("git")
	if err != nil {
		t.Skip("git が無い")
	}
	return slash(p)
}

// startGit は fetch / push / pull / ls-remote のときだけ、origin の URL を bare リポジトリへ向ける git のスタブ。
const startGit = `#!/bin/sh
for a in "$@"; do
  case "$a" in
  fetch | push | pull | ls-remote)
    exec '%GIT%' -c "url.%BARE%.insteadOf=$FAKE_ORIGIN_URL" "$@" ;;
  esac
done
exec '%GIT%' "$@"
`

// template は作り置きのリポジトリ（origin の代わりの bare とワークスペース）。クラスで 1 回だけ組み、テストごとに写しを使う。
// どちらも自分の置き場の絶対パスを持たないので、別の場所へ写してよい。
type template struct {
	once sync.Once
	dir  string
}

var templates = map[string]*template{ghURL: {}, glURL: {}}

func (tp *template) build(t testing.TB, origin string) string {
	t.Helper()
	tp.once.Do(func() {
		dir, err := os.MkdirTemp(base.TempBase(), "cc-start-tpl-")
		if err != nil {
			t.Fatal(err)
		}
		base.AtExit(dir)
		tp.dir = dir
		buildRepos(t, dir, origin)
	})
	if tp.dir == "" {
		t.Fatal("作り置きを組めなかった")
	}
	return tp.dir
}

func buildRepos(t testing.TB, dir, origin string) {
	t.Helper()
	bare := filepath.Join(dir, "origin.git")
	git(t, dir, "init", "-q", "--bare", "-b", "main", bare)
	ws := filepath.Join(dir, "ws")
	mkdir(t, ws)
	git(t, dir, "init", "-q", "-b", "main", ws)
	identity(t, ws)
	write(t, filepath.Join(ws, ".gitignore"), "logs/\n.ccnavi/\n.claude/\n")
	git(t, ws, "add", ".gitignore")
	git(t, ws, "commit", "-q", "-m", "seed")
	git(t, ws, "push", "-q", bare, "main")
	git(t, ws, "remote", "add", "origin", origin)
	git(t, ws, "update-ref", "refs/remotes/origin/main", "HEAD")
	git(t, ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	copyScripts(t, ws, "ccnavi-start.sh", "ccnavi-branches.sh", "ccnavi-git.sh", "ccnavi-sync.sh")
	base.PutMessages(t, ws)
	base.ShrinkRepo(t, filepath.Join(ws, ".git"))
	base.ShrinkRepo(t, bare)
}

// startEnv は 1 テストぶんのワークスペース（ws）・bare の origin・ホストのスタブ。
type startEnv struct {
	t          testing.TB
	origin     string
	base       string
	bare, ws   string
	bin        string
	exe        string
	stub       hoststub.Stub
	current    routes
	gitHubHost bool
}

// opt は 1 回の起動の指定。
type opt struct {
	noToken bool
	dir     string
	env     map[string]string
}

// newStart は origin（ghURL か glURL）のワークスペースを組む。
func newStart(t testing.TB, origin string) *startEnv {
	t.Helper()
	need(t, "sh", "git", "jq")
	tpl := templates[origin].build(t, origin)
	dir := base.TempDir(t)
	e := &startEnv{t: t, origin: origin, base: dir, gitHubHost: origin == ghURL}
	e.bare = filepath.Join(dir, "origin.git")
	e.ws = filepath.Join(dir, "ws")
	copyTree(t, filepath.Join(tpl, "origin.git"), e.bare)
	copyTree(t, filepath.Join(tpl, "ws"), e.ws)
	e.exe = exeWrapper(t, filepath.Join(dir, "exe"))
	e.stub = hoststub.Build(t, dir, true)
	e.bin = e.stub.BinDir
	writeExec(t, filepath.Join(e.bin, "git"), strings.NewReplacer("%GIT%", lookGit(t), "%BARE%", slash(e.bare)).Replace(startGit))
	e.route(routes{})
	return e
}

// defaultRoutes はホストのスタブの既定の答え。probe と、書き込みの権限と、紐づく issue。
func (e *startEnv) defaultRoutes() routes {
	if !e.gitHubHost {
		out := routes{
			"GET " + glProj:                    ok(obj{"id": 77, "permissions": obj{"project_access": obj{"access_level": 40}}}),
			"GET " + glAPI + "/user":           ok(obj{"id": 5}),
			"GET " + glProj + "/members/all/5": ok(obj{"access_level": 40}),
		}
		// 実際の MR の番号は何でもよい。hoststub の curl に番号の代わりの * は無いので、使う番号を並べる。
		for n := 1; n <= 20; n++ {
			out["GET "+glProj+"/merge_requests/"+itoa(n)+"/closes_issues?per_page=100&page=1"] =
				ok(arr{obj{"iid": 152, "references": obj{"full": "acme/widgets#152"}}})
		}
		return out
	}
	return routes{
		"GET " + ghAPI: ok(obj{"id": 1, "permissions": obj{"push": true}}),
		graphql:        ok(closing(false, [2]any{152, "acme/widgets"})),
	}
}

// route は既定の答えに r を重ねて、routes.json を書き換える。
func (e *startEnv) route(r routes) {
	e.t.Helper()
	e.current = combine(e.defaultRoutes(), r)
	e.stub.WriteRoutes(e.t, e.current)
}

// more は今の表に r を重ねる。
func (e *startEnv) more(r routes) {
	e.t.Helper()
	e.current = combine(e.current, r)
	e.stub.WriteRoutes(e.t, e.current)
}

func (e *startEnv) script() string {
	return filepath.Join(e.ws, ".ccnavi", "scripts", "ccnavi-start.sh")
}

func (e *startEnv) env(o opt) map[string]string {
	env := e.stub.Env(hoststub.EnvOpts{})
	delete(env, "FAKE_ORIGIN_URL")
	env["FAKE_ORIGIN_URL"] = e.origin
	env["CCNAVI_ENTRY_PATH"] = e.exe
	if !o.noToken {
		if e.gitHubHost {
			env["GITHUB_TOKEN"] = dummyToken
		} else {
			env["GITLAB_TOKEN"] = dummyToken
		}
	}
	return merge(env, o.env)
}

// sh は ccnavi-start.sh を ws で起こす。
func (e *startEnv) sh(args ...string) run.Result { return e.run(opt{}, args...) }

// noTokenSh はトークンなしで起こす。
func (e *startEnv) noTokenSh(args ...string) run.Result { return e.run(opt{noToken: true}, args...) }

func (e *startEnv) run(o opt, args ...string) run.Result {
	e.t.Helper()
	dir := o.dir
	if dir == "" {
		dir = e.ws
	}
	return runScript(e.t, e.script(), dir, e.env(o), args...)
}

// requests はホストのスタブが受けた要求の行。
func (e *startEnv) requests() []string {
	data, err := os.ReadFile(e.stub.Log)
	if err != nil {
		return nil
	}
	return strings.Split(strings.TrimRight(string(data), "\n"), "\n")
}

// posts は POST（GraphQL を除く）の本文。
func (e *startEnv) posts() []map[string]any {
	e.t.Helper()
	lines := e.requests()
	var out []map[string]any
	for i, line := range lines {
		if strings.HasPrefix(line, "POST ") && !strings.HasSuffix(line, "/graphql") && i+1 < len(lines) && strings.HasPrefix(lines[i+1], "BODY ") {
			var body map[string]any
			if err := json.Unmarshal([]byte(lines[i+1][5:]), &body); err != nil {
				e.t.Fatalf("POST の本文を読めない: %v", err)
			}
			out = append(out, body)
		}
	}
	return out
}

func (e *startEnv) wt(name string) string { return filepath.Join(e.ws, ".claude", "worktrees", name) }

func (e *startEnv) localBranches() []string {
	return strings.Fields(git(e.t, e.ws, "for-each-ref", "--format=%(refname:short)", "refs/heads"))
}

func (e *startEnv) remoteBranches() []string {
	return strings.Fields(git(e.t, e.bare, "for-each-ref", "--format=%(refname:short)", "refs/heads"))
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// nothingMade はブランチ・ワークツリー・push・MR の作成のどれもしていない。
func (e *startEnv) nothingMade(name string) {
	e.t.Helper()
	if got := e.localBranches(); len(got) != 1 || got[0] != "main" {
		e.t.Errorf("手元のブランチ = %v", got)
	}
	if got := e.remoteBranches(); len(got) != 1 || got[0] != "main" {
		e.t.Errorf("origin のブランチ = %v", got)
	}
	truth(e.t, !isDir(e.wt(name)), "ワークツリーが作られた")
	if got := e.posts(); len(got) != 0 {
		e.t.Errorf("POST した: %v", got)
	}
}

// fields は出力の `キー: 値` の行（ワークツリー・ブランチ・MR・対象リポジトリ）。
func fields(text string) map[string]string {
	out := map[string]string{}
	for _, line := range strings.Split(text, "\n") {
		for _, key := range []string{"ワークツリー", "ブランチ", "MR", "対象リポジトリ"} {
			if strings.HasPrefix(line, key+": ") {
				out[key] = line[len(key)+2:]
			}
		}
	}
	return out
}

// initialBranch は作りかけ（初期コミットだけ）のブランチを手元と origin に置く。
func (e *startEnv) initialBranch(name string) {
	e.t.Helper()
	tip := git(e.t, e.ws, "commit-tree", "-p", "main", "-m", initSubject, "main^{tree}")
	git(e.t, e.ws, "branch", "-f", name, tip)
	git(e.t, e.ws, "push", "-q", e.bare, name+":refs/heads/"+name)
}

// guidedArgs は案内の行（marker の後ろ）にあるコマンドを、sh と同じ字句の切り方で引数に戻す。
func guidedArgs(t testing.TB, text, marker string) []string {
	t.Helper()
	const prefix = "sh .ccnavi/scripts/ccnavi-start.sh "
	for _, line := range strings.Split(text, "\n") {
		if i := strings.Index(line, marker); i >= 0 {
			cmd := strings.TrimLeft(line[i+len(marker):], " ")
			cmd, _, _ = strings.Cut(cmd, "（")
			cmd = strings.TrimSpace(cmd)
			if !strings.HasPrefix(cmd, prefix) {
				t.Fatalf("案内が %q で始まらない: %s", prefix, cmd)
			}
			return shSplit(cmd[len(prefix):])
		}
	}
	t.Fatalf("%s の行が無い: %s", marker, text)
	return nil
}

// shSplit は単引用符と空白だけで区切る（shlex.split の代わり。案内が使う範囲）。
func shSplit(s string) []string {
	var out []string
	var cur strings.Builder
	inWord, quoted := false, false
	for _, r := range s {
		switch {
		case quoted:
			if r == '\'' {
				quoted = false
			} else {
				cur.WriteRune(r)
			}
		case r == '\'':
			quoted, inWord = true, true
		case r == ' ' || r == '\t':
			if inWord {
				out = append(out, cur.String())
				cur.Reset()
				inWord = false
			}
		default:
			cur.WriteRune(r)
			inWord = true
		}
	}
	if inWord {
		out = append(out, cur.String())
	}
	return out
}

// noIdentity は作成者を推定させない構成（useConfigOnly が無いと、FQDN のある機械で推定されて通る）。環境の差分を返す。
func (e *startEnv) noIdentity() map[string]string {
	e.t.Helper()
	git(e.t, e.ws, "config", "--unset", "user.name")
	git(e.t, e.ws, "config", "--unset", "user.email")
	home := filepath.Join(e.base, "home")
	mkdir(e.t, home)
	return map[string]string{
		"GIT_CONFIG_GLOBAL": os.DevNull, "GIT_CONFIG_SYSTEM": os.DevNull, "HOME": home,
		"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly", "GIT_CONFIG_VALUE_0": "true",
	}
}

// check は --check を打つ。表を渡せば先にその表にする。
func (e *startEnv) check(o opt, r ...routes) run.Result {
	e.t.Helper()
	if len(r) > 0 {
		e.route(r[0])
	}
	return e.run(o, "--check")
}

// labels は `確認: <名前>: <状態>…` の行の、名前から状態の先頭の語への表。
func labels(text string) map[string]string {
	got := map[string]string{}
	for _, line := range strings.Split(text, "\n") {
		if rest, found := strings.CutPrefix(line, "確認: "); found {
			name, state, _ := strings.Cut(rest, ": ")
			state, _, _ = strings.Cut(state, " ")
			state, _, _ = strings.Cut(state, "（")
			got[name] = state
		}
	}
	return got
}

// ---- GitHub のスタブ

// githubNew は GitHub で新規作成が通るスタブ。
func (e *startEnv) githubNew(title string, created bool, br string) {
	e.t.Helper()
	r := combine(routes{
		pulls:                          ok(arr{}),
		"GET " + ghAPI + "/issues/152": ok(obj{"title": title}),
		"GET " + ghAPI + "/pulls?state=open&head=acme%3A" + url.QueryEscape(br): ok(arr{}),
	}, closedPulls(br))
	if created {
		r["POST "+ghAPI+"/pulls"] = reply(201, obj{"number": 7, "html_url": "https://github.com/acme/widgets/pull/7"})
	}
	e.route(r)
}

// githubNewDefault は既定のタイトル・ブランチで githubNew を呼ぶ。
func (e *startEnv) githubNewDefault() { e.githubNew("Add login", true, branch) }

// githubNewIssue は githubNew に、open の issue の一覧と issue の作成を足す。
func (e *startEnv) githubNewIssue(found arr, created bool) {
	e.t.Helper()
	e.githubNewDefault()
	if found == nil {
		found = arr{}
	}
	r := routes{"GET " + ghAPI + "/issues?state=open&per_page=100&page=1": ok(found)}
	if created {
		r["POST "+ghAPI+"/issues"] = reply(201, obj{"number": 152, "html_url": "https://github.com/acme/widgets/issues/152"})
	}
	e.more(r)
}

// mrBranch は origin に name を置き、その MR（number）を答える。
func (e *startEnv) mrBranch(name string, number int, extra routes) {
	e.t.Helper()
	git(e.t, e.ws, "push", "-q", e.bare, "main:refs/heads/"+name)
	e.route(combine(routes{
		"GET " + ghAPI + "/pulls/" + itoa(number): ok(prOf(number, name)),
		pulls: ok(arr{prOf(number, name)}),
	}, closedPulls(name), extra))
}

// adopt は origin に br を置いて --mr 5 を打つ。mr は pr() の上書き。
func (e *startEnv) adopt(br string, mr obj, extra routes) run.Result {
	e.t.Helper()
	git(e.t, e.ws, "push", "-q", e.bare, "main:refs/heads/"+br)
	body := prOf(5, br)
	for k, v := range mr {
		body[k] = v
	}
	e.route(combine(routes{"GET " + ghAPI + "/pulls/5": ok(body)}, closedPulls(br), extra))
	return e.sh("--mr", "5")
}

// ---- GitLab のスタブ

func (e *startEnv) gitlabRoutes(created bool) {
	e.t.Helper()
	r := routes{
		"GET " + glProj: ok(obj{"id": 42}),
		"GET " + glProj + "/issues/152/related_merge_requests?per_page=100&page=1":                        ok(arr{}),
		"GET " + glProj + "/issues/152":                                                                   ok(obj{"title": "Add login"}),
		"GET " + glProj + "/merge_requests?state=opened&source_branch=" + branch + "&per_page=100&page=1": ok(arr{}),
		"GET " + glProj + "/merge_requests?state=merged&source_branch=" + branch + "&per_page=100&page=1": ok(arr{}),
	}
	if created {
		r["POST "+glProj+"/merge_requests"] = reply(201, obj{"iid": 9, "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/9"})
	}
	e.route(r)
}

func gitlabMR(over obj) obj {
	out := obj{
		"iid": 9, "source_branch": "topic/x", "source_project_id": 42, "target_branch": "main", "state": "opened",
		"title": "MR 9", "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/9",
	}
	for k, v := range over {
		out[k] = v
	}
	return out
}

// drop は今の表から keys を外す。
func (e *startEnv) drop(keys ...string) {
	e.t.Helper()
	for _, k := range keys {
		delete(e.current, k)
	}
	e.stub.WriteRoutes(e.t, e.current)
}

// keysOf は表の鍵。
func keysOf(r routes) []string {
	var out []string
	for k := range r {
		out = append(out, k)
	}
	return out
}
