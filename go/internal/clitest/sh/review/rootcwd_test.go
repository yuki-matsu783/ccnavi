package review

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// ワークスペースルート（origin は GitHub）から review の confirm・decide・request を打ったときの止まり方
// （ready は --parent が要る別の止め方）。
//
// 親は cwd のワークツリーから引く。ルートから打つと親が決まらないので、ホストへ繋ぐ前に「親のワークツリーの中で打つ」と止める。
// 先にホストへ繋ぐと、親のプロジェクトが GitLab でも、ルート自身の origin（GitHub）で見て「gh が github.com で利用できず」と
// 別の理由で止まっていた。

// rootSite は、origin が GitHub のワークスペースルート。認証の通らない gh を先頭に置き、トークンも渡さないので、
// ホストへ繋ぐと必ず「gh が github.com で利用できず」で止まる。
type rootSite struct {
	*site
	env map[string]string
}

func newRootSite(t *testing.T) rootSite {
	t.Helper()
	s := newMarkerSite(t, "main")
	s.git("remote", "add", "origin", "https://github.com/o/r.git")
	s.git("update-ref", "refs/remotes/origin/main", "HEAD") // 統合先は origin/main から決まる
	shim := filepath.Join(s.tmp, "shim")
	shkit.WriteExec(t, filepath.Join(shim, "gh"), "#!/bin/sh\nexit 1\n")
	// 統合先は本物の実行ファイル（git integration）に聞く
	return rootSite{s, map[string]string{"CCNAVI_ENTRY_PATH": exe.Path(t), "PATH": pathWith(shim)}}
}

func (r rootSite) review(cwd string, args ...string) run.Result {
	r.t.Helper()
	return r.real(reviewSh, run.Opts{Env: r.env, Dir: cwd, Unset: []string{"GH_TOKEN"}}, args...)
}

// parentWorktree は `.claude/worktrees/p-1`（親のワークツリー）を作って返す。
func (r rootSite) parentWorktree() string {
	r.t.Helper()
	r.git("worktree", "add", "-q", "-b", "p-1", ".claude/worktrees/p-1")
	return filepath.Join(r.ws, ".claude", "worktrees", "p-1")
}

// nextLines は標準エラーの `次: ` の行。
func nextLines(stderr string) []string {
	var out []string
	for _, l := range lines(stderr) {
		if strings.HasPrefix(l, "次: ") {
			out = append(out, l)
		}
	}
	return out
}

func TestRootCwdStopsBeforeConnectingToTheHost(t *testing.T) {
	t.Parallel()
	cases := map[string][]string{
		"confirm": {"confirm", "--phase", "1"},
		"decide":  {"decide", "1", "--preview"},
		"request": {"review-request", "--phase", "1", "--body-file", "b.md"},
	}
	for name, args := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			r := newRootSite(t)
			done := r.review(r.ws, args...)
			if done.Code != 2 || strings.Contains(done.Stderr, "利用できず") || !strings.Contains(done.Stderr, "の中で打ってください") {
				t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
			}
			// 止める理由に cwd が出る
			if !strings.Contains(done.Stderr, filepath.Base(r.ws)) {
				t.Errorf("cwd が出ない: %s", done.Stderr)
			}
			next := nextLines(done.Stderr)
			if len(next) != 1 || !strings.Contains(next[0], "cd ") || !strings.Contains(next[0], "ccnavi-review.sh' "+args[0]) {
				t.Errorf("次: の行 = %v", next)
			}
		})
	}
}

func TestInsideAParentWorktreeIsNotStopped(t *testing.T) {
	t.Parallel()
	r := newRootSite(t)
	tree := r.parentWorktree()
	for _, args := range [][]string{{"confirm", "--phase", "1"}, {"decide", "1", "--preview"}} {
		done := r.review(tree, args...)
		// 中なら先へ進み、ホストへ繋ぐところで止まる
		if strings.Contains(done.Stderr, "の中で打ってください") || !strings.Contains(done.Stderr, "利用できず") {
			t.Errorf("%v: code = %d\n%s", args, done.Code, done.Stderr)
		}
	}
}

func TestReadyWithoutParentStopsBeforeTheHostAndSaysHowToTypeItOutside(t *testing.T) {
	t.Parallel()
	r := newRootSite(t)
	done := r.review(r.ws, "ready")
	next := nextLines(done.Stderr)
	if done.Code != 2 || strings.Contains(done.Stderr, "利用できず") || !strings.Contains(done.Stderr, "--parent") ||
		len(next) != 1 || !strings.Contains(next[0], "ready --parent <親の識別子>") || !strings.Contains(done.Stderr, "案内: ") {
		t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}

func TestReadyWithoutParentInsideAParentWorktreeFillsInTheParent(t *testing.T) {
	t.Parallel()
	r := newRootSite(t)
	tree := r.parentWorktree()
	done := r.review(tree, "ready")
	next := nextLines(done.Stderr)
	if done.Code != 2 || strings.Contains(done.Stderr, "利用できず") || len(next) != 1 || !strings.Contains(next[0], "ready --parent p-1") {
		t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}

func TestReadyWithParentIsNotStoppedByTheCheck(t *testing.T) {
	t.Parallel()
	r := newRootSite(t)
	done := r.review(r.ws, "ready", "--parent", "p-1")
	if strings.Contains(done.Stderr, "の中で打ってください") || !strings.Contains(done.Stderr, "ワークツリー") {
		t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}
