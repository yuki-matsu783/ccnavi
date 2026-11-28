package gitwrap

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/tickets/testkit"
)

// 状態で決まる Judge の答え（git の状態を読んで決まる形）を、使い捨てのリポジトリで確かめる。sh を起こさない。
// 引数だけで決まる形は judge_test.go と testdata/check.json が見る。ラッパー（sh）を通す確認は clitest/sh/git に数本だけ残す。

func gitIn(t *testing.T, dir string, args ...string) string {
	t.Helper()
	return strings.TrimRight(testkit.Git(t, dir, args...), "\r\n")
}

func writeFile(t *testing.T, path, text string) {
	t.Helper()
	testkit.Put(t, path, text)
}

// newWorkspace は、コミットが 1 件ある main のリポジトリ（ワークスペースルート兼ユーザのチェックアウト）を返す。
func newWorkspace(t *testing.T) string {
	t.Helper()
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	gitIn(t, dir, "init", "-q", "-b", "main")
	gitIn(t, dir, "config", "user.email", "t@example.invalid")
	gitIn(t, dir, "config", "user.name", "t")
	writeFile(t, filepath.Join(dir, "tracked.txt"), "line\n")
	gitIn(t, dir, "add", "tracked.txt")
	gitIn(t, dir, "commit", "-q", "-m", "seed")
	return dir
}

// addOrigin は空の bare を origin にして main を送り、取得まで済ませる（origin/main が在り、FETCH_HEAD が新しい状態）。
func addOrigin(t *testing.T, root string) string {
	t.Helper()
	bare := filepath.Join(t.TempDir(), "origin.git")
	gitIn(t, root, "init", "-q", "--bare", "-b", "main", bare)
	gitIn(t, root, "remote", "add", "origin", bare)
	gitIn(t, root, "push", "-q", "-u", "origin", "main")
	gitIn(t, root, "fetch", "-q", "origin")
	return bare
}

// pushFromElsewhere は別の clone から origin の main を 1 つ進める（手元はまだ知らない）。
func pushFromElsewhere(t *testing.T, bare string) {
	t.Helper()
	other := filepath.Join(t.TempDir(), "c")
	gitIn(t, filepath.Dir(other), "clone", "-q", bare, "c")
	gitIn(t, other, "config", "user.email", "t@example.invalid")
	gitIn(t, other, "config", "user.name", "t")
	writeFile(t, filepath.Join(other, "new.txt"), "n\n")
	gitIn(t, other, "add", "new.txt")
	gitIn(t, other, "commit", "-q", "-m", "ahead")
	gitIn(t, other, "push", "-q", "origin", "HEAD:main")
}

func envAt(root, cwd string) *Env {
	self := "sh .ccnavi/scripts/ccnavi-git.sh"
	return &Env{Names: Names{Self: self, SelfQ: self, Sync: "sh .ccnavi/scripts/ccnavi-sync.sh", Start: "sh .ccnavi/scripts/ccnavi-start.sh",
		WorktreeQ: "sh .ccnavi/scripts/ccnavi-worktree.sh"}, Root: root, Cwd: cwd,
		// i0001 だけが親のワークツリー。
		ParentTree: func(_, name string) int {
			if name == "i0001" {
				return 0
			}
			return 1
		}}
}

func denyID(a Answer) string {
	if a.Deny == nil {
		return ""
	}
	return a.Deny.ID
}

// 統合先から切る worktree add は、fetch が新しく、手元の統合先が遅れていないときだけ通す。
func TestWorktreeAddFreshBase(t *testing.T) {
	const (
		behind = "worktree-base-behind"
		stale  = "worktree-fetch-stale"
	)
	aged := func(t *testing.T, root string) {
		old := time.Now().Add(-20 * time.Minute)
		if err := os.Chtimes(filepath.Join(root, ".git", "FETCH_HEAD"), old, old); err != nil {
			t.Fatal(err)
		}
	}
	forms := [][]string{
		{"-b", "a"},
		{"-b", "a", "main"},
		{"-b", "a", "origin/main"},
	}
	for _, c := range []struct {
		name   string
		setup  func(t *testing.T, root, bare string)
		forms  [][]string // 空なら forms 全部
		id     string     // 全部がこの識別子で止まる（通すなら空）
		has    []string
		hasNot []string
		warn   string
	}{
		{name: "up to date", id: ""},
		{name: "behind", id: behind, forms: forms[:2],
			setup: func(t *testing.T, root, bare string) {
				pushFromElsewhere(t, bare)
				gitIn(t, root, "fetch", "-q", "origin")
			},
			has:    []string{"基点 main が origin/main より 1 件遅れて", "--no-track 'origin/main'", "ccnavi-worktree.sh"},
			hasNot: []string{"merge --ff-only origin"}},
		{name: "origin as the base skips the behind check", id: "", forms: forms[2:],
			setup: func(t *testing.T, root, bare string) {
				pushFromElsewhere(t, bare)
				gitIn(t, root, "fetch", "-q", "origin")
			}},
		{name: "behind then fast-forwarded", id: "",
			setup: func(t *testing.T, root, bare string) {
				pushFromElsewhere(t, bare)
				gitIn(t, root, "fetch", "-q", "origin")
				gitIn(t, root, "merge", "-q", "--ff-only", "origin/main")
			}},
		{name: "diverged", id: "worktree-base-diverged", forms: forms[1:2],
			setup: func(t *testing.T, root, bare string) {
				writeFile(t, filepath.Join(root, "local.txt"), "l\n")
				gitIn(t, root, "add", "local.txt")
				gitIn(t, root, "commit", "-q", "-m", "local only")
				pushFromElsewhere(t, bare)
				gitIn(t, root, "fetch", "-q", "origin")
			},
			has: []string{"分岐しています"}, hasNot: []string{"より 1 件遅れて"}},
		{name: "old FETCH_HEAD", id: stale,
			setup: func(t *testing.T, root, _ string) { aged(t, root) },
			has:   []string{"fetch origin 'main'"}, hasNot: []string{"merge --ff-only origin"}},
		{name: "old FETCH_HEAD then fetched", id: "", setup: func(t *testing.T, root, _ string) {
			aged(t, root)
			gitIn(t, root, "fetch", "-q", "origin")
		}},
		{name: "missing FETCH_HEAD", id: stale, forms: forms[:1],
			setup: func(t *testing.T, root, _ string) {
				if err := os.Remove(filepath.Join(root, ".git", "FETCH_HEAD")); err != nil {
					t.Fatal(err)
				}
			}},
		{name: "other bases are not checked", id: "", forms: [][]string{{"-b", "a", "other"}},
			setup: func(t *testing.T, root, _ string) {
				gitIn(t, root, "branch", "other")
				aged(t, root)
			}},
		{name: "an existing branch is not checked", id: "", forms: [][]string{{"existing"}},
			setup: func(t *testing.T, root, _ string) {
				gitIn(t, root, "branch", "existing")
				aged(t, root)
			}},
		{name: "a failed fetch downgrades the stale rejection to a warning", id: "", forms: forms[:1],
			setup: func(t *testing.T, root, _ string) {
				aged(t, root)
				writeFile(t, filepath.Join(root, ".git", FetchFailedMark), "")
			},
			warn: "オフライン"},
		{name: "offline does not hide a known lag", id: behind, forms: forms[:1],
			setup: func(t *testing.T, root, bare string) {
				pushFromElsewhere(t, bare)
				gitIn(t, root, "fetch", "-q", "origin")
				aged(t, root)
				writeFile(t, filepath.Join(root, ".git", FetchFailedMark), "")
			},
			has: []string{"1 件遅れて"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			root := newWorkspace(t)
			bare := addOrigin(t, root)
			if c.setup != nil {
				c.setup(t, root, bare)
			}
			use := c.forms
			if use == nil {
				use = forms
			}
			for _, form := range use {
				dest := ".claude/worktrees/" + form[len(form)-1]
				args := append([]string{"worktree", "add", ".claude/worktrees/a"}, form...)
				if form[0] != "-b" {
					args = []string{"worktree", "add", dest, form[0]}
				}
				a := Judge(envAt(root, root), args)
				if got := denyID(a); got != c.id {
					t.Errorf("%v: %q, want %q", args, got, c.id)
					continue
				}
				if a.Deny != nil {
					for _, h := range c.has {
						if !strings.Contains(a.Deny.Message, h) {
							t.Errorf("%v: 文に %q が無い: %s", args, h, a.Deny.Message)
						}
					}
					for _, h := range c.hasNot {
						if strings.Contains(a.Deny.Message, h) {
							t.Errorf("%v: 文に %q がある: %s", args, h, a.Deny.Message)
						}
					}
				}
				if (c.warn == "") != (a.Warn == "") || !strings.Contains(a.Warn, c.warn) {
					t.Errorf("%v: 警告 %q, want %q", args, a.Warn, c.warn)
				}
			}
		})
	}
}

// 既にあるブランチを出す形は、ブランチの種類（手元・origin・タグ・sha）と識別子の形で通すかを決める。
func TestWorktreeAddExistingBranch(t *testing.T) {
	root := newWorkspace(t)
	addOrigin(t, root)
	sha := gitIn(t, root, "rev-parse", "HEAD")
	longBranch := "feature/" + strings.Repeat("a", 51)
	gitIn(t, root, "tag", "v1")
	gitIn(t, root, "tag", "feature/t")
	gitIn(t, root, "push", "-q", "origin", "HEAD:refs/heads/i0002")
	gitIn(t, root, "fetch", "-q", "origin")
	for _, b := range []string{"other", "feature/x", "feature/8-y-05", longBranch, "feature/１２", "i0001-01-01"} {
		gitIn(t, root, "branch", b)
	}
	for _, c := range []struct {
		args string
		id   string
		has  string
	}{
		// 行き先の名前とブランチの識別子が揃えば通る。/ を含むブランチは - に置き換えた名前に出す
		{"worktree add .claude/worktrees/feature-x feature/x", "", ""},
		{"worktree add .claude/worktrees/topic-y -b topic/y HEAD", "", ""},
		{"worktree add .claude/worktrees/c -b c HEAD", "", ""},
		{"worktree add .claude/worktrees/other other", "", ""},
		{"worktree add .claude/worktrees/a other", "worktree-name", ""},
		{"worktree add .claude/worktrees/other feature/x", "worktree-name", ""},
		{"worktree add .claude/worktrees/other -b feature/x HEAD", "worktree-name", ""},
		// ブランチの外（タグ・sha）に作る形は、名前が揃っていても止める
		{"worktree add .claude/worktrees/v1 v1", "worktree-detach", "detached"},
		{"worktree add .claude/worktrees/" + sha + " " + sha, "worktree-detach", "detached"},
		{"worktree add .claude/worktrees/feature-t feature/t", "worktree-detach", ""},
		// origin にだけ在る同名のブランチは出せる
		{"worktree add .claude/worktrees/i0002 i0002", "", ""},
		// 親に使えない識別子（末尾が -<2 桁>・59 文字以上・全角）は止め、子の識別子の形は通す
		{"worktree add .claude/worktrees/feature-8-y-05 feature/8-y-05", "worktree-id", "ccnavi-start.sh --mr"},
		{"worktree add .claude/worktrees/" + strings.ReplaceAll(longBranch, "/", "-") + " " + longBranch, "worktree-id", "ccnavi-start.sh --mr"},
		{"worktree add .claude/worktrees/feature-１２ feature/１２", "worktree-id", "ccnavi-start.sh --mr"},
		{"worktree add .claude/worktrees/i0001-01-01 i0001-01-01", "", ""},
	} {
		a := Judge(envAt(root, root), strings.Fields(c.args))
		if got := denyID(a); got != c.id {
			t.Errorf("%s: %q, want %q", c.args, got, c.id)
			continue
		}
		if a.Deny != nil && !strings.Contains(a.Deny.Message, c.has) {
			t.Errorf("%s: 文に %q が無い: %s", c.args, c.has, a.Deny.Message)
		}
	}
}

// 親のワークツリーでは、自分のブランチのほかへ移らない。値をまとめた書き方・略した長いオプションでも移れない。
func TestParentWorktreeDoesNotSwitchAway(t *testing.T) {
	root := newWorkspace(t)
	parent := filepath.Join(root, ".claude", "worktrees", "i0001")
	free := filepath.Join(root, ".claude", "worktrees", "free")
	child := filepath.Join(root, ".claude", "worktrees", "i0001-01-01")
	gitIn(t, root, "worktree", "add", "-q", parent, "-b", "i0001")
	gitIn(t, root, "worktree", "add", "-q", free, "-b", "free")
	gitIn(t, root, "worktree", "add", "-q", child, "-b", "i0001-01-01")
	const switched = "parent-worktree-switch"
	for _, c := range []struct {
		cwd  string
		args string
		id   string
	}{
		{parent, "checkout main", switched},
		{parent, "switch main", switched},
		{parent, "checkout -b other", switched},
		{parent, "switch --create other", switched},
		{parent, "switch --create=other", switched},
		{parent, "checkout --orphan other", switched},
		{parent, "checkout --detach", switched},
		{parent, "switch --detach", switched},
		{parent, "switch -d HEAD", switched},
		{parent, "checkout -", switched},
		{parent, "checkout HEAD~0", switched},
		{parent, "checkout -bnew1", switched},
		{parent, "checkout -qbnew3", switched},
		{parent, "switch -cbar", switched},
		{parent, "switch -qcbar", switched},
		{parent, "checkout --orphan=x", switched},
		{parent, "checkout --track origin/x", switched},
		{parent, "checkout refs/heads/main", switched},
		{parent, "switch --det", "option-not-allowed"}, // 略した長いオプションは許可リストで止める
		{parent, "checkout --orph x", "option-not-allowed"},
		// 自分のブランチ・HEAD・パスの復元は通す
		{parent, "checkout i0001", ""},
		{parent, "checkout HEAD tracked.txt", ""},
		{parent, "checkout", ""},
		{parent, "checkout -q HEAD", ""},
		// 親でないワークツリー（子を含む）は今までどおり移れる
		{free, "checkout -b elsewhere", ""},
		{free, "checkout -qbelsewhere", ""},
		{child, "checkout -b elsewhere", ""},
	} {
		a := Judge(envAt(root, c.cwd), strings.Fields(c.args))
		if got := denyID(a); got != c.id {
			t.Errorf("%s: %s: %q, want %q", filepath.Base(c.cwd), c.args, got, c.id)
			continue
		}
		if c.id == switched && !strings.Contains(a.Deny.Message, "親のワークツリー") {
			t.Errorf("%s: 文に親のワークツリーが無い: %s", c.args, a.Deny.Message)
		}
	}
}

// 固定のリストに無い名前の統合先（origin/HEAD の指す先）へも直接は送らない。統合先は、送るツリーが属するリポジトリごとに決める。
func TestPushToTheIntegrationBranch(t *testing.T) {
	const branch = "develop-v1.0.0"
	pointOriginHead := func(t *testing.T, repo, name string) {
		gitIn(t, repo, "update-ref", "refs/remotes/origin/"+name, "HEAD")
		gitIn(t, repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/"+name)
	}
	project := func(t *testing.T, root, name string) string {
		dir := filepath.Join(root, "projects", "app")
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		gitIn(t, dir, "init", "-q")
		gitIn(t, dir, "config", "user.email", "t@example.invalid")
		gitIn(t, dir, "config", "user.name", "t")
		gitIn(t, dir, "commit", "-q", "--allow-empty", "-m", "seed")
		gitIn(t, dir, "checkout", "-q", "-b", name)
		gitIn(t, dir, "remote", "add", "origin", filepath.Join(t.TempDir(), "app.git"))
		return dir
	}
	for _, c := range []struct {
		name  string
		setup func(t *testing.T, root string) (cwd, branch string)
		deny  bool
	}{
		{"an undetermined integration keeps the fixed list only", func(t *testing.T, root string) (string, string) {
			gitIn(t, root, "remote", "add", "origin", filepath.Join(t.TempDir(), "o.git"))
			gitIn(t, root, "checkout", "-q", "-b", branch)
			return root, branch
		}, false},
		{"the branch origin/HEAD points to is refused", func(t *testing.T, root string) (string, string) {
			gitIn(t, root, "remote", "add", "origin", filepath.Join(t.TempDir(), "o.git"))
			gitIn(t, root, "checkout", "-q", "-b", branch)
			pointOriginHead(t, root, branch)
			return root, branch
		}, true},
		{"the fixed list holds beside origin/HEAD", func(t *testing.T, root string) (string, string) {
			gitIn(t, root, "remote", "add", "origin", filepath.Join(t.TempDir(), "o.git"))
			pointOriginHead(t, root, branch)
			return root, "main"
		}, true},
		{"a project is judged by its own integration", func(t *testing.T, root string) (string, string) {
			gitIn(t, root, "remote", "add", "origin", filepath.Join(t.TempDir(), "o.git"))
			pointOriginHead(t, root, branch) // ワークスペースの統合先が同じ名前でも、プロジェクトの統合先（main）とは別
			p := project(t, root, branch)
			pointOriginHead(t, p, "main")
			return p, branch
		}, false},
		{"a project whose own integration is the branch is refused", func(t *testing.T, root string) (string, string) {
			p := project(t, root, branch)
			pointOriginHead(t, p, branch)
			return p, branch
		}, true},
		{"each repository has its own integration (project)", func(t *testing.T, root string) (string, string) {
			gitIn(t, root, "remote", "add", "origin", filepath.Join(t.TempDir(), "o.git"))
			pointOriginHead(t, root, "main")
			p := project(t, root, "trunk")
			pointOriginHead(t, p, "trunk")
			return p, "trunk"
		}, true},
		{"a branch spelled differently from the integration is refused", func(t *testing.T, root string) (string, string) {
			p := project(t, root, "Trunk")
			pointOriginHead(t, p, "trunk")
			return p, "Trunk"
		}, true},
		{"each repository has its own integration (workspace)", func(t *testing.T, root string) (string, string) {
			gitIn(t, root, "remote", "add", "origin", filepath.Join(t.TempDir(), "o.git"))
			pointOriginHead(t, root, "main")
			project(t, root, "trunk")
			gitIn(t, root, "checkout", "-q", "-b", "trunk")
			return root, "trunk"
		}, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			root := newWorkspace(t)
			cwd, b := c.setup(t, root)
			a := Judge(envAt(root, cwd), []string{"push", "-u", "origin", b})
			want := ""
			if c.deny {
				want = "push-integration-branch"
			}
			if got := denyID(a); got != want {
				t.Fatalf("%q, want %q", got, want)
			}
			if c.deny && !strings.Contains(a.Deny.Message, b+" は統合先です") {
				t.Errorf("文: %s", a.Deny.Message)
			}
		})
	}
}

// 統合先をチェックアウトしているユーザのチェックアウトでは、merge も pull も統合先以外を取り込めない。
// ワークツリーの中の merge は、形の指定が無ければ --no-ff を補う。
func TestMergeAndPullOnTheTarget(t *testing.T) {
	root := newWorkspace(t)
	addOrigin(t, root)
	gitIn(t, root, "branch", "fa")
	sub := filepath.Join(root, "sub")
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	wt := filepath.Join(root, ".claude", "worktrees", "wt")
	gitIn(t, root, "worktree", "add", "-q", wt, "-b", "wt")
	wtSub := filepath.Join(wt, "sub")
	if err := os.MkdirAll(wtSub, 0o755); err != nil {
		t.Fatal(err)
	}
	const onTarget = "merge-on-target"
	for _, c := range []struct {
		cwd     string
		args    string
		id      string
		prepend string // 補う語
	}{
		// 統合先のユーザのチェックアウトの merge
		{root, "merge fa", onTarget, ""},
		{root, "merge fa -m msg", onTarget, ""},
		{root, "merge --ff-only fa", onTarget, ""},
		{root, "merge --squash fa", onTarget, ""},
		{root, "merge --no-commit fa", onTarget, ""},
		{root, "merge --no-ff fa", onTarget, ""},
		{root, "merge FETCH_HEAD", onTarget, ""},
		{sub, "merge fa", onTarget, ""},
		{root, "merge --no-ff fa -- --abort", onTarget, ""}, // `--` の後ろの --abort は枝の名前
		{root, "merge --abort", "", ""},
		{root, "merge --continue", "", ""},
		{root, "merge --quit", "", ""},
		// ワークツリーの中の merge は通し、形の指定が無ければ --no-ff を補う
		{wt, "merge main --no-edit", "", "--no-ff"},
		{wt, "merge main -m msg", "", "--no-ff"},
		{wt, "merge -m --ff main", "", "--no-ff"}, // -m の値の --ff は形のオプションではない
		{wt, "merge --no-commit main", "", "--no-ff"},
		{wtSub, "merge main --no-edit", "", "--no-ff"},
		{wt, "merge --ff-only main", "", ""},
		{wt, "merge --ff main", "", ""},
		{wt, "merge --no-ff main", "", ""},
		{wt, "merge --squash main", "", ""},
		// pull: 統合先自身の更新は通し、別のブランチ・リモートは止める
		{root, "pull --ff-only", "", ""},
		{root, "pull origin", "", ""},
		{root, "pull --ff-only origin main", "", ""},
		{root, "pull origin feat", onTarget, ""},
		{root, "pull --no-ff origin feat", onTarget, ""},
		{root, "pull --ff-only origin feat", onTarget, ""},
		{root, "pull --no-commit origin feat", onTarget, ""},
		{root, "pull origin main feat", onTarget, ""},
		{root, "pull origin HEAD", onTarget, ""},
		{root, "pull origin refs/heads/main", onTarget, ""},
		{root, "pull origin -- feat", onTarget, ""},
		{root, "pull origin Main", onTarget, ""},
		{root, "pull upstream main", onTarget, ""},
		{root, "pull ../other.git main", onTarget, ""},
		{root, "pull --ff-only origin main next", onTarget, ""},
		{wt, "pull --no-edit origin main", "", ""}, // ワークツリーは統合先のチェックアウトではない
		{wt, "pull --no-edit origin feat", "", ""},
	} {
		a := Judge(envAt(root, c.cwd), strings.Fields(c.args))
		if got := denyID(a); got != c.id {
			t.Errorf("%s: %s: %q, want %q", filepath.Base(c.cwd), c.args, got, c.id)
			continue
		}
		if a.Deny != nil {
			// 次の一手の文は文面ファイル（.ccnavi/messages/）にあり、文面の検査が見る。
			if !strings.Contains(a.Deny.Message, "統合先 main のユーザのチェックアウトでは") {
				t.Errorf("%s: 文: %s", c.args, a.Deny.Message)
			}
		}
		if got := strings.Join(a.Prepend, " "); got != c.prepend {
			t.Errorf("%s: %s: 補う語 %q, want %q", filepath.Base(c.cwd), c.args, got, c.prepend)
		}
	}

	// pull の答えは、統合先のチェックアウトなら統合先の名前（衝突の案内を足す印）を持つ。ワークツリーは持たない。
	if a := Judge(envAt(root, root), []string{"pull", "--ff-only"}); a.Target != "main" {
		t.Errorf("統合先で Target = %q", a.Target)
	}
	if a := Judge(envAt(root, wt), []string{"pull", "--no-edit", "origin", "main"}); a.Target != "" {
		t.Errorf("ワークツリーで Target = %q", a.Target)
	}

	// 上流が別のブランチなら、引数なしの pull も取り込む形と同じ。branch.<名>.remote が別名なら、その名前で更新を通す。
	gitIn(t, root, "config", "branch.main.merge", "refs/heads/feat")
	for _, args := range [][]string{{"pull"}, {"pull", "origin"}} {
		a := Judge(envAt(root, root), args)
		if denyID(a) != onTarget || !strings.Contains(a.Deny.Message, "上流 feat") {
			t.Errorf("%v: %v", args, a.Deny)
		}
	}
	gitIn(t, root, "config", "branch.main.merge", "refs/heads/main")
	gitIn(t, root, "remote", "add", "up", gitIn(t, root, "remote", "get-url", "origin"))
	gitIn(t, root, "config", "branch.main.remote", "up")
	if a := Judge(envAt(root, root), []string{"pull", "--ff-only", "up", "main"}); a.Deny != nil {
		t.Errorf("up main: %v", a.Deny)
	}
	if a := Judge(envAt(root, root), []string{"pull", "--ff-only", "origin", "main"}); denyID(a) != onTarget {
		t.Errorf("origin main（上流は up）: %v", a.Deny)
	}

	// ブランチの外・ほかのブランチ・統合先が決まらないときは、ユーザのチェックアウトでも止めない。
	gitIn(t, root, "checkout", "-q", "--detach")
	for _, args := range []string{"merge fa --no-edit", "pull --no-edit origin feat"} {
		if a := Judge(envAt(root, root), strings.Fields(args)); a.Deny != nil {
			t.Errorf("detached %s: %v", args, a.Deny)
		}
	}
	gitIn(t, root, "checkout", "-q", "-b", "scratch")
	for _, args := range []string{"merge fa --no-edit", "pull --no-edit origin feat"} {
		if a := Judge(envAt(root, root), strings.Fields(args)); a.Deny != nil {
			t.Errorf("scratch %s: %v", args, a.Deny)
		}
	}
	gitIn(t, root, "checkout", "-q", "main")
	gitIn(t, root, "update-ref", "-d", "refs/remotes/origin/main")
	for _, args := range []string{"merge fa --no-edit", "pull --no-edit origin feat"} {
		if a := Judge(envAt(root, root), strings.Fields(args)); a.Deny != nil {
			t.Errorf("undecidable %s: %v", args, a.Deny)
		}
	}
}
