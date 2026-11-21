package git

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// ccnavi-branches.sh の受入テスト。使い捨てのワークスペースを組み、sh を外から呼ぶ。ホストは PATH の先頭に置いた curl のスタブが
// 答える（gh / glab は使えないスタブにして、curl とトークンの経路に切り替える）。スタブは、要求の表記（`<METHOD> <URL>`）ごとに
// 決めた答えを返し、決めていない要求は 404 にする。
//
// 見るのは次のとおり。
//  1. MR 指定: その MR の元ブランチ（GitHub・GitLab。GitLab のフォークから出た MR は目印を付ける）
//  2. issue 指定: その issue を参照している開いた MR の元ブランチと、名前に番号を含む手元のブランチ
//  3. ホストに繋げない（トークンが無い・origin が無い・API が失敗した）ときは止めず、手元の候補だけを出して「ホストは見ていない」と理由を書く
//  4. projects/<名前>/ の中から打てば、そのリポジトリを見る
//  5. --json と、引数の誤り
const (
	githubAPI = "https://api.github.com/repos/acme/widgets"
	gitlabAPI = "https://gitlab.com/api/v4/projects/acme%2Fwidgets"
)

func pr(number int, ref string, opts ...func(obj)) obj {
	p := obj{
		"number": number, "title": "", "body": "", "state": "open", "merged_at": nil,
		"html_url": "https://github.com/acme/widgets/pull/" + itoa(number),
		"head":     obj{"ref": ref, "repo": obj{"full_name": "acme/widgets"}},
	}
	for _, o := range opts {
		o(p)
	}
	return p
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

func withTitle(s string) func(obj) { return func(p obj) { p["title"] = s } }
func withBody(s string) func(obj)  { return func(p obj) { p["body"] = s } }
func withFork(full string) func(obj) {
	return func(p obj) { p["head"].(obj)["repo"] = obj{"full_name": full} }
}
func withMerged() func(obj) {
	return func(p obj) { p["state"] = "closed"; p["merged_at"] = "2026-10-01T00:00:00Z" }
}

func mr(iid int, branch, state string, pid int) obj {
	return obj{
		"iid": iid, "source_branch": branch, "source_project_id": pid, "state": state,
		"title": "MR " + itoa(iid), "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/" + itoa(iid),
	}
}

type branchesWS struct {
	t      *testing.T
	ws     string
	bin    string
	exe    string
	routes hoststub.Stub
	log    string
}

func newBranchesWS(t *testing.T) *branchesWS {
	t.Helper()
	shkit.Need(t, "sh", "git", "jq")
	tmp := base.TempDir(t)
	b := &branchesWS{t: t, ws: filepath.Join(tmp, "ws")}
	seedRepo(t, b.ws, "main")
	for _, name := range []string{"feature-152-login", "fix/1520-other"} {
		git(t, b.ws, "branch", name)
	}
	shkit.Write(t, filepath.Join(b.ws, ".gitignore"), "logs/\n.ccnavi/\nprojects/\n")
	shkit.CopyScripts(t, b.ws, "ccnavi-branches.sh")
	b.exe = shkit.ExeWrapper(t, filepath.Join(tmp, "exe"))
	b.bin = filepath.Join(tmp, "bin")
	hoststub.WriteShim(t, filepath.Join(b.bin, "curl"), exe.TeststubPath(t), "curl")
	// gh / glab があっても使わせない（疎通の試しで失敗して curl に切り替わる）
	hoststub.WriteFail(t, filepath.Join(b.bin, "gh"))
	hoststub.WriteFail(t, filepath.Join(b.bin, "glab"))
	b.routes = hoststub.Stub{BinDir: b.bin, RoutesPath: filepath.Join(tmp, "routes.json"), Log: filepath.Join(tmp, "requests.log")}
	b.route(nil)
	return b
}

func (b *branchesWS) route(r map[string]any) {
	b.t.Helper()
	routes := hoststub.Routes{}
	for k, v := range r {
		routes[k] = hoststub.Route{Status: 200, Body: v}
	}
	b.routes.WriteRoutes(b.t, routes)
}

func (b *branchesWS) origin(url string) { b.t.Helper(); git(b.t, b.ws, "remote", "add", "origin", url) }

// sh は ccnavi-branches.sh を打つ。extra は GITHUB_TOKEN などの追加の環境。
func (b *branchesWS) sh(cwd string, extra map[string]string, args ...string) run.Result {
	b.t.Helper()
	if cwd == "" {
		cwd = b.ws
	}
	env := map[string]string{
		"PATH":              b.bin + string(os.PathListSeparator) + os.Getenv("PATH"),
		"CCNAVI_ENTRY_PATH": b.exe,
		"FAKE_ROUTES":       b.routes.RoutesPath,
		"FAKE_LOG":          b.routes.Log,
	}
	for k, v := range extra {
		env[k] = v
	}
	return run.Sh(b.t, filepath.Join(b.ws, ".ccnavi", "scripts", "ccnavi-branches.sh"), args, run.Opts{Dir: cwd, Env: env})
}

func (b *branchesWS) ok(cwd string, extra map[string]string, args ...string) string {
	b.t.Helper()
	r := b.sh(cwd, extra, args...)
	if r.Code != 0 {
		b.t.Fatalf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	return r.Stdout
}

var tok = map[string]string{"GITHUB_TOKEN": "t0k"}
var glTok = map[string]string{"GITLAB_TOKEN": "t0k"}

// candidates は「候補 <ブランチ> … 在りか=…」の行を、ブランチ名で引けるようにする。
func candidates(text string) map[string]string {
	found := map[string]string{}
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "候補 ") && strings.Contains(line, "在りか=") {
			found[strings.Fields(line)[1]] = line
		}
	}
	return found
}

func sortedKeys(m map[string]string) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func wantContains(t *testing.T, got, want string) {
	t.Helper()
	if !strings.Contains(got, want) {
		t.Errorf("%q が含まれない:\n%s", want, got)
	}
}

func wantKeys(t *testing.T, found map[string]string, want ...string) {
	t.Helper()
	sort.Strings(want)
	if got := sortedKeys(found); strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("候補 = %v, want %v", got, want)
	}
}

// ---- 1. MR 指定

func TestBranchesGitHubMRListsItsSourceBranch(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	b.origin("https://github.com/acme/widgets.git")
	b.route(map[string]any{"GET " + githubAPI + "/pulls/5": pr(5, "feature/5-login", withMerged())})
	text := b.ok("", tok, "--mr", "5")
	wantContains(t, text, "ホスト: github.com acme/widgets を見た")
	found := candidates(text)
	wantKeys(t, found, "feature/5-login")
	wantContains(t, found["feature/5-login"], "在りか=ホストだけ  由来=MR  MR=!5(merged)")
}

func TestBranchesGitLabMRFromAForkIsMarked(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	b.origin("https://gitlab.com/acme/widgets.git")
	b.route(map[string]any{
		"GET " + gitlabAPI:                       obj{"id": 42},
		"GET " + gitlabAPI + "/merge_requests/9": mr(9, "feature-152-login", "opened", 77),
	})
	var data struct {
		Host       struct{ Checked bool } `json:"host"`
		Candidates []struct {
			Branch string `json:"branch"`
			Local  bool   `json:"local"`
			MRs    []struct {
				Fork bool `json:"fork"`
			} `json:"mrs"`
		} `json:"candidates"`
	}
	if err := json.Unmarshal([]byte(b.ok("", glTok, "--mr", "9", "--json")), &data); err != nil {
		t.Fatal(err)
	}
	if !data.Host.Checked || len(data.Candidates) != 1 {
		t.Fatalf("data = %+v", data)
	}
	only := data.Candidates[0]
	if only.Branch != "feature-152-login" || !only.Local || len(only.MRs) == 0 || !only.MRs[0].Fork {
		t.Errorf("候補 = %+v", only)
	}
}

// ---- 2. issue 指定

func TestBranchesGitHubIssueListsReferringPullRequestsAndLocalNames(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	b.origin("https://github.com/acme/widgets.git")
	pulls := []obj{
		pr(11, "topic/a", withBody("Closes #152")),
		pr(12, "topic/b", withTitle("fix #1520")),
		pr(13, "fix-152-x"),
		pr(14, "topic/c", withBody("see https://github.com/acme/widgets/issues/152"), withFork("o/w")),
		pr(15, "topic/d", withBody("&#152; は文字参照")),
		pr(16, "claude/tender-volta-qmy152q"),
		pr(17, "topic/i152"),
		pr(18, "feature_152_x"),
		pr(19, "feature-1520-x"),
		pr(20, "topic/152a"),
	}
	b.route(map[string]any{"GET " + githubAPI + "/pulls?state=open&per_page=100&page=1": pulls})
	text := b.ok("", tok, "--issue", "152")
	found := candidates(text)
	wantKeys(t, found, "feature-152-login", "feature_152_x", "fix-152-x", "topic/a", "topic/c")
	wantContains(t, found["topic/a"], "由来=MR  MR=!11(open)")
	wantContains(t, found["topic/c"], "MR=!14(open,フォーク)")
	wantContains(t, found["feature-152-login"], "在りか=手元  由来=名前に番号")
}

func TestBranchesGitLabIssueReadsRelatedMergeRequests(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	b.origin("https://gitlab.com/acme/widgets.git")
	b.route(map[string]any{
		"GET " + gitlabAPI: obj{"id": 42},
		"GET " + gitlabAPI + "/issues/152/related_merge_requests?per_page=100&page=1": []obj{mr(3, "feat-a", "opened", 42), mr(4, "feat-b", "merged", 42)},
	})
	wantKeys(t, candidates(b.ok("", glTok, "--issue", "152")), "feat-a", "feature-152-login")
}

// ---- 3. ホストに繋げない

func TestBranchesWithoutATokenOnlyLocalCandidatesAndSaysSo(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	b.origin("https://github.com/acme/widgets.git")
	text := b.ok("", nil, "--issue", "152")
	wantContains(t, text, "ホストは見ていない（gh が github.com で利用できず")
	wantContains(t, text, "GITHUB_TOKEN もありません")
	wantKeys(t, candidates(text), "feature-152-login")
}

func TestBranchesWithoutOriginSaysSo(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	text := b.ok("", nil, "--issue", "152")
	wantContains(t, text, "ホストは見ていない（origin がありません）")
	wantKeys(t, candidates(text), "feature-152-login")
}

func TestBranchesAFailedAPICallSaysWhichOne(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	b.origin("https://github.com/acme/widgets.git")
	text := b.ok("", tok, "--mr", "6")
	wantContains(t, text, "ホストは見ていない（ホストの API が失敗した（GET repos/acme/widgets/pulls/6）")
	wantContains(t, text, "候補なし")
	if strings.Contains(text, "t0k") {
		t.Error("トークンが出力に出ている")
	}
}

func TestBranchesCredentialsInOriginAreHidden(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	b.origin("https://oauth2:glpat-secret@gitlab.com/acme/widgets.git")
	r := b.sh("", nil, "--issue", "152")
	if r.Code != 0 || strings.Contains(r.Stdout+r.Stderr, "glpat-secret") {
		t.Errorf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

// ---- 4. プロジェクト

func TestBranchesInsideAProjectLooksAtItsRepository(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	web := filepath.Join(b.ws, "projects", "web")
	seedRepo(t, web, "main")
	git(t, web, "branch", "feature-152-web")
	text := b.ok(web, nil, "--issue", "152")
	wantContains(t, text, "プロジェクト web")
	wantKeys(t, candidates(text), "feature-152-web")
}

// ---- 5. 形と引数

func TestBranchesBadArgumentsExit2(t *testing.T) {
	t.Parallel()
	for _, args := range [][]string{nil, {"--issue"}, {"--issue", "0"}, {"--mr", "x"}, {"--issue", "1", "--mr", "2"}, {"--pr", "1"}} {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			t.Parallel()
			if r := newBranchesWS(t).sh("", nil, args...); r.Code != 2 {
				t.Errorf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
			}
		})
	}
}

func TestBranchesHelp(t *testing.T) {
	t.Parallel()
	r := newBranchesWS(t).sh("", nil, "--help")
	if r.Code != 0 {
		t.Fatalf("code = %d", r.Code)
	}
	wantContains(t, r.Stdout, "--issue <番号>")
}

// ワークスペースの外の git でないディレクトリから、ワークスペースを指して打つと断る。
func TestBranchesOutsideGitIsRefused(t *testing.T) {
	t.Parallel()
	b := newBranchesWS(t)
	bare := base.TempDir(t)
	r := b.sh(bare, map[string]string{"CCNAVI_WORKSPACE": b.ws}, "--issue", "1")
	if r.Code != 1 {
		t.Errorf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	wantContains(t, r.Stderr, "git のリポジトリの中ではありません")
}
