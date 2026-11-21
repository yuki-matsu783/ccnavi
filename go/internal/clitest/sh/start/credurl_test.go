package start

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
)

// hoststub の CredURL（Docker の GitLab で git の push が通るようにする仕掛け）の観点。git のスタブは、その URL だけに環境変数 GITLAB_TOKEN を
// 返す credential helper を、利用者の helper（GCM・osxkeychain）より先に足す。ほかのホストには何も返さない。
// 値を引数に入れず環境変数から読むので、プロセスの一覧にトークンが出ない。
func TestCredURLReturnsTheTokenOnlyForThatURL(t *testing.T) {
	t.Parallel()
	need(t, "git")
	dir := base.TempDir(t)
	stub := hoststub.Build(t, dir, false)
	const cred = "http://127.0.0.1:8929"
	env := stub.Env(hoststub.EnvOpts{
		Token: "tok-cred-7c1", CredURL: cred,
		Bare: filepath.Join(dir, "bare.git"), RealOrigin: cred + "/root/p.git",
	})
	env["GIT_TERMINAL_PROMPT"] = "0"
	fill := func(host string) run.Result {
		return run.Command(t, filepath.Join(stub.BinDir, "git"), []string{"credential", "fill"},
			run.Opts{Dir: dir, Env: env, Input: "protocol=http\nhost=" + host + "\n\n"})
	}
	hit := code(t, fill("127.0.0.1:8929"), 0)
	has(t, hit.Stdout, "username=root", "password=tok-cred-7c1")
	other := fill("example.invalid")
	lacks(t, out(other), "tok-cred-7c1")
}
