package review

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/hostfixture"
	"ccnavi/internal/harness/run"
)

// hostSite は、録ったホストの応答の見本（hostfixture）を返す curl を PATH の先頭に置いたワークスペース。
// 実行ファイルは writeStub の代わりの sh（受けた引数を stub.Args に溜める）。
type hostSite struct {
	*site
	kind    hostfixture.Host
	bin     string
	stub    stubDir
	origin  string
	token   string
	tokenIs string // トークンの環境変数の名前
}

const (
	githubOrigin = "https://github.com/acme/widgets.git"
	gitlabOrigin = "https://gitlab.com/acme/widgets.git"
)

func newHostSite(t *testing.T, kind hostfixture.Host) hostSite {
	t.Helper()
	h := hostSite{kind: kind}
	if kind == hostfixture.GitHub {
		h.origin, h.token, h.tokenIs = githubOrigin, "t0k", "GITHUB_TOKEN"
	} else {
		h.origin, h.token, h.tokenIs = gitlabOrigin, "glpat-t0k", "GITLAB_TOKEN"
	}
	h.site = newSite(t, h.origin, reviewScripts...)
	if kind == hostfixture.GitLab {
		h.git("update-ref", "refs/remotes/origin/main", "HEAD")
	}
	h.bin = filepath.Join(h.tmp, "bin")
	hostfixture.Install(t, h.bin, kind)
	h.stub = writeStub(t, h.tmp)
	return h
}

// env は 1 回の起動の環境（見本の場面・トークン・PATH・実行ファイルの代わりの sh）に、extra を重ねたもの。
func (h hostSite) env(scene string, extra map[string]string) map[string]string {
	env := hostfixture.Env(h.kind, scene)
	env[h.tokenIs] = h.token
	env["PATH"] = pathWith(h.bin)
	env["CCNAVI_ENTRY_PATH"] = h.stub.Path
	if h.kind == hostfixture.GitHub {
		env["FAKE_GITHUB_STATE"] = filepath.Join(h.tmp, "comments.json")
	}
	for k, v := range extra {
		env[k] = v
	}
	return env
}

// mrURL は計画に書く MR の URL。
func (h hostSite) mrURL() string {
	if h.kind == hostfixture.GitHub {
		return "https://github.com/acme/widgets/pull/1"
	}
	return "https://gitlab.com/acme/widgets/-/merge_requests/1"
}

func (h hostSite) state() string { return filepath.Join(h.ws, "logs", "state") }

// plan は、nodes を全部受け入れる計画のファイル（実行ファイルのスタブが `--accept-plan` に写す）。
func (h hostSite) plan(nodes ...string) string {
	h.t.Helper()
	return keepPlan(h.t, h.tmp, h.state(), "test", h.mrURL(), 1, nodes...)
}

// review は ccnavi-review.sh を、decide（`--preview` なし）だけは ccnavi-user-decide.sh を起こす
// （置く形はユーザ専用の sh。どちらの sh がホストに同じ形で聞くかを、同じ場面で見るため）。
// decide で STUB_PLAN を渡さなければ、指摘の無い計画を使う。
func (h hostSite) review(scene string, extra map[string]string, args ...string) run.Result {
	h.t.Helper()
	return h.reviewIn(h.ws, scene, extra, args...)
}

func (h hostSite) reviewIn(cwd, scene string, extra map[string]string, args ...string) run.Result {
	h.t.Helper()
	script := reviewSh
	if len(args) > 0 && args[0] == "decide" && !contains(args, "--preview") {
		script, args = userDecideSh, args[1:]
	}
	if _, ok := extra["STUB_PLAN"]; !ok && script == userDecideSh {
		merged := map[string]string{"STUB_PLAN": h.plan()}
		for k, v := range extra {
			merged[k] = v
		}
		extra = merged
	}
	return h.sh(script, run.Opts{Env: h.env(scene, extra), Dir: cwd}, args...)
}

// sceneWith は scene を tmp の下にコピーした場面（見本の根からの相対の名前）を返し、user.json だけ login に替える。
func (h hostSite) sceneWith(from, login string) string {
	h.t.Helper()
	dst := filepath.Join(h.tmp, "odd")
	copyTree(h.t, hostfixture.SceneDir(h.kind, from), dst)
	write(h.t, filepath.Join(dst, "user.json"), `{"login": `+quoteJSON(login)+`}`)
	rel, err := filepath.Rel(filepath.Dir(hostfixture.SceneDir(h.kind, from)), dst)
	if err != nil { // Windows で別のドライブ
		h.t.Skip("見本の根から相対で指せない: " + err.Error())
	}
	return rel
}

func quoteJSON(s string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s) + `"`
}

func copyTree(t testing.TB, from, to string) {
	t.Helper()
	entries, err := os.ReadDir(from)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.IsDir() {
			copyTree(t, filepath.Join(from, e.Name()), filepath.Join(to, e.Name()))
			continue
		}
		write(t, filepath.Join(to, e.Name()), read(t, filepath.Join(from, e.Name())))
	}
}
