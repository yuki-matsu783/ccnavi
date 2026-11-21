package host

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/ospath"
)

// bareHost は bare を用意し、スタブを効かせた環境つきで返す。
type bareHost struct {
	m   *Manager
	w   workspace
	h   *Host
	env map[string]string
	t   *testing.T
}

func prepareHost(t *testing.T, kind Kind, state map[string]any) *bareHost {
	t.Helper()
	m, _ := manager(t)
	w := newWorkspace(t)
	h, err := m.Prepare(w.root, w.target, Setup{Kind: kind, State: state}, "")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { m.Teardown(w.root, h) })
	return &bareHost{m: m, w: w, h: h, env: h.StepEnv(os.Environ(), ""), t: t}
}

// stubbed はスタブの置き場を先頭にした PATH から探した program のパス（exec.Command は親の PATH で探すので、自分で探す）。
func stubbed(t *testing.T, env map[string]string, program string) string {
	t.Helper()
	if ospath.Windows {
		t.Skip("拡張子の無いスタブは sh からしか見えない（Windows では直に起こさない）")
	}
	for _, dir := range filepath.SplitList(env["PATH"]) {
		if p := filepath.Join(dir, program); isExecutable(p) {
			return p
		}
	}
	t.Fatalf("%s が PATH に無い", program)
	return ""
}

func isExecutable(p string) bool {
	info, err := os.Stat(p)
	return err == nil && !info.IsDir() && info.Mode()&0o111 != 0
}

func (b *bareHost) git(dir string, args ...string) run.Result {
	b.t.Helper()
	return run.Command(b.t, stubbed(b.t, b.env, "git"), args, run.Opts{Dir: dir, Env: b.env})
}

func refsOf(t *testing.T, dir string) string {
	return run.Git(t, dir, "for-each-ref", "--format=%(refname)")
}

func TestBareStubAnswersAHostShapedOriginForTheTargetOnly(t *testing.T) {
	t.Parallel()
	b := prepareHost(t, Bare, nil)
	wt := filepath.Join(filepath.Dir(b.w.root), "wt")
	run.Git(t, b.w.target, "worktree", "add", "-q", wt, "-b", "w")
	for _, dir := range []string{b.w.target, wt} {
		if got := strings.TrimSpace(b.git(dir, "remote", "get-url", "origin").Stdout); got != hoststub.OriginURL {
			t.Errorf("%s: origin = %q", dir, got)
		}
	}
	if got := strings.TrimSpace(b.git(b.w.target, "-C", b.w.target, "remote", "get-url", "origin").Stdout); got != hoststub.OriginURL {
		t.Errorf("-C で指しても対象は同じ: %q", got)
	}
	// ccnavi 本体のリポジトリには、本物の答えを返す
	for _, args := range [][]string{{"remote", "get-url", "origin"}, {"-C", b.w.root, "remote", "get-url", "origin"}} {
		dir := b.w.root
		if args[0] == "-C" {
			dir = b.w.target
		}
		if got := strings.TrimSpace(b.git(dir, args...).Stdout); got != bodyOriginURL {
			t.Errorf("本体の origin = %q（%v）", got, args)
		}
	}
}

func TestBarePushAndFetchReachTheBareNotTheRealOrigin(t *testing.T) {
	t.Parallel()
	b := prepareHost(t, Bare, nil)
	run.Git(t, b.w.target, "switch", "-q", "-c", "feat")
	if err := os.WriteFile(filepath.Join(b.w.target, "new.txt"), []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	run.Git(t, b.w.target, "add", "new.txt")
	run.Git(t, b.w.target, "commit", "-q", "-m", "new")
	if r := b.git(b.w.target, "push", "-q", "origin", "feat"); r.Code != 0 {
		t.Fatalf("push: %s", r.Stderr)
	}
	sha := run.Git(t, b.w.target, "rev-parse", "feat")
	if got := run.Git(t, b.h.Bare, "rev-parse", "refs/heads/feat"); got != sha {
		t.Errorf("bare の feat = %s, want %s", got, sha)
	}
	if r := b.git(b.w.target, "fetch", "-q", "origin"); r.Code != 0 {
		t.Errorf("fetch: %s", r.Stderr)
	}
	if listed := b.git(b.w.target, "ls-remote", "origin").Stdout; !strings.Contains(listed, sha+"\trefs/heads/feat") {
		t.Errorf("ls-remote = %q", listed)
	}
	// .git/config の origin は書き換えない
	if got := run.Git(t, b.w.target, "config", "remote.origin.url"); got != realOriginURL {
		t.Errorf("remote.origin.url = %q", got)
	}
}

// 利用者の url.*.insteadOf があっても、bare へ向ける -c には設定の生の値を使うので、本物の origin へは出ない。
func TestUsersInsteadOfDoesNotSendTrafficToTheRealOrigin(t *testing.T) {
	t.Parallel()
	m, tmp := manager(t)
	w := newWorkspace(t)
	config := filepath.Join(tmp, "global.gitconfig")
	if err := os.WriteFile(config, []byte("[url \"git@example.invalid:\"]\n\tinsteadOf = https://github.com/\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	userEnv := map[string]string{"GIT_CONFIG_GLOBAL": config}
	userGit := func(args ...string) string {
		r := run.Command(t, "git", args, run.Opts{Dir: w.target, Env: userEnv})
		return strings.TrimSpace(r.Stdout)
	}
	if got := userGit("remote", "get-url", "origin"); !strings.HasPrefix(got, "git@example.invalid") {
		t.Fatalf("前提: insteadOf が効いていない: %q", got)
	}
	m.Git = func(args []string, o GitOpts) GitResult {
		o.Env = map[string]string{"GIT_CONFIG_GLOBAL": config}
		return gitFunc(t)(args, o)
	}
	h, err := m.Prepare(w.root, w.target, Setup{Kind: Bare}, "")
	if err != nil {
		t.Fatal(err)
	}
	defer m.Teardown(w.root, h)
	if h.RealOrigin != realOriginURL {
		t.Fatalf("RealOrigin = %q", h.RealOrigin)
	}
	run.Git(t, w.target, "switch", "-q", "-c", "feat")
	run.Git(t, w.target, "commit", "-q", "--allow-empty", "-m", "e")
	env := h.StepEnv(os.Environ(), "")
	env["GIT_CONFIG_GLOBAL"] = config
	r := run.Command(t, stubbed(t, env, "git"), []string{"push", "-q", "origin", "feat"}, run.Opts{Dir: w.target, Env: env})
	if r.Code != 0 {
		t.Fatalf("push: %s", r.Stderr)
	}
	if got, want := run.Git(t, h.Bare, "rev-parse", "refs/heads/feat"), run.Git(t, w.target, "rev-parse", "feat"); got != want {
		t.Errorf("bare の feat = %s, want %s", got, want)
	}
}

func TestBareIsMadeFromTheOriginRemoteRefs(t *testing.T) {
	t.Parallel()
	m, _ := manager(t)
	w := newWorkspace(t)
	run.Git(t, w.target, "update-ref", "refs/remotes/origin/main", w.base)
	run.Git(t, w.target, "update-ref", "refs/remotes/origin/side", w.base)
	h, err := m.Prepare(w.root, w.target, Setup{Kind: Bare}, "")
	if err != nil {
		t.Fatal(err)
	}
	defer m.Teardown(w.root, h)
	heads := run.Git(t, h.Bare, "for-each-ref", "--format=%(refname)", "refs/heads")
	if heads != "refs/heads/main\nrefs/heads/side" {
		t.Errorf("heads = %q", heads)
	}
	if head := run.Git(t, h.Bare, "symbolic-ref", "HEAD"); head != "refs/heads/main" {
		t.Errorf("HEAD = %q", head)
	}
}

func TestBareStubsApiToolsFailAndStubsCurl(t *testing.T) {
	t.Parallel()
	b := prepareHost(t, Bare, nil)
	for _, tool := range []string{"gh", "glab"} {
		if r := run.Command(t, stubbed(t, b.env, tool), []string{"x"}, run.Opts{Env: b.env}); r.Code != 1 {
			t.Errorf("%s: code = %d", tool, r.Code)
		}
	}
	if _, err := os.Stat(filepath.Join(b.h.Bin, "curl")); err != nil {
		t.Error("bare は curl のスタブを置く")
	}
	if r := run.Command(t, stubbed(t, b.env, "curl"), []string{"-s", "https://api.github.com/x"}, run.Opts{Env: b.env}); r.Code != 22 {
		t.Errorf("空の routes の curl: code = %d, want 22", r.Code)
	}
}

// 配る ccnavi-git.sh（ラッパー）が打つ git も、スタブを通る。
func TestWrapperGitShGoesThroughTheStubToo(t *testing.T) {
	t.Parallel()
	b := prepareHost(t, Bare, nil)
	scripts := filepath.Join(b.w.target, ".ccnavi", "scripts")
	if err := os.MkdirAll(scripts, 0o755); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(base.ScriptsDir())
	for _, e := range entries {
		if n := e.Name(); strings.HasPrefix(n, "ccnavi-") && strings.HasSuffix(n, ".sh") {
			data, err := os.ReadFile(filepath.Join(base.ScriptsDir(), n))
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(scripts, n), data, 0o755); err != nil {
				t.Fatal(err)
			}
		}
	}
	run.Git(t, b.w.target, "switch", "-q", "-c", "feat")
	b.env["CCNAVI_ENTRY_PATH"] = exeShPath(t)
	r := run.Command(t, "sh", []string{filepath.Join(scripts, "ccnavi-git.sh"), "fetch", "origin"}, run.Opts{Dir: b.w.target, Env: b.env})
	if r.Code != 0 {
		t.Errorf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func TestTeardownRemovesThePlaceAndPutsTheRemoteRefsBack(t *testing.T) {
	t.Parallel()
	m, _ := manager(t)
	w := newWorkspace(t)
	run.Git(t, w.target, "update-ref", "refs/remotes/origin/main", w.base)
	h, err := m.Prepare(w.root, w.target, Setup{Kind: Bare}, "")
	if err != nil {
		t.Fatal(err)
	}
	env := h.StepEnv(os.Environ(), "")
	run.Git(t, w.target, "switch", "-q", "-c", "feat")
	run.Git(t, w.target, "commit", "-q", "--allow-empty", "-m", "e")
	if r := run.Command(t, stubbed(t, env, "git"), []string{"push", "-q", "origin", "feat"}, run.Opts{Dir: w.target, Env: env}); r.Code != 0 {
		t.Fatal(r.Stderr)
	}
	if !strings.Contains(refsOf(t, w.target), "refs/remotes/origin/feat") {
		t.Fatal("前提: push で remote-tracking ref が増える")
	}
	if failed := m.Teardown(w.root, h); failed {
		t.Error("bare の後片付けは失敗しない")
	}
	if _, err := os.Stat(h.Dir); !os.IsNotExist(err) {
		t.Errorf("置き場が残っている: %v", err)
	}
	if refs := refsOf(t, w.target); strings.Contains(refs, "origin/feat") {
		t.Errorf("増えた ref が残っている: %s", refs)
	}
	if got := run.Git(t, w.target, "rev-parse", "refs/remotes/origin/main"); got != w.base {
		t.Errorf("origin/main = %s, want %s", got, w.base)
	}
	m.Teardown(w.root, h) // 2 回目は何もしない
}

func TestATargetWithoutOriginIsRefusedAndLeavesNothing(t *testing.T) {
	t.Parallel()
	m, tmp := manager(t)
	w := newWorkspace(t)
	run.Git(t, w.target, "remote", "remove", "origin")
	if _, err := m.Prepare(w.root, w.target, Setup{Kind: Bare}, ""); err == nil {
		t.Fatal("origin の無い対象を受けた")
	}
	if left, _ := filepath.Glob(filepath.Join(tmp, "ccnavi-host-*")); len(left) != 0 {
		t.Errorf("置き場が残った: %v", left)
	}
}
