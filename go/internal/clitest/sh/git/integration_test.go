package git

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// ccnavi-common-state.sh の ccnavi_integration（統合先を実行ファイルに聞く口）。決め方（origin/HEAD → origin/main・origin/master）と
// 案内の文は実行ファイル（`git integration`）が持ち、go test（integrate の integration_test.go）が見る。
// ここでは通しで、sh が答えを読めること、ワークツリーとプロジェクトがそれぞれ自分のリポジトリで聞くこと、
// 実行ファイルが無いときに「分からない」になることを見る。

// integrationHarness は、保護済み sh と同じく入口の `$0` のディレクトリから部品を読み、第 1 引数の関数を第 2 引数の木で呼ぶ。
const integrationHarness = `. "$(dirname "$0")/ccnavi-common.sh"
case "$1" in
hint) ccnavi_integration_hint "$2" ;;
*) ccnavi_integration "$2" ;;
esac
`

type integrationWS struct {
	t       *testing.T
	ws      string
	harness string
}

func newIntegrationWS(t *testing.T) *integrationWS {
	t.Helper()
	ws := base.TempDir(t)
	scripts := copyScripts(t, ws)
	h := write(t, filepath.Join(scripts, "integration-harness.sh"), integrationHarness)
	seedRepo(t, ws, "work")
	return &integrationWS{t: t, ws: ws, harness: h}
}

func (w *integrationWS) call(mode, tree string, env map[string]string) run.Result {
	w.t.Helper()
	merged := map[string]string{"CCNAVI_ENTRY_PATH": exe.Path(w.t)}
	for k, v := range env {
		merged[k] = v
	}
	return run.Sh(w.t, w.harness, []string{mode, tree}, run.Opts{Dir: tree, Env: merged})
}

// remoteRef は origin/<名前> を手元に置く（取ってきた後の形）。
func remoteRef(t *testing.T, repo, name string) {
	t.Helper()
	git(t, repo, "update-ref", "refs/remotes/origin/"+name, "HEAD")
}

func pointOriginHead(t *testing.T, repo, name string) {
	t.Helper()
	remoteRef(t, repo, name)
	git(t, repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/"+name)
}

// origin/HEAD も origin/main・master も無ければ当てずっぽうで宣言せず、終了コード 1 で何も出さない。実行ファイルが無いときも同じ。
func TestIntegrationUnknownIsExit1(t *testing.T) {
	t.Parallel()
	need(t, "sh", "git")
	for _, tc := range []struct {
		name      string
		missingEx bool
	}{{"既定", false}, {"実行ファイルが無い", true}} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			w := newIntegrationWS(t)
			git(t, w.ws, "branch", "-m", "main")
			var env map[string]string
			if tc.missingEx {
				env = map[string]string{"CCNAVI_ENTRY_PATH": filepath.Join(w.ws, "no-such-bin")}
			}
			r := w.call("integration", w.ws, env)
			if r.Code != 1 || r.Stdout != "" {
				t.Errorf("code = %d, stdout = %q", r.Code, r.Stdout)
			}
		})
	}
}

// ワークツリーは元リポジトリの参照を読み、プロジェクト（projects/<名前>）は自分の origin を読む。
func TestIntegrationAWorktreeAndAProjectEachReadTheirOwnRepository(t *testing.T) {
	t.Parallel()
	need(t, "sh", "git")
	w := newIntegrationWS(t)
	tree := filepath.Join(w.ws, ".claude", "worktrees", "i0001")
	git(t, w.ws, "worktree", "add", "-q", tree, "-b", "i0001")
	project := filepath.Join(w.ws, "projects", "app")
	seedRepo(t, project, "work")
	pointOriginHead(t, project, "trunk")
	pointOriginHead(t, w.ws, "main")
	for tree, want := range map[string]string{tree: "main", project: "trunk"} {
		r := w.call("integration", tree, nil)
		if r.Code != 0 || strings.TrimSpace(r.Stdout) != want {
			t.Errorf("%s: code = %d, stdout = %q, stderr = %q, want %q", tree, r.Code, r.Stdout, r.Stderr, want)
		}
	}
}

// 案内は、origin が無いときと、origin/HEAD が決まらないときで言い分ける。
func TestIntegrationHintTellsApartNoOriginAndNoOriginHead(t *testing.T) {
	t.Parallel()
	need(t, "sh", "git")
	w := newIntegrationWS(t)
	r := w.call("hint", w.ws, nil)
	if r.Code != 0 || !strings.Contains(r.Stdout, "origin が無いので統合先が決まらない") {
		t.Errorf("origin が無い: code = %d, stdout = %q, stderr = %q", r.Code, r.Stdout, r.Stderr)
	}
	git(t, w.ws, "remote", "add", "origin", "https://example.invalid/x.git")
	r = w.call("hint", w.ws, nil)
	for _, want := range []string{"統合先 (origin/HEAD) が決まらない", "git remote set-head origin -a"} {
		if !strings.Contains(r.Stdout, want) {
			t.Errorf("origin/HEAD が無い: stdout に %q が無い: %q", want, r.Stdout)
		}
	}
}
