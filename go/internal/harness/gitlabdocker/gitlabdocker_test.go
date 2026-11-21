package gitlabdocker

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/gitlabapi"
)

// fakeDocker は docker の呼び出しを記録して答える。本物の docker も GitLab も使わない。
type fakeDocker struct {
	calls  [][]string
	stdins []string
	// 状態。
	memory        string
	image, volume bool
	state         string // コンテナの状態（空ならコンテナ無し）
	healthy       bool
	created       string // docker exec の gitlab-rails runner - が出す内容
}

func (f *fakeDocker) run(args []string, o RunOpts) Result {
	f.calls = append(f.calls, args)
	f.stdins = append(f.stdins, o.Stdin)
	ok := Result{}
	switch {
	case args[1] == "version":
		return Result{Stdout: "27.0"}
	case args[1] == "info":
		return Result{Stdout: f.memory}
	case args[1] == "image":
		return codeOf(f.image)
	case args[1] == "volume":
		return codeOf(f.volume)
	case args[1] == "inspect" && strings.Contains(strings.Join(args, " "), "State.Status"):
		if f.state == "" {
			return Result{Code: 1}
		}
		if f.healthy {
			return Result{Stdout: f.state + " healthy"}
		}
		return Result{Stdout: f.state + " starting"}
	case args[1] == "exec" && strings.Contains(strings.Join(args, " "), "puts :ok"):
		return Result{Stdout: "ok"}
	case args[1] == "exec":
		return Result{Stdout: f.created}
	}
	return ok
}

func codeOf(ok bool) Result {
	if ok {
		return Result{}
	}
	return Result{Code: 1}
}

func (f *fakeDocker) called(sub ...string) bool { return f.count(sub...) > 0 }

func (f *fakeDocker) count(sub ...string) int {
	n := 0
	for _, c := range f.calls {
		if strings.Contains(strings.Join(c, " "), strings.Join(sub, " ")) {
			n++
		}
	}
	return n
}

// composeEnv は最後の compose 呼び出しに渡した環境の値。
type fakeAPI struct{ valid func(token string) int }

func (f fakeAPI) User(token string) (int, any) { return f.valid(token), map[string]any{} }
func (f fakeAPI) Version(string) (int, any)    { return 200, map[string]any{"version": "19.4.1"} }

type rig struct {
	tool *Tool
	d    *fakeDocker
	out  *bytes.Buffer
	err  *bytes.Buffer
	api  *fakeAPI
	envs []string
}

func newRig(t *testing.T) *rig {
	t.Helper()
	cache := base.TempDir(t)
	r := &rig{d: &fakeDocker{memory: "10737418240", image: true}, out: &bytes.Buffer{}, err: &bytes.Buffer{}}
	r.api = &fakeAPI{valid: func(string) int { return 200 }}
	r.tool = &Tool{
		Root: base.Root(), Home: cache,
		Getenv:  func(name string) string { return map[string]string{"XDG_CACHE_HOME": cache}[name] },
		Environ: []string{"A=1"},
		Run: func(args []string, o RunOpts) Result {
			if args[1] == "compose" {
				r.envs = o.Env
			}
			return r.d.run(args, o)
		},
		NewClient: func(string) Client { return r.api },
		Out:       r.out, Err: r.err,
		Sleep: func(time.Duration) {}, Wait: 1, Poll: 1,
	}
	return r
}

func (r *rig) envValue(name string) string {
	for _, kv := range r.envs {
		if v, ok := strings.CutPrefix(kv, name+"="); ok {
			return v
		}
	}
	return ""
}

func (r *rig) writeTokens(t *testing.T, values map[string]string) {
	t.Helper()
	if err := WriteEnvFile(r.tool.TokensPath(), values, nil); err != nil {
		t.Fatal(err)
	}
}

func mode(t *testing.T, path string) os.FileMode {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode().Perm()
}

func TestUpWithoutTheImageStopsAndPullsOnlyWhenAsked(t *testing.T) {
	t.Parallel()
	r := newRig(t)
	r.d.image = false
	if rc := r.tool.Up(false, false); rc != 2 {
		t.Errorf("rc = %d、2 のはず", rc)
	}
	if r.d.called("pull") || r.d.called("compose") {
		t.Errorf("--pull なしで取得か起動をした: %v", r.d.calls)
	}
	if !strings.Contains(r.out.String(), "up --pull") {
		t.Errorf("案内が無い: %q", r.out.String())
	}
	// --pull を付けたときだけ取る（起動は待ちが終わっても応答せず 1 で終わる）
	if rc := r.tool.Up(true, false); rc != 1 {
		t.Errorf("rc = %d、1 のはず", rc)
	}
	if r.d.count("pull "+Image) != 1 {
		t.Errorf("pull が 1 回でない: %v", r.d.calls)
	}
}

func TestDockerMemoryWarning(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name, memory string
		warn         bool
	}{
		{"少ない", "4294967296", true},
		{"足りる", "10737418240", false},
	} {
		r := newRig(t)
		r.d.memory, r.d.image = tc.memory, false
		if rc := r.tool.Up(false, false); rc != 2 {
			t.Errorf("%s: rc = %d、警告のあともイメージが無ければ 2 で止まる", tc.name, rc)
		}
		has := strings.Contains(r.out.String(), "次: Docker に割り当てたメモリが 4.0GB")
		if has != tc.warn || (!tc.warn && strings.Contains(r.out.String(), "メモリ")) {
			t.Errorf("%s: 警告の有無が違う: %q", tc.name, r.out.String())
		}
	}
}

func TestUpPassword(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name    string
		state   string
		volume  bool
		stored  string
		wantNew bool
	}{
		{"止まっているコンテナ（exited）は作り直さない", "exited", false, "keep-me", false},
		{"down のあと（コンテナ無し・ボリュームあり）は作り直さない", "", true, "keep-me", false},
		{"ボリュームもコンテナも無ければ作って書く", "", false, "", true},
	} {
		r := newRig(t)
		r.d.state, r.d.volume = tc.state, tc.volume
		if tc.stored != "" {
			if err := WriteEnvFile(r.tool.PasswordPath(), map[string]string{"GITLAB_ROOT_PASSWORD": tc.stored}, nil); err != nil {
				t.Fatal(err)
			}
		}
		r.tool.Up(false, false)
		got := r.envValue("GITLAB_ROOT_PASSWORD")
		stored := gitlabapi.ReadEnvFile(r.tool.PasswordPath())["GITLAB_ROOT_PASSWORD"]
		if tc.wantNew {
			if len(stored) != 48 || stored != got {
				t.Errorf("%s: 保存 %q・compose に渡した %q", tc.name, stored, got)
			}
			continue
		}
		if got != "keep-me" || stored != "keep-me" {
			t.Errorf("%s: 保存 %q・compose に渡した %q", tc.name, stored, got)
		}
	}
}

func TestComposeGetsPortImageAndMemoryFromTheTool(t *testing.T) {
	t.Parallel()
	r := newRig(t)
	r.tool.Getenv = func(name string) string { return map[string]string{"CCNAVI_GITLAB_PORT": "9123"}[name] }
	r.tool.Up(false, false)
	for name, want := range map[string]string{"GITLAB_PORT": "9123", "GITLAB_IMAGE": Image, "GITLAB_MEM_LIMIT": MemLimit} {
		if got := r.envValue(name); got != want {
			t.Errorf("%s = %q、%q のはず", name, got, want)
		}
	}
	if got := r.tool.Base(); got != "http://127.0.0.1:9123" {
		t.Errorf("Base = %q", got)
	}
	// compose.yml は値を持たず、道具が渡す（ポートを 1 か所で決める）
	data, err := os.ReadFile(filepath.Join(base.Root(), "tests", "manual", "gitlab", "compose.yml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	if !strings.Contains(text, "${GITLAB_PORT}") || !strings.Contains(text, "${GITLAB_MEM_LIMIT:?") || strings.Contains(text, "8929") {
		t.Error("compose.yml がポートとメモリを環境変数で受けていない")
	}
}

func TestPortDefaultsWhenTheSettingIsUnreadable(t *testing.T) {
	t.Parallel()
	for value, want := range map[string]int{"": 8929, "abc": 8929, "0": 8929, "9123": 9123} {
		if got := Port(func(string) string { return value }); got != want {
			t.Errorf("%q: %d、%d のはず", value, got, want)
		}
	}
}

func TestStateFilesAre0600(t *testing.T) {
	t.Parallel()
	if runtime.GOOS == "windows" {
		t.Skip("Windows にモードの 0600 は無い")
	}
	r := newRig(t)
	path := r.tool.PasswordPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	// 既にあるファイルも 0600 に直す
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := WriteEnvFile(path, map[string]string{"GITLAB_ROOT_PASSWORD": "x"}, nil); err != nil {
		t.Fatal(err)
	}
	if got := mode(t, path); got != 0o600 {
		t.Errorf("モード = %o", got)
	}
}

const made = "GITLAB_TOKEN=glpat-new\nCCNAVI_PROBE_REVIEWER_TOKEN=glpat-rev-new\nCCNAVI_PROBE_REVIEWER_ID=3\nCCNAVI_PROBE_ROOT_ID=1\n"

func TestTokensAreReusedUnlessRevokedOrRotated(t *testing.T) {
	t.Parallel()
	old := map[string]string{"GITLAB_TOKEN": "glpat-old", "CCNAVI_PROBE_REVIEWER_TOKEN": "glpat-rev-old"}
	for _, tc := range []struct {
		name    string
		status  int
		rotate  bool
		remakes bool
	}{
		{"有効なら作り直さない", 200, false, false},
		{"一時的な不通（0）は失効とみなさない", 0, false, false},
		{"一時的な不通（502）は失効とみなさない", 502, false, false},
		{"一時的な不通（503）は失効とみなさない", 503, false, false},
		{"401 は --rotate なしでも作り直す", 401, false, true},
		{"--rotate は有効でも作り直す", 200, true, true},
	} {
		r := newRig(t)
		r.writeTokens(t, old)
		r.api.valid = func(string) int { return tc.status }
		r.d.created = made
		tokens := r.tool.ensureTokens(r.api, tc.rotate)
		remade := r.d.called("gitlab-rails runner -")
		if remade != tc.remakes {
			t.Errorf("%s: 作り直した = %v", tc.name, remade)
		}
		want := "glpat-old"
		if tc.remakes {
			want = "glpat-new"
		}
		if tokens["GITLAB_TOKEN"] != want || gitlabapi.ReadEnvFile(r.tool.TokensPath())["GITLAB_TOKEN"] != want {
			t.Errorf("%s: トークン %v", tc.name, tokens)
		}
		if tc.remakes && runtime.GOOS != "windows" && mode(t, r.tool.TokensPath()) != 0o600 {
			t.Errorf("%s: 新しいトークンのファイルが 0600 でない", tc.name)
		}
	}
}

func TestStatusNeverPrintsTokenValues(t *testing.T) {
	t.Parallel()
	r := newRig(t)
	r.d.state, r.d.healthy = "running", true
	r.writeTokens(t, map[string]string{"GITLAB_TOKEN": "glpat-secret-1", "CCNAVI_PROBE_REVIEWER_TOKEN": "glpat-secret-2"})
	if rc := r.tool.Status(); rc != 0 {
		t.Fatalf("rc = %d", rc)
	}
	text := r.out.String()
	if strings.Contains(text, "glpat-secret") || !strings.Contains(text, "有効") {
		t.Errorf("出力: %q", text)
	}
	r.api.valid = func(string) int { return 401 }
	r.out.Reset()
	r.tool.Status()
	if !strings.Contains(r.out.String(), "無効") {
		t.Errorf("失効を言わない: %q", r.out.String())
	}
}

func TestDownPurgeRemovesTheVolumeAndTheStateFiles(t *testing.T) {
	t.Parallel()
	r := newRig(t)
	r.writeTokens(t, map[string]string{"GITLAB_TOKEN": "y"})
	if err := WriteEnvFile(r.tool.PasswordPath(), map[string]string{"GITLAB_ROOT_PASSWORD": "x"}, nil); err != nil {
		t.Fatal(err)
	}
	if rc := r.tool.Down(false); rc != 0 || r.d.called("down -v") {
		t.Fatalf("--purge なし: rc = %d、%v", rc, r.d.calls)
	}
	if _, err := os.Stat(r.tool.TokensPath()); err != nil {
		t.Error("--purge なしで状態のファイルを消した")
	}
	if rc := r.tool.Down(true); rc != 0 || !r.d.called("down -v") {
		t.Fatalf("--purge: rc = %d、%v", rc, r.d.calls)
	}
	for _, p := range []string{r.tool.PasswordPath(), r.tool.TokensPath()} {
		if _, err := os.Stat(p); err == nil {
			t.Errorf("%s が残っている", p)
		}
	}
}

func TestNothingRunsWithoutDocker(t *testing.T) {
	t.Parallel()
	r := newRig(t)
	r.tool.Run = func([]string, RunOpts) Result { return Result{Code: 127} }
	for name, rc := range map[string]int{"up": r.tool.Up(false, false), "status": r.tool.Status(), "down": r.tool.Down(false)} {
		if rc != 2 {
			t.Errorf("%s: rc = %d、2 のはず", name, rc)
		}
	}
}
