package hoststub

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

func needSh(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh が無い")
	}
}

// curl のスタブは routes.json の `<METHOD> <URL>` で答え、要求（本文も）を FAKE_LOG に残し、400 以上は curl の書き方で 22 で終わる。
func TestCurlStubAnswersFromTheRoutesAndLogsTheRequests(t *testing.T) {
	t.Parallel()
	needSh(t)
	stub := Build(t, base.TempDir(t), true)
	stub.WriteRoutes(t, Routes{
		"GET " + API + "/user":       {Status: 200, Body: map[string]any{"id": 7}},
		"POST " + Project + "/notes": {Status: 201, Body: map[string]any{"ok": true}},
		"GET " + API + "/forbidden":  {Status: 403, Body: map[string]any{"message": "no"}},
	})
	env := run.Opts{Env: stub.Env(EnvOpts{})}
	curl := filepath.Join(stub.BinDir, "curl")
	cases := []struct {
		name   string
		args   []string
		input  string
		code   int
		stdout string
		stderr string
	}{
		{"表にある", []string{"-fsS", "-X", "GET", "-H", "A: b", API + "/user"}, "", 0, `{"id":7}`, ""},
		{"本文を標準入力から受ける", []string{"-fsS", "-X", "POST", "--data-binary", "@-", Project + "/notes"}, "a\nb", 0, `{"ok":true}`, ""},
		{"400 以上は 22", []string{"-fsS", API + "/forbidden"}, "", 22, "", "returned error: 403"},
		{"表に無い要求は 404", []string{"-fsS", API + "/none"}, "", 22, "", "returned error: 404"},
	}
	for _, c := range cases {
		o := env
		o.Input = c.input
		r := run.Sh(t, curl, c.args, o)
		if r.Code != c.code || r.Stdout != c.stdout || !strings.Contains(r.Stderr, c.stderr) {
			t.Errorf("%s: code=%d stdout=%q stderr=%q", c.name, r.Code, r.Stdout, r.Stderr)
		}
	}
	log, err := os.ReadFile(stub.Log)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"GET " + API + "/user\n", "POST " + Project + "/notes\nBODY a b\n"} {
		if !strings.Contains(string(log), want) {
			t.Errorf("ログに %q が無い: %q", want, log)
		}
	}
}

func repoWithOrigin(t *testing.T, origin string) (dir, common string) {
	t.Helper()
	dir = base.TempDir(t)
	for _, args := range [][]string{{"init", "-q", dir}, {"-C", dir, "remote", "add", "origin", origin}} {
		cmd := exec.Command("git", args...)
		cmd.Env = run.Environment(run.Opts{})
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
	return dir, filepath.Join(dir, ".git")
}

// git のスタブは `remote get-url origin` だけホストの形の URL を答える。TargetCommon があれば、その共通の .git のリポジトリだけ
// （大文字小文字を区別せず比べる）。ほかは本物の git が答える。
func TestGitStubAnswersTheHostUrlOnlyForTheTarget(t *testing.T) {
	t.Parallel()
	needSh(t)
	stub := Build(t, base.TempDir(t), false)
	if _, err := os.Stat(filepath.Join(stub.BinDir, "curl")); err == nil {
		t.Error("curl=false なのに curl を置いた")
	}
	target, common := repoWithOrigin(t, "https://real.example/o/target.git")
	other, _ := repoWithOrigin(t, "https://real.example/o/other.git")
	cases := []struct {
		name string
		repo string
		opts EnvOpts
		want string
	}{
		{"対象を決めない", target, EnvOpts{}, OriginURL},
		{"対象のリポジトリ", target, EnvOpts{TargetCommon: common}, OriginURL},
		{"対象の大文字小文字違い", target, EnvOpts{TargetCommon: strings.ToUpper(common)}, OriginURL},
		{"対象でないリポジトリは本物が答える", other, EnvOpts{TargetCommon: common}, "https://real.example/o/other.git"},
		{"別の URL を答える", target, EnvOpts{OriginURL: "http://127.0.0.1:1/x.git"}, "http://127.0.0.1:1/x.git"},
	}
	for _, c := range cases {
		r := run.Sh(t, filepath.Join(stub.BinDir, "git"), []string{"-C", c.repo, "remote", "get-url", "origin"}, run.Opts{Env: stub.Env(c.opts)})
		if got := strings.TrimSpace(r.Stdout); got != c.want {
			t.Errorf("%s: stdout = %q (code %d, stderr %q), want %q", c.name, got, r.Code, r.Stderr, c.want)
		}
	}
}

// bare と本物の URL を渡すと、本物の origin への fetch が bare へ向く。
func TestGitStubRedirectsTheRealOriginToTheBare(t *testing.T) {
	t.Parallel()
	needSh(t)
	tmp := base.TempDir(t)
	stub := Build(t, tmp, false)
	bare := filepath.Join(tmp, "bare.git")
	repo, _ := repoWithOrigin(t, "https://real.example/o/r.git")
	for _, args := range [][]string{{"init", "-q", "--bare", bare}, {"-C", repo, "-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-q", "--allow-empty", "-m", "s"}} {
		cmd := exec.Command("git", args...)
		cmd.Env = run.Environment(run.Opts{})
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
	env := stub.Env(EnvOpts{Bare: bare, RealOrigin: "https://real.example/o/r.git"})
	r := run.Sh(t, filepath.Join(stub.BinDir, "git"), []string{"-C", repo, "push", "-q", "origin", "HEAD:refs/heads/main"}, run.Opts{Env: env})
	if r.Code != 0 {
		t.Fatalf("push = %d %s%s", r.Code, r.Stdout, r.Stderr)
	}
	cmd := exec.Command("git", "-C", bare, "rev-parse", "--verify", "refs/heads/main")
	cmd.Env = run.Environment(run.Opts{})
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Errorf("bare に届いていない: %v\n%s", err, out)
	}
}

// gh と glab は失敗する。
func TestGhAndGlabFail(t *testing.T) {
	t.Parallel()
	needSh(t)
	stub := Build(t, base.TempDir(t), false)
	for _, name := range []string{"gh", "glab"} {
		if r := run.Sh(t, filepath.Join(stub.BinDir, name), []string{"api"}); r.Code != 1 {
			t.Errorf("%s = %d", name, r.Code)
		}
	}
}

// BuildTo は testing 無しで git・gh・glab（curl 有りなら curl）と routes.json を置く。
func TestBuildToPlacesTheStubsWithoutTesting(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	s, err := BuildTo(dir, "/x/teststub", true)
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"git", "gh", "glab", "curl"} {
		if _, err := os.Stat(filepath.Join(s.BinDir, name)); err != nil {
			t.Errorf("%s が置かれていない: %v", name, err)
		}
	}
	if _, err := os.Stat(s.RoutesPath); err != nil {
		t.Errorf("routes.json が無い: %v", err)
	}
	s2, err := BuildTo(base.TempDir(t), "/x/teststub", false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(s2.BinDir, "curl")); err == nil {
		t.Error("curl=false なのに curl が置かれた")
	}
}
