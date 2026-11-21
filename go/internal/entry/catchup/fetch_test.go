package catchup

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// fetch の判定（早送り・統合先を進める・origin/HEAD の向け直し）を、使い捨てのリポジトリと bare の origin で確かめる。
// 取ってくる（fetch・ls-remote）のは sh なので、ここでは origin/<ブランチ> を取り終えた状態から始める。
// sh を通す確認は clitest/sh/start の試験に数本だけ残す。

type fetchFixture struct {
	t      *testing.T
	repo   string
	bare   string
	pusher string
}

func newFetch(t *testing.T) *fetchFixture {
	t.Helper()
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	f := &fetchFixture{t: t, repo: filepath.Join(base, "ws"), bare: filepath.Join(base, "origin.git"), pusher: filepath.Join(base, "pusher")}
	gitIn(t, base, "init", "-q", "-b", "main", f.repo)
	gitIn(t, f.repo, "config", "user.email", "t@example.invalid")
	gitIn(t, f.repo, "config", "user.name", "t")
	put(t, filepath.Join(f.repo, ".gitignore"), ".claude/worktrees/\n")
	put(t, filepath.Join(f.repo, "README.md"), "seed\n")
	gitIn(t, f.repo, "add", "--", ".gitignore", "README.md")
	gitIn(t, f.repo, "commit", "-q", "-m", "seed")
	gitIn(t, base, "init", "-q", "--bare", "-b", "main", f.bare)
	gitIn(t, f.repo, "remote", "add", "origin", f.bare)
	gitIn(t, f.repo, "push", "-q", "-u", "origin", "main")
	gitIn(t, f.repo, "remote", "set-head", "origin", "main")
	return f
}

func (f *fetchFixture) rev(dir, ref string) string {
	cmd := exec.Command("git", "rev-parse", "--verify", "-q", ref)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), testkit.GitEnv()...)
	out, _ := cmd.Output()
	return strings.TrimSpace(string(out))
}

func (f *fetchFixture) git(dir string, args ...string) { gitIn(f.t, dir, args...) }

// advance は別の機械の代わりに、origin の branch を 1 件進めて取ってきて（origin/<branch> を更新して）、足したファイルの名前を返す。
func (f *fetchFixture) advance(branch string) string {
	t := f.t
	if _, err := os.Stat(f.pusher); err != nil {
		gitIn(t, filepath.Dir(f.pusher), "clone", "-q", f.bare, f.pusher)
		gitIn(t, f.pusher, "config", "user.email", "t@example.invalid")
		gitIn(t, f.pusher, "config", "user.name", "t")
	}
	gitIn(t, f.pusher, "fetch", "-q", "origin")
	gitIn(t, f.pusher, "checkout", "-q", "-B", branch, "origin/"+branch)
	entries, _ := os.ReadDir(f.pusher)
	name := "r" + strings.Repeat("x", len(entries)) + ".txt"
	put(t, filepath.Join(f.pusher, name), "remote\n")
	gitIn(t, f.pusher, "add", "-A")
	gitIn(t, f.pusher, "commit", "-q", "-m", "remote")
	gitIn(t, f.pusher, "push", "-q", "origin", branch)
	gitIn(t, f.repo, "fetch", "-q", "origin")
	return name
}

// ignore は、手元の worktree に、進める先が足すファイルと同じ名前の無視されたファイルを置く。
func (f *fetchFixture) ignore(dir, name string) {
	exclude := filepath.Join(f.repo, ".git", "info", "exclude")
	put(f.t, exclude, name+"\n")
	put(f.t, filepath.Join(dir, name), "手元の ignored\n")
}

func outLines(a *answer) string {
	var out []string
	for _, line := range strings.Split(a.String(), "\n") {
		if text, ok := strings.CutPrefix(line, "out "); ok {
			out = append(out, text)
		}
	}
	return strings.Join(out, "\n")
}

func contains(t *testing.T, text string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !strings.Contains(text, w) {
			t.Errorf("%q が無い:\n%s", w, text)
		}
	}
}

// 統合先を origin/<統合先> まで進める（ルートがどこに居ても）。進めるのは早送りだけで、手元にしか無いコミット・書きかけ・
// 無視されたファイルとの重なりがあれば触らず、理由を言う。
func TestAdvanceTheIntegrationBranch(t *testing.T) {
	const wt = ".claude/worktrees/main-tree"
	t.Run("behind advances while the root is elsewhere", func(t *testing.T) {
		f := newFetch(t)
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		f.advance("main")
		var a answer
		advance(&a, f.repo, "main")
		contains(t, outLines(&a), "統合先ブランチ main を 1 コミット進めた")
		if f.rev(f.repo, "refs/heads/main") != f.rev(f.repo, "refs/remotes/origin/main") {
			t.Error("進んでいない")
		}
	})
	t.Run("a missing local branch is created from origin", func(t *testing.T) {
		f := newFetch(t)
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		head := f.rev(f.repo, "refs/heads/main")
		f.git(f.repo, "branch", "-q", "-D", "main")
		var a answer
		advance(&a, f.repo, "main")
		contains(t, outLines(&a), "ローカルに統合先ブランチ main が無かった")
		if f.rev(f.repo, "refs/heads/main") != head || f.rev(f.repo, "main@{u}") != head {
			t.Error("作れていない、または上流が無い")
		}
	})
	t.Run("a diverged branch is left alone", func(t *testing.T) {
		f := newFetch(t)
		put(t, filepath.Join(f.repo, "local.txt"), "local\n")
		f.git(f.repo, "add", "-A")
		f.git(f.repo, "commit", "-q", "-m", "local")
		mine := f.rev(f.repo, "refs/heads/main")
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		f.advance("main")
		var a answer
		advance(&a, f.repo, "main")
		contains(t, outLines(&a), "一致しない", mine[:7], f.rev(f.repo, "origin/main")[:7])
		if f.rev(f.repo, "refs/heads/main") != mine {
			t.Error("手元の ref を動かした")
		}
	})
	t.Run("a shallow clone is reported as not matching", func(t *testing.T) {
		// 浅い clone で履歴が切れると、手元の版が丸ごと「リモートに無いコミット」に見える。原因は見分けない。
		f := newFetch(t)
		for range 3 {
			f.advance("main")
		}
		f.git(f.repo, "pull", "-q", "--ff-only", "origin", "main")
		f.git(f.repo, "update-ref", "refs/heads/main", f.rev(f.repo, "HEAD")+"~2")
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		f.git(f.repo, "fetch", "-q", "--depth", "1", "origin", "main")
		before := f.rev(f.repo, "refs/heads/main")
		var a answer
		advance(&a, f.repo, "main")
		contains(t, outLines(&a), "一致しない", before[:7], f.rev(f.repo, "origin/main")[:7])
		if f.rev(f.repo, "refs/heads/main") != before {
			t.Error("手元の ref を動かした")
		}
	})
	t.Run("a dirty tree that holds it is left alone", func(t *testing.T) {
		f := newFetch(t)
		tree := filepath.Join(f.repo, wt)
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		f.git(f.repo, "worktree", "add", "-q", tree, "main")
		before := f.rev(f.repo, "refs/heads/main")
		f.advance("main")
		put(t, filepath.Join(tree, "README.md"), "書きかけ\n")
		var a answer
		advance(&a, f.repo, "main")
		contains(t, outLines(&a), "未コミットの変更")
		if f.rev(f.repo, "refs/heads/main") != before {
			t.Error("書きかけのあるツリーを進めた")
		}
	})
	t.Run("an ignored file is not overwritten", func(t *testing.T) {
		f := newFetch(t)
		tree := filepath.Join(f.repo, wt)
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		f.git(f.repo, "worktree", "add", "-q", tree, "main")
		before := f.rev(tree, "HEAD")
		f.ignore(tree, f.advance("main"))
		var a answer
		advance(&a, f.repo, "main")
		contains(t, outLines(&a), "進められなかった")
		if f.rev(tree, "HEAD") != before {
			t.Error("無視されたファイルと重なるのに進めた")
		}
	})
	t.Run("advances in the tree that holds it", func(t *testing.T) {
		f := newFetch(t)
		tree := filepath.Join(f.repo, wt)
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		f.git(f.repo, "worktree", "add", "-q", tree, "main")
		f.advance("main")
		var a answer
		advance(&a, f.repo, "main")
		contains(t, outLines(&a), "統合先ブランチ")
		if f.rev(tree, "HEAD") != f.rev(f.repo, "origin/main") {
			t.Error("ツリーが進んでいない")
		}
	})
}

// 承認済みチケットを持つツリーがチェックアウトしているブランチは、upstream まで早送りする。
func TestTrackTheCheckedOutBranch(t *testing.T) {
	setup := func(t *testing.T) *fetchFixture {
		f := newFetch(t)
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		f.git(f.repo, "push", "-q", "-u", "origin", "claude/x")
		return f
	}
	t.Run("advances", func(t *testing.T) {
		f := setup(t)
		f.advance("claude/x")
		var a answer
		track(&a, f.repo, "claude/x")
		contains(t, outLines(&a), "親ブランチ claude/x を 1 コミット進めた")
		if f.rev(f.repo, "HEAD") != f.rev(f.repo, "origin/claude/x") {
			t.Error("進んでいない")
		}
	})
	t.Run("does not overwrite an ignored file", func(t *testing.T) {
		f := setup(t)
		before := f.rev(f.repo, "HEAD")
		f.ignore(f.repo, f.advance("claude/x"))
		var a answer
		track(&a, f.repo, "claude/x")
		if f.rev(f.repo, "HEAD") != before || !strings.Contains(outLines(&a), "一致しない") {
			t.Errorf("答え: %s", outLines(&a))
		}
	})
	t.Run("a branch that does not match is named and not merged", func(t *testing.T) {
		f := setup(t)
		remote := func() string { f.advance("claude/x"); return f.rev(f.repo, "origin/claude/x") }()
		put(t, filepath.Join(f.repo, "mine.txt"), "mine\n")
		f.git(f.repo, "add", "--", "mine.txt")
		f.git(f.repo, "commit", "-q", "-m", "mine")
		mine := f.rev(f.repo, "HEAD")
		var a answer
		track(&a, f.repo, "claude/x")
		contains(t, outLines(&a), "一致しない", mine[:7], remote[:7])
		if f.rev(f.repo, "HEAD") != mine {
			t.Error("分かれているのに動かした")
		}
	})
	t.Run("an uncommitted change keeps it from moving", func(t *testing.T) {
		f := setup(t)
		f.advance("claude/x")
		put(t, filepath.Join(f.repo, "README.md"), "書きかけ\n")
		var a answer
		track(&a, f.repo, "claude/x")
		contains(t, outLines(&a), "未コミットの変更があるので進めない")
	})
}

// 親のワークツリーは、origin/<P> まで早送りする。書きかけとの重なり・無視されたファイル・分岐は進めずに言う。早送りが要れば
// ロックを 1 回だけ試す（lock、そのあと locked か busy で聞き直す）。
func TestForwardTheFamily(t *testing.T) {
	setup := func(t *testing.T) (*fetchFixture, string) {
		f := newFetch(t)
		f.git(f.repo, "checkout", "-q", "-b", "claude/x")
		tree := filepath.Join(f.repo, ".claude", "worktrees", "i0001")
		f.git(f.repo, "worktree", "add", "-q", tree, "-b", "i0001", "main")
		put(t, filepath.Join(tree, ".ccnavi", "approved", "doing", "i0001.md"), "---\nversion: 1\nticket: i0001\n---\n")
		f.git(tree, "add", "--", ".ccnavi/approved/doing/i0001.md")
		f.git(tree, "commit", "-q", "-m", "copy")
		f.git(tree, "push", "-q", "-u", "origin", "i0001")
		return f, tree
	}
	run := func(f *fetchFixture, mode string) string {
		var a answer
		forward(&a, f.repo, "i0001", "i0001", mode)
		return a.String()
	}
	t.Run("asks for the lock first and moves once locked", func(t *testing.T) {
		f, tree := setup(t)
		f.advance("i0001")
		if got := run(f, ""); got != "next lock\n" {
			t.Errorf("答え: %q", got)
		}
		contains(t, run(f, "locked"), "親ブランチ i0001 を 1 コミット進めた")
		if f.rev(tree, "HEAD") != f.rev(f.repo, "origin/i0001") {
			t.Error("進んでいない")
		}
	})
	t.Run("busy tells the person to run sync", func(t *testing.T) {
		f, tree := setup(t)
		before := f.rev(tree, "HEAD")
		f.advance("i0001")
		contains(t, run(f, "busy"), "他の操作の最中", "ccnavi-sync.sh i0001")
		if f.rev(tree, "HEAD") != before {
			t.Error("ロックを取れないのに進めた")
		}
	})
	t.Run("overlapping work in progress is not moved", func(t *testing.T) {
		f, tree := setup(t)
		before := f.rev(tree, "HEAD")
		added := f.advance("i0001")
		put(t, filepath.Join(tree, added), "書きかけ\n")
		got := run(f, "locked")
		contains(t, got, "重なる", added, "ccnavi-sync.sh i0001")
		if f.rev(tree, "HEAD") != before {
			t.Error("重なるのに進めた")
		}
	})
	t.Run("an ignored file is not overwritten", func(t *testing.T) {
		f, tree := setup(t)
		before := f.rev(tree, "HEAD")
		added := f.advance("i0001")
		f.ignore(tree, added)
		run(f, "locked")
		if f.rev(tree, "HEAD") != before {
			t.Error("無視されたファイルと重なるのに進めた")
		}
		if got, _ := os.ReadFile(filepath.Join(tree, added)); string(got) != "手元の ignored\n" {
			t.Errorf("手元のファイルが変わった: %q", got)
		}
	})
	t.Run("a diverged family is named and not merged", func(t *testing.T) {
		f, tree := setup(t)
		f.advance("i0001")
		put(t, filepath.Join(tree, "mine.txt"), "mine\n")
		f.git(tree, "add", "--", "mine.txt")
		f.git(tree, "commit", "-q", "-m", "mine")
		mine := f.rev(tree, "HEAD")
		contains(t, run(f, ""), "一致しない", "取り込みが必要です", mine[:7], "ccnavi-sync.sh i0001")
		if f.rev(tree, "HEAD") != mine {
			t.Error("分かれているのに動かした")
		}
	})
	t.Run("a refusal that is not an overlap says why", func(t *testing.T) {
		f, tree := setup(t)
		before := f.rev(tree, "HEAD")
		f.advance("i0001")
		cmd := exec.Command("git", "-C", tree, "rev-parse", "--git-path", "index.lock")
		cmd.Env = append(os.Environ(), testkit.GitEnv()...)
		out, _ := cmd.Output()
		lock := strings.TrimSpace(string(out))
		if !filepath.IsAbs(lock) {
			lock = filepath.Join(tree, lock)
		}
		put(t, lock, "")
		got := run(f, "locked")
		contains(t, got, "index.lock")
		if strings.Contains(got, "と重なる") || f.rev(tree, "HEAD") != before {
			t.Errorf("答え: %s", got)
		}
	})
}

// 手元の origin/HEAD は、ホストの既定ブランチに向け直す（綴り違いだけの名前は同じ名前）。取ってこられなければ向け直さない。
func TestRetargetTheHostDefault(t *testing.T) {
	symref := func(name string) string { return "ref: refs/heads/" + name + "\tHEAD\n" }
	originHead := func(f *fetchFixture) string {
		cmd := exec.Command("git", "-C", f.repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
		cmd.Env = append(os.Environ(), testkit.GitEnv()...)
		out, _ := cmd.Output()
		return strings.TrimSpace(string(out))
	}
	setup := func(t *testing.T) *fetchFixture {
		f := newFetch(t)
		f.git(f.repo, "push", "-q", "origin", "main:trunk")
		f.git(f.repo, "fetch", "-q", "origin")
		return f
	}
	t.Run("a changed host default asks to fetch the new name, then moves origin/HEAD", func(t *testing.T) {
		f := setup(t)
		var a answer
		retargetHost(&a, f.repo, symref("trunk"), false)
		if a.String() != "next fetch trunk\n" || originHead(f) != "refs/remotes/origin/main" {
			t.Errorf("取る前: %q %s", a.String(), originHead(f))
		}
		a = answer{}
		retargetHost(&a, f.repo, symref("trunk"), true)
		contains(t, outLines(&a), "ws: 統合先を main から trunk に合わせた（ホストの既定ブランチ）")
		if originHead(f) != "refs/remotes/origin/trunk" {
			t.Errorf("origin/HEAD = %s", originHead(f))
		}
	})
	t.Run("a default that differs only in case is the same name", func(t *testing.T) {
		f := setup(t)
		var a answer
		retargetHost(&a, f.repo, symref("Main"), false)
		retargetHost(&a, f.repo, symref("Main"), true)
		if a.String() != "" || originHead(f) != "refs/remotes/origin/main" {
			t.Errorf("答え: %q %s", a.String(), originHead(f))
		}
	})
	t.Run("a default the host does not have does not move origin/HEAD", func(t *testing.T) {
		f := newFetch(t) // origin/trunk を取っていない
		var a answer
		retargetHost(&a, f.repo, symref("trunk"), true)
		if a.String() != "" || originHead(f) != "refs/remotes/origin/main" {
			t.Errorf("答え: %q %s", a.String(), originHead(f))
		}
	})
	t.Run("an unreadable answer leaves origin/HEAD and says nothing", func(t *testing.T) {
		f := setup(t)
		var a answer
		retargetHost(&a, f.repo, "", false)
		retargetHost(&a, f.repo, "fatal: unable to access\n", true)
		if a.String() != "" || originHead(f) != "refs/remotes/origin/main" {
			t.Errorf("答え: %q %s", a.String(), originHead(f))
		}
	})
}

// 統合先は、手元の origin/HEAD（無ければ origin/main・master）の名前で進める。統合先をチェックアウトしているツリーを第 1 周が
// 見たなら、そこで済んでいる。
func TestBaseFollowsOriginHead(t *testing.T) {
	f := newFetch(t)
	f.git(f.repo, "push", "-q", "origin", "main:trunk")
	f.git(f.repo, "fetch", "-q", "origin")
	f.git(f.repo, "checkout", "-q", "-b", "claude/x")
	seen := filepath.Join(t.TempDir(), "seen")
	put(t, seen, "")
	var a answer
	base(&a, f.repo, readText(seen))
	if a.String() != "next fetch main\n" {
		t.Errorf("origin/HEAD が main のとき: %q", a.String())
	}
	f.git(f.repo, "remote", "set-head", "origin", "trunk")
	a = answer{}
	base(&a, f.repo, "")
	if a.String() != "next fetch trunk\n" {
		t.Errorf("origin/HEAD が trunk のとき: %q", a.String())
	}
	// 統合先をチェックアウトしているツリーを第 1 周が見ていれば、もう取らない
	f.git(f.repo, "checkout", "-q", "main")
	f.git(f.repo, "remote", "set-head", "origin", "main")
	a = answer{}
	base(&a, f.repo, f.repo+"\n")
	if a.String() != "" {
		t.Errorf("第 1 周が見たのに取りに行く: %q", a.String())
	}
}
