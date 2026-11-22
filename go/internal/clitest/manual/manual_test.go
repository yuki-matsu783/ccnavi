package manual

import (
	"bufio"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/fakegitlab"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/ospath"
)

func manualExe(t *testing.T) string {
	t.Helper()
	return exe.ToolPath(t, "./internal/harness/cmd/gitlabmanual", "gitlabmanual")
}

// manualRun は gitlabmanual を、走った機械のトークンも状態も見えない環境で起こす。
func manualRun(t *testing.T, env map[string]string, args ...string) run.Result {
	t.Helper()
	home := base.TempDir(t)
	merged := map[string]string{"HOME": home, "USERPROFILE": home, "XDG_CACHE_HOME": home}
	for k, v := range env {
		merged[k] = v
	}
	return run.Command(t, manualExe(t), args, run.Opts{Dir: base.Root(), Env: merged, Unset: []string{"GITLAB_TOKEN", "CCNAVI_PROBE_REVIEWER_TOKEN"}})
}

func TestTheEntryAnswersHelpAndRefusesWhatItDoesNotKnow(t *testing.T) {
	t.Parallel()
	for name, tc := range map[string]struct {
		args []string
		code int
		want string
	}{
		"--help":        {[]string{"--help"}, 0, "使い方: gitlabmanual"},
		"引数なし":          {nil, 2, "使い方: gitlabmanual"},
		"知らないサブコマンド":    {[]string{"nope"}, 2, "知らないサブコマンド"},
		"probe --help":  {[]string{"probe", "--help"}, 0, "GITLAB_TOKEN"},
		"トークンが無ければ止まる":  {[]string{"probe"}, 2, "make_gitlab_tokens.rb"},
		"契約の録りもトークンが要る": {[]string{"record-contract", "--dry-run"}, 2, "トークンが無い"},
	} {
		r := manualRun(t, nil, tc.args...)
		if r.Code != tc.code || !strings.Contains(r.Stdout+r.Stderr, tc.want) {
			t.Errorf("%s: code = %d、%q %q", name, r.Code, r.Stdout, r.Stderr)
		}
	}
}

// トークンが無いとき、probe は置き場も作らない（CCNAVI_PROBE_OUT の下が空のまま）。
func TestProbeWithoutTokensCreatesNothing(t *testing.T) {
	t.Parallel()
	out := base.TempDir(t)
	r := manualRun(t, map[string]string{"CCNAVI_PROBE_OUT": out}, "probe")
	if r.Code != 2 {
		t.Fatalf("code = %d、%q", r.Code, r.Stderr)
	}
	if matches, _ := filepath.Glob(filepath.Join(out, "*")); len(matches) != 0 {
		t.Errorf("置き場を作った: %v", matches)
	}
}

var listening = regexp.MustCompile(`fake-gitlab listening on 127\.0\.0\.1:(\d+) state=`)

// fakegitlab: 起動（標準エラー 1 行）・1 往復（要求の記録 1 行）・停止（シグナルで終わる）。
func TestFakeGitlabStartsAnswersAndStops(t *testing.T) {
	t.Parallel()
	state := filepath.Join(base.TempDir(t), "state.json")
	if err := os.WriteFile(state, []byte(`{"project": "demo/greeter", "seq": 100, "issues": [], "mrs": []}`), 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe.ToolPath(t, "./internal/harness/cmd/fakegitlab", "fakegitlab"), state, "0")
	cmd.Env = run.Environment(run.Opts{})
	stderr, err := cmd.StderrPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()
	defer func() { _ = cmd.Process.Kill() }()

	lines := make(chan string, 16)
	go func() {
		sc := bufio.NewScanner(stderr)
		for sc.Scan() {
			lines <- sc.Text()
		}
		close(lines)
	}()
	next := func() string {
		select {
		case l := <-lines:
			return l
		case <-time.After(30 * time.Second):
			t.Fatal("標準エラーの行が出ない")
			return ""
		}
	}

	first := next()
	m := listening.FindStringSubmatch(first)
	if m == nil || !strings.HasSuffix(first, "state="+state) {
		t.Fatalf("起動の 1 行が違う: %q", first)
	}
	req, _ := http.NewRequest("GET", fmt.Sprintf("http://127.0.0.1:%s/api/v4/user", m[1]), nil)
	req.Header.Set("PRIVATE-TOKEN", fakegitlab.Token)
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if res.StatusCode != 200 {
		t.Errorf("HTTP %d", res.StatusCode)
	}
	if got := next(); got != "fake-gitlab GET /api/v4/user" {
		t.Errorf("要求の記録 = %q", got)
	}

	// Windows にはシグナルでの停止が無い。止めるのは Kill（本体の停止は cmd/fakegitlab の単体テストが見る）
	if ospath.Windows {
		_ = cmd.Process.Kill()
	} else if err := cmd.Process.Signal(os.Interrupt); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-exited:
		if !ospath.Windows && err != nil {
			t.Errorf("0 で終わるはず: %v", err)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("止まらない")
	}
}
