package small

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// gh・glab の資格情報を git に使わせる（ccnavi_git_auth）。できあがりの姿の ccnavi-common-host.sh を読む。
// 守ること: 相対の道具の場所は使わない（PATH の相対・空の項目・`$` を含む場所）、ホストの名前を実行しない、
// 親の git の設定は消さない、ssh の origin には何も足さない。

// gitAuth は、一時の木（repo）と道具の置き場（bin）。
type gitAuth struct {
	tmp, repo, bin string
}

func newGitAuth(t *testing.T) gitAuth {
	t.Helper()
	tmp := base.TempDir(t)
	g := gitAuth{tmp: tmp, repo: filepath.Join(tmp, "repo"), bin: filepath.Join(tmp, "bin")}
	if err := os.MkdirAll(g.repo, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(g.bin, 0o755); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("git", "init", "-q", g.repo)
	cmd.Env = run.Environment(run.Opts{})
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git init: %v: %s", err, out)
	}
	return g
}

// addRemote は repo に origin を足す。
func (g gitAuth) addRemote(t *testing.T, origin string) {
	t.Helper()
	cmd := exec.Command("git", "-C", g.repo, "remote", "add", "origin", origin)
	cmd.Env = run.Environment(run.Opts{})
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git remote add: %v: %s", err, out)
	}
}

// tool は bin に道具（何もしないスタブ）を置き、その絶対パスを返す。
func (g gitAuth) tool(t *testing.T, name string) string {
	t.Helper()
	path := filepath.Join(g.bin, name)
	writeExe(t, path)
	return path
}

// authResult は ccnavi_git_auth を 2 回呼んだあとの GIT_CONFIG_*（件数・キー・値）。
type authResult struct {
	count  string
	keys   []string
	values []string
}

// gitAuthProbe は、ccnavi_git_auth を 2 回呼び（2 度目は足さない）、GIT_CONFIG_* を出す。
const gitAuthProbe = `. "$1"
cd "$2"
ccnavi_git_auth .
ccnavi_git_auth .
printf 'count=%s\n' "${GIT_CONFIG_COUNT:-}"
n=${GIT_CONFIG_COUNT:-0}
i=0
while [ "$i" -lt "$n" ]; do
  eval "printf 'key=%s\\nvalue=%s\\n' \"\$GIT_CONFIG_KEY_$i\" \"\$GIT_CONFIG_VALUE_$i\""
  i=$((i + 1))
done
`

// authRun は origin を repo に足して ccnavi_git_auth を起こす。parent は親から来た GIT_CONFIG_*（nil なら無し）、
// path は PATH（空なら bin と /usr/bin:/bin）。
func (g gitAuth) authRun(t *testing.T, shell, origin string, parent bool, path string) authResult {
	t.Helper()
	g.addRemote(t, origin)
	if path == "" {
		path = g.bin + ":/usr/bin:/bin"
	}
	env := map[string]string{"PATH": path}
	if parent {
		env["GIT_CONFIG_COUNT"] = "1"
		env["GIT_CONFIG_KEY_0"] = "user.name"
		env["GIT_CONFIG_VALUE_0"] = "x"
	}
	res := runProgram(t, shell, run.Opts{Env: env}, "-c", gitAuthProbe, "probe", hostScript(), g.repo)
	return parseAuth(res.Stdout)
}

func parseAuth(out string) authResult {
	var r authResult
	for _, line := range strings.Split(out, "\n") {
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		switch k {
		case "count":
			r.count = v
		case "key":
			r.keys = append(r.keys, v)
		case "value":
			r.values = append(r.values, v)
		}
	}
	return r
}

// TestGitAuthAddsOneHelperForAnHTTPSHost は、HTTPS の origin に、その道具の資格情報の helper を 1 本だけ足す（2 度呼んでも増えない）。
func TestGitAuthAddsOneHelperForAnHTTPSHost(t *testing.T) {
	t.Parallel()
	shell := firstShell(t)
	cases := []struct {
		name, origin, key, tool string
	}{
		{"GitHub", "https://github.com/o/r.git", "credential.https://github.com.helper", "gh"},
		{"GitLab はポート付き", "http://localhost:8929/g/p.git", "credential.http://localhost:8929.helper", "glab"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			g := newGitAuth(t)
			helper := g.tool(t, c.tool)
			got := g.authRun(t, shell, c.origin, false, "")
			if got.count != "1" {
				t.Fatalf("count = %q, want 1", got.count)
			}
			if strings.Join(got.keys, ",") != c.key {
				t.Errorf("keys = %q, want %q", got.keys, c.key)
			}
			if want := `!"` + helper + `" auth git-credential`; strings.Join(got.values, ",") != want {
				t.Errorf("values = %q, want %q", got.values, want)
			}
		})
	}
}

func TestGitAuthAddsNothingForSSHOrigin(t *testing.T) {
	t.Parallel()
	g := newGitAuth(t)
	g.tool(t, "gh")
	if got := g.authRun(t, firstShell(t), "git@github.com:o/r.git", false, ""); got.count != "" {
		t.Errorf("count = %q, want 無し", got.count)
	}
}

func TestGitAuthAddsNothingWhenTheToolIsMissing(t *testing.T) {
	t.Parallel()
	for _, dir := range []string{"/usr/bin", "/bin"} {
		if _, err := os.Stat(filepath.Join(dir, "gh")); err == nil {
			t.Skip("システムに gh がある")
		}
	}
	g := newGitAuth(t)
	if got := g.authRun(t, firstShell(t), "https://github.com/o/r.git", false, ""); got.count != "" {
		t.Errorf("count = %q, want 無し", got.count)
	}
}

// PATH の相対の項目（bin）にある道具は使わない。
func TestGitAuthRefusesARelativeToolPath(t *testing.T) {
	t.Parallel()
	g := newGitAuth(t)
	writeExe(t, filepath.Join(g.repo, "bin", "gh"))
	got := g.authRun(t, firstShell(t), "https://github.com/o/r.git", false, "bin:/usr/bin:/bin")
	if len(got.keys) != 0 {
		t.Errorf("相対の道具を使った: keys = %q", got.keys)
	}
}

// 相対の項目は飛ばし、絶対の項目の道具を使う。
func TestGitAuthSkipsARelativeEntryAndUsesTheAbsoluteOne(t *testing.T) {
	t.Parallel()
	g := newGitAuth(t)
	writeExe(t, filepath.Join(g.repo, "bin", "gh"))
	gh := g.tool(t, "gh")
	got := g.authRun(t, firstShell(t), "https://github.com/o/r.git", false, "bin:"+g.bin+":/usr/bin:/bin")
	if want := `!"` + gh + `" auth git-credential`; strings.Join(got.values, ",") != want {
		t.Errorf("values = %q, want %q", got.values, want)
	}
}

// PATH の空の項目（カレントディレクトリの意味になる）は、カレントの道具を選ばない。
func TestGitAuthEmptyPathEntryDoesNotPickTheCurrentDirectory(t *testing.T) {
	t.Parallel()
	g := newGitAuth(t)
	writeExe(t, filepath.Join(g.repo, "gh"))
	got := g.authRun(t, firstShell(t), "https://github.com/o/r.git", false, "::/usr/bin:/bin:")
	for _, v := range got.values {
		if strings.Contains(v, g.repo) {
			t.Errorf("カレントの道具を選んだ: %q", v)
		}
	}
}

// ホストへの接続（ccnavi_host_connect）も、相対の glab を実行しない。
func TestHostConnectRunsTheAbsoluteGlabNotARelativeOne(t *testing.T) {
	t.Parallel()
	g := newGitAuth(t)
	marker := filepath.Join(g.tmp, "rel-ran")
	writeFile(t, filepath.Join(g.repo, "bin", "glab"), "#!/bin/sh\n: > '"+marker+"'\nexit 0\n")
	if err := os.Chmod(filepath.Join(g.repo, "bin", "glab"), 0o755); err != nil {
		t.Fatal(err)
	}
	g.tool(t, "glab")
	const probe = `. "$1"
cd "$2"
ccnavi_h_kind=gitlab ccnavi_h_host=h ccnavi_h_path=g/p ccnavi_h_max_time=5
ccnavi_host_connect
printf 'rc=%s cli=%s\n' "$?" "$ccnavi_h_cli"
`
	res := runProgram(t, firstShell(t), run.Opts{
		Env: map[string]string{"PATH": "bin:" + g.bin + ":/usr/bin:/bin"},
	}, "-c", probe, "probe", hostScript(), g.repo)
	if want := "cli=" + g.bin + "/glab"; !strings.Contains(res.Stdout, want) {
		t.Errorf("stdout = %q, want %q を含む（stderr %q）", res.Stdout, want, res.Stderr)
	}
	if _, err := os.Stat(marker); err == nil {
		t.Errorf("相対の glab が動いた")
	}
}

// `$` と `(` を含む道具の場所は、コマンドの置換として動かさない。
func TestGitAuthRefusesAToolPathWithDollar(t *testing.T) {
	t.Parallel()
	g := newGitAuth(t)
	bad := filepath.Join(g.tmp, "d$(touch pwned)")
	writeExe(t, filepath.Join(bad, "gh"))
	got := g.authRun(t, firstShell(t), "https://github.com/o/r.git", false, bad+":/usr/bin:/bin")
	if len(got.keys) != 0 {
		t.Errorf("道具を使った: keys = %q", got.keys)
	}
	for _, dir := range []string{g.repo, g.tmp} {
		if _, err := os.Stat(filepath.Join(dir, "pwned")); err == nil {
			t.Errorf("%s で置換が動いた", dir)
		}
	}
}

// 親の git の設定（GIT_CONFIG_*）は消さず、後ろに足す。
func TestGitAuthKeepsTheParentConfig(t *testing.T) {
	t.Parallel()
	g := newGitAuth(t)
	g.tool(t, "gh")
	got := g.authRun(t, firstShell(t), "https://github.com/o/r.git", true, "")
	if got.count != "2" {
		t.Fatalf("count = %q, want 2", got.count)
	}
	if len(got.keys) == 0 || got.keys[0] != "user.name" {
		t.Errorf("keys = %q, 先頭が user.name のはず", got.keys)
	}
}

// ホストの名前に `$(…)` があっても、何も動かさない。
func TestGitAuthRunsNothingForAHostileHost(t *testing.T) {
	t.Parallel()
	g := newGitAuth(t)
	g.tool(t, "glab")
	marker := filepath.Join(g.tmp, "pwned")
	g.authRun(t, firstShell(t), "https://$(touch "+marker+")/a/b.git", false, "")
	if _, err := os.Stat(marker); err == nil {
		t.Errorf("ホストの名前の置換が動いた")
	}
}
