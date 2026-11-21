package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/fakegitlab"
	"ccnavi/internal/harness/gitlabdocker"
	"ccnavi/internal/harness/scenario/actors"
)

func TestMain(m *testing.M) { base.Main(m) }

// 引数を振り分ける入口。本物の Docker も GitLab も使わない道具だけを回す。
func call(t *testing.T, args []string, environ ...string) (int, string, string) {
	t.Helper()
	var out, errw bytes.Buffer
	rc := run(args, base.Root(), append([]string{"HOME=" + base.TempDir(t)}, environ...), &out, &errw)
	return rc, out.String(), errw.String()
}

func TestDispatchRefusesWhatItDoesNotKnow(t *testing.T) {
	t.Parallel()
	for name, tc := range map[string]struct {
		args []string
		rc   int
		want string
	}{
		"引数なし":            {nil, 2, "使い方"},
		"知らないサブコマンド":      {[]string{"nope"}, 2, "知らないサブコマンド"},
		"知らないオプション":       {[]string{"up", "--nope"}, 2, ""},
		"余計な引数":           {[]string{"status", "x"}, 2, "余計な引数"},
		"tools の道具が無い":    {[]string{"tools"}, 2, "使い方"},
		"tools の置き場が無い":   {[]string{"tools", "leak"}, 2, "出力の置き場"},
		"agree に識別子が無い":   {[]string{"tools", "agree", "out"}, 2, "識別子"},
		"知らない道具":          {[]string{"tools", "nope", "out"}, 2, "知らない道具"},
		"--help は 0 で終わる": {[]string{"--help"}, 0, "使い方"},
	} {
		rc, out, errw := call(t, tc.args)
		if rc != tc.rc || !strings.Contains(out+errw, tc.want) {
			t.Errorf("%s: rc = %d、%q %q", name, rc, out, errw)
		}
	}
}

func TestProbeHelpAndMissingTokensGoThroughTheEntry(t *testing.T) {
	t.Parallel()
	if rc, out, _ := call(t, []string{"probe", "--help"}); rc != 0 || !strings.Contains(out, "GITLAB_TOKEN") {
		t.Errorf("probe --help: rc = %d、%q", rc, out)
	}
	// トークンも tokens.env も無ければ、何も作らず止まる
	if rc, _, errw := call(t, []string{"probe"}); rc != 2 || !strings.Contains(errw, "make_gitlab_tokens.rb") {
		t.Errorf("probe: rc = %d、%q", rc, errw)
	}
	if rc, _, errw := call(t, []string{"record-contract", "--dry-run"}); rc != 2 || !strings.Contains(errw, "トークンが無い") {
		t.Errorf("record-contract: rc = %d、%q", rc, errw)
	}
}

// ProbeEnviron は、環境変数に無いトークンと接続先を、tokens.env と同じポートで補う。
func TestProbeEnvironFillsTokensAndTheBase(t *testing.T) {
	t.Parallel()
	home := base.TempDir(t)
	environ := []string{"CCNAVI_GITLAB_PORT=9123"}
	tool := &gitlabdocker.Tool{Home: home, Getenv: getenvFrom(environ)}
	if err := gitlabdocker.WriteEnvFile(tool.TokensPath(), map[string]string{"GITLAB_TOKEN": "root", "CCNAVI_PROBE_REVIEWER_TOKEN": "rev"}, nil); err != nil {
		t.Fatal(err)
	}
	get := getenvFrom(probeEnviron(tool, environ, getenvFrom(environ)))
	for k, want := range map[string]string{"GITLAB_TOKEN": "root", "CCNAVI_PROBE_REVIEWER_TOKEN": "rev", "CCNAVI_PROBE_GITLAB": "http://127.0.0.1:9123"} {
		if got := get(k); got != want {
			t.Errorf("%s = %q、%q のはず", k, got, want)
		}
	}
	// 環境変数で接続先を決めていれば、そのまま
	given := []string{"CCNAVI_PROBE_GITLAB=http://example.test:1"}
	if got := getenvFrom(probeEnviron(tool, given, getenvFrom(given)))("CCNAVI_PROBE_GITLAB"); got != "http://example.test:1" {
		t.Errorf("接続先を上書きした: %q", got)
	}
}

// toolsRig は tools が読む物（runner の出力の置き場の state.json とトークンのファイル）を、模擬の相手で用意する。
type toolsRig struct {
	environ []string
	out     string
	root    string
	tokens  string
	srv     *fakegitlab.Server
}

func newToolsRig(t *testing.T) *toolsRig {
	t.Helper()
	srv := fakegitlab.Start(t, fakegitlab.State{"project": "7", "seq": 100, "issues": []any{}, "mrs": []any{}}, 0)
	tmp := base.TempDir(t)
	r := &toolsRig{srv: srv, out: filepath.Join(tmp, "out"), root: filepath.Join(tmp, "ws")}
	r.environ = []string{"HOME=" + tmp, "XDG_CACHE_HOME=" + tmp, fmt.Sprintf("CCNAVI_GITLAB_PORT=%d", srv.Port())}
	for _, d := range []string{r.out, r.root} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	state, _ := json.Marshal(map[string]any{"host": map[string]any{"kind": "docker-gitlab", "project_id": 7, "base": srv.URL, "bin": filepath.Join(tmp, "bin")}})
	if err := os.WriteFile(filepath.Join(r.out, "state.json"), state, 0o644); err != nil {
		t.Fatal(err)
	}
	tool := &gitlabdocker.Tool{Home: tmp, Getenv: getenvFrom(r.environ)}
	r.tokens = tool.TokensPath()
	// モックの GitLab は Token だけを受ける。人間役にも同じ値を使う
	values := map[string]string{"GITLAB_TOKEN": "glpat-agent-1", "CCNAVI_PROBE_REVIEWER_TOKEN": fakegitlab.Token}
	if err := gitlabdocker.WriteEnvFile(r.tokens, values, nil); err != nil {
		t.Fatal(err)
	}
	return r
}

func (r *toolsRig) call(t *testing.T, args ...string) (int, string, string) {
	t.Helper()
	var out, errw bytes.Buffer
	rc := run(append([]string{"tools"}, args...), base.Root(), r.environ, &out, &errw)
	return rc, out.String(), errw.String()
}

func TestToolsIssueCreatesAnIssueAsTheHumanRole(t *testing.T) {
	t.Parallel()
	r := newToolsRig(t)
	rc, out, errw := r.call(t, "issue", r.out, "--root", r.root, "--title", "題")
	if rc != 0 || !strings.Contains(out, "status 201") {
		t.Fatalf("rc = %d、%q %q", rc, out, errw)
	}
	if !contains(r.srv.Requests(), "POST /api/v4/projects/7/issues") {
		t.Errorf("issue を作る要求が無い: %v", r.srv.Requests())
	}
}

func contains(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}

func TestToolsStopWhenTheRunnerStateIsMissing(t *testing.T) {
	t.Parallel()
	r := newToolsRig(t)
	if err := os.Remove(filepath.Join(r.out, "state.json")); err != nil {
		t.Fatal(err)
	}
	if rc, _, errw := r.call(t, "issue", r.out); rc != 1 || errw == "" {
		t.Errorf("rc = %d、%q", rc, errw)
	}
}

func TestToolsLeakNamesTheFilesWithoutPrintingTheValues(t *testing.T) {
	t.Parallel()
	r := newToolsRig(t)
	if rc, out, _ := r.call(t, "leak", r.out, "--root", r.root); rc != 0 || !strings.Contains(out, "エージェント役: 0 件") {
		t.Fatalf("漏れが無いのに rc = %d、%q", rc, out)
	}
	leaked := filepath.Join(r.root, "logs", "a.log")
	if err := os.MkdirAll(filepath.Dir(leaked), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(leaked, []byte("x glpat-agent-1 y"), 0o644); err != nil {
		t.Fatal(err)
	}
	rc, out, _ := r.call(t, "leak", r.out, "--root", r.root)
	if rc != 1 || !strings.Contains(out, "エージェント役: 1 件") || !strings.Contains(out, leaked) || strings.Contains(out, "glpat-agent-1") {
		t.Errorf("rc = %d、%q", rc, out)
	}
	if !strings.Contains(out, "人間役: 0 件") {
		t.Errorf("人間役の行が無い: %q", out)
	}
}

func TestToolsLeakRefusesWhenTheTokensAreMissing(t *testing.T) {
	t.Parallel()
	r := newToolsRig(t)
	if err := os.Remove(r.tokens); err != nil {
		t.Fatal(err)
	}
	rc, out, errw := r.call(t, "leak", r.out, "--root", r.root)
	if rc != 2 || !strings.Contains(errw, r.tokens) || strings.Contains(out, "件") {
		t.Errorf("rc = %d、%q %q", rc, out, errw)
	}
}

// 承認の代行は、擬似端末から y を送り、出力に混ざるトークンを伏せる。
func TestToolsAgreeSendsYThroughATerminalAndMasksTokens(t *testing.T) {
	t.Parallel()
	if runtime.GOOS == "windows" {
		t.Skip("Windows に擬似端末は無い（手で打つ経路に落とす）")
	}
	r := newToolsRig(t)
	script := filepath.Join(r.root, ".ccnavi", "scripts", "ccnavi-user-agree.sh")
	body := "read answer\necho \"answer=$answer ids=$*\"\necho glpat-secret-xyz\n"
	if err := os.MkdirAll(filepath.Dir(script), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(script, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	rc, out, errw := r.call(t, "agree", r.out, "i0001", "i0001-01-01", "--root", r.root)
	if errw != "" && strings.Contains(errw, actors.ErrNoTerminal.Error()) {
		t.Skip("script コマンドが無い")
	}
	if rc != 0 || !strings.Contains(out, "answer=y ids=i0001 i0001-01-01") || strings.Contains(out, "glpat-secret-xyz") || !strings.Contains(out, "glpat-***") || !strings.Contains(out, "終了コード: 0") {
		t.Errorf("rc = %d、%q %q", rc, out, errw)
	}
}
