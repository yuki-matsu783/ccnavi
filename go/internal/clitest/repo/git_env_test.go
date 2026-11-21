package repo

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

// 走った機械の git の設定が、テストから締め出されているかを確かめる。締め出しそのものは base.Main が置く。
// ここはそれが効いているかを見る側で、効かなくなる経路を 2 つ見る。
//
//  1. 締め出しの値が指す先が無い、または名前を取り違えた。指し先が無いと git は設定なしとして進むので、締め出せたように見える
//  2. 子の環境を組むときに締め出しが落ちる（呼び手の差分や Unset で消えない）
//
// 3 つ目（sh のラッパーが環境を消毒するときの巻き添え）は clitest/sh/git の試験が見る。
//
// 締め出さないと何が起きるかは実際に起きている。init.defaultBranch = main の機械では master を前提にした試験が落ち、
// commit.gpgsign = true の機械では commit が 10 倍遅くなる。どちらも「試験が緩む」ではなく「答えが機械で変わる」。

// 締め出しの中身を見るときに、git が出す設定の名前（小文字）。
var blockedKeys = []string{"init.defaultbranch", "core.excludesfile"}

func TestTheBlockPointsAtFilesThatExist(t *testing.T) {
	t.Parallel()
	gitEnv := base.GitEnv()
	for _, name := range []string{env.GitConfigGlobal, env.GitConfigSystem} {
		// 指し先が無いと git は何も出さずに進み、名前を間違えた回と見分けがつかない。
		if info, err := os.Stat(gitEnv[name]); err != nil || !info.Mode().IsRegular() {
			t.Errorf("%s = %q は実在するファイルのはず（%v）", name, gitEnv[name], err)
		}
	}
}

func TestTheHostGlobalConfigIsNotVisible(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	// 走った機械の ~/.gitconfig に何が入っているかは分からないので、内容でなく「締め出しのファイルが持つキーだけか」で見る。
	listed := gitIn(t, dir, nil, "config", "--global", "--list")
	var keys []string
	for _, line := range strings.Split(listed, "\n") {
		if line != "" {
			keys = append(keys, strings.SplitN(line, "=", 2)[0])
		}
	}
	sort.Strings(keys)
	want := append([]string(nil), blockedKeys...)
	sort.Strings(want)
	if strings.Join(keys, ",") != strings.Join(want, ",") {
		t.Errorf("ホストの設定が見えている: キー %v、表示 %q", keys, listed)
	}
}

// Claude Code は `**/.claude/settings.local.json` を $XDG_CONFIG_HOME/git/ignore に書く。締め出しが効くと、
// 試験が `git add -A` で作るリポジトリにその設定ファイルが入らない（#607）。
func TestTheHostGlobalIgnoreFileIsNotRead(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	xdg := filepath.Join(dir, "xdg")
	if err := os.MkdirAll(filepath.Join(xdg, "git"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(xdg, "git", "ignore"), []byte("**/.claude/settings.local.json\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	repo := filepath.Join(dir, "repo")
	if err := os.MkdirAll(filepath.Join(repo, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(repo, ".claude", "settings.local.json"), []byte("{}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	xdgEnv := map[string]string{"XDG_CONFIG_HOME": xdg}
	gitIn(t, repo, xdgEnv, "init", "--quiet")
	gitIn(t, repo, xdgEnv, "add", "-A")
	listed := gitIn(t, repo, xdgEnv, "ls-files")
	if listed != ".claude/settings.local.json" {
		t.Errorf("ls-files = %q。無視ファイルが読まれて、締め出しが効いていない", listed)
	}
}

// `-b` を渡さない git init の既定が、機械にも git の版にも依らない。sh の試験が `checkout master` する前提がこれ。
func TestInitMakesTheSameBranchOnEveryMachine(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	gitIn(t, dir, nil, "init", "--quiet")
	if got := gitIn(t, dir, nil, "branch", "--show-current"); got != "master" {
		t.Errorf("既定のブランチ = %q", got)
	}
}

// 署名は入らない。入る機械では commit 1 回が 8 ミリ秒から 90 ミリ秒になる。%G? の N は「署名なし」。
func TestCommitsAreNotSigned(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	gitIn(t, dir, nil, "init", "--quiet")
	if err := os.WriteFile(filepath.Join(dir, "a.txt"), []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	gitIn(t, dir, nil, "add", "-A")
	gitIn(t, dir, nil, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "s")
	if got := gitIn(t, dir, nil, "log", "-1", "--format=%G?"); got != "N" {
		t.Errorf("%%G? = %q。署名が入っている", got)
	}
}

// 子の環境は、呼び手が何を渡しても（渡さなくても、外す名前を書いても）締め出しを残す。
// 締め出し方そのものを試す呼び手が、締め出しに上書きされないことも見る。
func TestTheBlockSurvivesAnyCallerEnvironment(t *testing.T) {
	t.Parallel()
	gitEnv := base.GitEnv()
	cases := []struct {
		name string
		o    run.Opts
		want map[string]string // 締め出しの上に、呼び手が書いた値が勝つ分
	}{
		{name: "差分なし", o: run.Opts{}},
		{name: "呼び手の差分があっても残る", o: run.Opts{Env: map[string]string{"X_KEEP": "1"}}},
		{name: "関係ない名前を外しても残る", o: run.Opts{Unset: []string{"X_OTHER"}}},
		{name: "呼び手が上書きできる", o: run.Opts{Env: map[string]string{env.GitConfigGlobal: "/nowhere"}},
			want: map[string]string{env.GitConfigGlobal: "/nowhere"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			got := envMap(run.Environment(c.o))
			for name, value := range gitEnv {
				expect := value
				if v, ok := c.want[name]; ok {
					expect = v
				}
				if got[name] != expect {
					t.Errorf("%s = %q, want %q", name, got[name], expect)
				}
			}
		})
	}
}

// 締め出しが子のプロセスまで届くこと（組んだ環境の文字列だけでなく、実際に起こした sh が見る値）。
func TestTheBlockReachesTheChildProcess(t *testing.T) {
	t.Parallel()
	script := filepath.Join(base.TempDir(t), "print.sh")
	if err := os.WriteFile(script, []byte("printf '%s' \"$GIT_CONFIG_GLOBAL\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	r := run.Sh(t, script, nil)
	if want := base.GitEnv()[env.GitConfigGlobal]; r.Stdout != want {
		t.Errorf("子の GIT_CONFIG_GLOBAL = %q, want %q", r.Stdout, want)
	}
}

// envMap は NAME=value の並びを名前から値の map にする（Windows でも名前は大文字で比べる）。
func envMap(list []string) map[string]string {
	out := map[string]string{}
	for _, kv := range list {
		name, value, _ := strings.Cut(kv, "=")
		if ospath.Windows {
			name = strings.ToUpper(name)
		}
		out[name] = value
	}
	return out
}
