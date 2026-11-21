package cut

import (
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// ccnavi-worktree.sh が起こす 2 回（Plan のあと、fetch と ls-remote を済ませて Cut）を、使い捨てのワークスペースと bare の origin
// で通す。sh は起こさない。ずれ（C4〜C7）の報告・確認コードが縛るもの・書きかけの扱い・切る前に止まる判断を確かめる。
// sh を通す確認は tests/sh/test_worktree_sh.py に、経路ごとに数本だけ残す。

type flowFixture struct {
	t        *testing.T
	root     string // ワークスペース（ユーザのチェックアウト）
	bare     string // origin
	other    string // origin を進めるための別の clone
	leftover func(path, name string) Leftover
}

func gitIn(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimRight(string(out), "\r\n")
}

func put(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

// newFlow は、main を origin に送った状態のワークスペースを組む（.claude/ は .gitignore 済み）。
func newFlow(t *testing.T) *flowFixture {
	t.Helper()
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	f := &flowFixture{t: t, root: filepath.Join(base, "ws"), bare: filepath.Join(base, "origin.git"), other: filepath.Join(base, "other"),
		leftover: func(string, string) Leftover { return Leftover{} }}
	if err := os.MkdirAll(f.root, 0o755); err != nil {
		t.Fatal(err)
	}
	gitIn(t, base, "init", "-q", "--bare", "-b", "main", f.bare)
	gitIn(t, base, "init", "-q", "-b", "main", f.root)
	gitIn(f.t, f.root, "config", "user.email", "t@example.invalid")
	gitIn(f.t, f.root, "config", "user.name", "t")
	put(f.t, filepath.Join(f.root, ".gitignore"), "logs/\n.ccnavi/\n.claude/\n")
	gitIn(f.t, f.root, "add", ".gitignore")
	gitIn(f.t, f.root, "commit", "-q", "-m", "seed")
	gitIn(f.t, f.root, "remote", "add", "origin", f.bare)
	gitIn(f.t, f.root, "push", "-q", "origin", "main")
	gitIn(f.t, f.root, "fetch", "-q", "origin")
	gitIn(f.t, f.root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	testkit.CopyMessages(t, f.root)
	return f
}

func (f *flowFixture) wt(name string) string {
	return filepath.Join(f.root, ".claude", "worktrees", name)
}

// fetched は sh が取ってくる origin の様子（fetch が通り、ls-remote が通った）。
func (f *flowFixture) fetched(branch string) Remote {
	f.t.Helper()
	cmd := exec.Command("git", "fetch", "-q", "origin", branch)
	cmd.Dir = f.root
	if out, err := cmd.CombinedOutput(); err != nil {
		return Remote{FetchOK: false, FetchOut: string(out)} // origin に無いブランチは、取ってこられない（sh も同じ答えを渡す）
	}
	return Remote{FetchOK: true, HeadsOK: true, Heads: gitIn(f.t, f.root, "ls-remote", "--heads", "origin") + "\n"}
}

// run は sh と同じ順（Plan のあと、切る道なら取ってきて Cut）で通し、答えの全文を返す。remote を渡すと取ってきた結果にする。
func (f *flowFixture) run(cwd string, remote *Remote, args ...string) string {
	f.t.Helper()
	if cwd == "" {
		cwd = f.root
	}
	conf, _ := config.Load(f.root)
	in := Input{Root: f.root, Cwd: cwd, Conf: conf, Args: args, Leftover: f.leftover}
	plan := Plan(in)
	if !strings.Contains(plan, "\nfetch ") && !strings.HasPrefix(plan, "fetch ") {
		return plan
	}
	fetch := plan[strings.Index(plan, "fetch ")+len("fetch "):]
	branch, _, _ := strings.Cut(fetch, "\n")
	r := remote
	if r == nil {
		got := f.fetched(branch)
		r = &got
	}
	return Cut(in, *r)
}

var acceptCode = regexp.MustCompile(`--accept ([0-9a-f]{8})`)

func acceptOf(t *testing.T, out string) string {
	t.Helper()
	m := acceptCode.FindStringSubmatch(out)
	if m == nil {
		t.Fatalf("確認コードが無い:\n%s", out)
	}
	return m[1]
}

func (f *flowFixture) record(name string) map[string]string {
	f.t.Helper()
	data, err := os.ReadFile(filepath.Join(f.root, ".git", "ccnavi-direct", name))
	if err != nil {
		f.t.Fatal(err)
	}
	rec := map[string]string{}
	for _, line := range strings.Split(string(data), "\n") {
		if k, v, ok := strings.Cut(line, " "); ok {
			rec[k] = v
		}
	}
	return rec
}

func (f *flowFixture) head(rev string) string { return gitIn(f.t, f.root, "rev-parse", rev) }

func (f *flowFixture) cutOff(name string) bool {
	_, err := os.Stat(f.wt(name))
	return err != nil
}

func (f *flowFixture) commit(file, text string) {
	put(f.t, filepath.Join(f.root, file), text)
	gitIn(f.t, f.root, "add", file)
	gitIn(f.t, f.root, "commit", "-q", "-m", file)
}

// advanceOrigin は origin の main に別の clone から 1 コミット足す（手元の main はそのまま。取ってくるのは run）。
func (f *flowFixture) advanceOrigin() {
	t := f.t
	if _, err := os.Stat(f.other); err != nil {
		gitIn(t, filepath.Dir(f.other), "clone", "-q", f.bare, f.other)
		gitIn(f.t, f.other, "config", "user.email", "t@example.invalid")
		gitIn(f.t, f.other, "config", "user.name", "t")
	} else {
		gitIn(f.t, f.other, "pull", "-q", "--ff-only", "origin", "main")
	}
	n := gitIn(f.t, f.other, "rev-list", "--count", "HEAD")
	put(f.t, filepath.Join(f.other, "new.txt"), "n"+n)
	gitIn(f.t, f.other, "add", "new.txt")
	gitIn(f.t, f.other, "commit", "-q", "-m", "ahead")
	gitIn(f.t, f.other, "push", "-q", "origin", "main")
}

// makeFeatureWithOrigin は取り込み先を統合先でないブランチへ移し、origin にも同じ位置で置く（C5 だけにヒットする）。
func (f *flowFixture) makeFeatureWithOrigin() {
	gitIn(f.t, f.root, "checkout", "-q", "-b", "feature/x")
	gitIn(f.t, f.root, "push", "-q", "origin", "feature/x")
	gitIn(f.t, f.root, "fetch", "-q", "origin")
}

// dirtyRoot は、追跡済みの変更 1・新規 1・staged 1 の書きかけを作る。
func (f *flowFixture) dirtyRoot() {
	f.commit("tracked.txt", "v1\n")
	gitIn(f.t, f.root, "push", "-q", "origin", "main")
	put(f.t, filepath.Join(f.root, "tracked.txt"), "v2 書きかけ\n")
	put(f.t, filepath.Join(f.root, "fresh.txt"), "新規\n")
	put(f.t, filepath.Join(f.root, "staged.txt"), "staged\n")
	gitIn(f.t, f.root, "add", "staged.txt")
}

func has(t *testing.T, out string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !strings.Contains(out, w) {
			t.Errorf("答えに %q が無い:\n%s", w, out)
		}
	}
}

func hasNot(t *testing.T, out string, not ...string) {
	t.Helper()
	for _, w := range not {
		if strings.Contains(out, w) {
			t.Errorf("答えに %q がある:\n%s", w, out)
		}
	}
}

// 確認コードは、確認した状態のどれが動いても合わなくなる（動いたら報告からやり直す）。
func TestConfirmCodeBindsTheState(t *testing.T) {
	const stale = "reason confirm-code"
	t.Run("the same state accepts its code", func(t *testing.T) {
		f := newFlow(t)
		f.makeFeatureWithOrigin()
		c := acceptOf(t, f.run("", nil, "topic"))
		done := f.run("", nil, "topic", "--accept", c)
		has(t, done, "ワークツリーを切った")
		if f.cutOff("topic") {
			t.Error("切れていない")
		}
	})
	for _, c := range []struct {
		name  string
		setup func(f *flowFixture) // 最初に聞く前
		move  func(f *flowFixture) // 聞いたあとの動き
		cwd   func(f *flowFixture) []string
	}{
		{name: "the state moves", setup: (*flowFixture).makeFeatureWithOrigin, move: func(f *flowFixture) {
			f.commit("more.txt", "m")
			gitIn(f.t, f.root, "push", "-q", "origin", "feature/x")
		}},
		{name: "the head when origin has no such branch", setup: func(f *flowFixture) {
			gitIn(f.t, f.root, "checkout", "-q", "-b", "local-only")
		}, move: func(f *flowFixture) { f.commit("more.txt", "m") }},
		{name: "the origin sha", setup: (*flowFixture).advanceOrigin, move: (*flowFixture).advanceOrigin},
		{name: "an edit of the wip after the confirmation", setup: (*flowFixture).dirtyRoot, move: func(f *flowFixture) {
			put(f.t, filepath.Join(f.root, "fresh.txt"), "確認のあとの編集\n")
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			f := newFlow(t)
			c.setup(f)
			args := []string{"topic"}
			if strings.Contains(c.name, "wip") {
				args = append(args, "--copy-wip")
			}
			first := f.run("", nil, "topic")
			accept := acceptOf(t, first)
			c.move(f)
			out := f.run("", nil, append(args, "--accept", accept)...)
			has(t, out, stale, "exit 3")
			if !f.cutOff("topic") {
				t.Error("合わないコードで切れている")
			}
		})
	}
	t.Run("where it was run from", func(t *testing.T) {
		f := newFlow(t)
		f.makeFeatureWithOrigin()
		a, b := filepath.Join(filepath.Dir(f.root), "side-a"), filepath.Join(filepath.Dir(f.root), "side-b")
		gitIn(f.t, f.root, "worktree", "add", "-q", a, "-b", "side-a")
		gitIn(f.t, f.root, "worktree", "add", "-q", b, "-b", "side-b")
		first := f.run(a, nil, "topic", "--base", "feature/x")
		has(t, first, "reason confirm-accept")
		out := f.run(b, nil, "topic", "--base", "feature/x", "--accept", acceptOf(t, first))
		has(t, out, stale)
	})
	t.Run("whether the remote was verified", func(t *testing.T) {
		f := newFlow(t)
		f.makeFeatureWithOrigin()
		heads := f.fetched("feature/x")
		unverified := Remote{FetchOK: true, HeadsOK: false, HeadsErr: "fatal: unable to access 'https://example.invalid/': timed out\n"}
		first := f.run("", &unverified, "topic")
		has(t, first, "リモート未確認", "exit 3")
		out := f.run("", &heads, "topic", "--accept", acceptOf(t, first))
		has(t, out, stale)
		if !f.cutOff("topic") {
			t.Error("切れている")
		}
	})
}

// 取り込み先と origin のずれ・取り込み先が統合先でないとき・cwd のブランチが違うときは、切らずに報告して止まる。
// 確認コードを付けて打ち直すと切れ、記録に確認コードを残す。
func TestCutAsksBeforeCutting(t *testing.T) {
	t.Run("local behind origin is cut from local after accept", func(t *testing.T) {
		f := newFlow(t)
		f.advanceOrigin()
		ahead := f.head("refs/remotes/origin/main")
		first := f.run("", nil, "topic")
		has(t, first, "origin/main より 1 件遅れている", "git pull --ff-only origin 'main'", "古いコードの上で作業する", "reason confirm-accept")
		done := f.run("", nil, "topic", "--accept", acceptOf(t, first))
		has(t, done, "ワークツリーを切った", "案内: 手元の main は origin/main より古い")
		if got := gitIn(f.t, f.wt("topic"), "rev-parse", "HEAD"); got == f.head("refs/remotes/origin/main") || got != f.head("main") {
			t.Errorf("origin からは切らない: %s（origin %s）", got, ahead)
		}
	})
	t.Run("local ahead of origin", func(t *testing.T) {
		f := newFlow(t)
		f.commit("local.txt", "l")
		first := f.run("", nil, "topic")
		has(t, first, "origin/main より 1 件進んでいる")
		has(t, f.run("", nil, "topic", "--accept", acceptOf(t, first)), "ワークツリーを切った")
	})
	t.Run("diverged local offers both ways to match", func(t *testing.T) {
		f := newFlow(t)
		f.commit("local.txt", "l")
		f.advanceOrigin()
		out := f.run("", nil, "topic")
		has(t, out, "分岐している（先行 1 件・遅れ 1 件）", "git pull --rebase origin 'main'", "git pull --no-rebase origin 'main'")
		if !f.cutOff("topic") {
			t.Error("切れている")
		}
	})
	t.Run("a branch missing on origin", func(t *testing.T) {
		f := newFlow(t)
		gitIn(f.t, f.root, "checkout", "-q", "-b", "local-only")
		first := f.run("", nil, "topic")
		has(t, first, "origin に local-only が無い")
		has(t, f.run("", nil, "topic", "--accept", acceptOf(t, first)), "ワークツリーを切った")
	})
	t.Run("a fetch failure is an unverified remote", func(t *testing.T) {
		f := newFlow(t)
		failed := Remote{FetchOK: false, FetchOut: "fatal: unable to access 'https://example.invalid/': Could not resolve host\n"}
		first := f.run("", &failed, "topic")
		has(t, first, "リモート未確認", "Could not resolve host", "reason confirm-accept")
		if !f.cutOff("topic") {
			t.Error("切らずに止まる")
		}
		done := f.run("", &failed, "topic", "--accept", acceptOf(t, first))
		has(t, done, "リモート未確認で切った", "ユーザにも伝える")
		if got := gitIn(f.t, f.wt("topic"), "rev-parse", "HEAD"); got != f.head("main") {
			t.Errorf("起点 %s", got)
		}
	})
	t.Run("a missing remote branch is called out", func(t *testing.T) {
		f := newFlow(t)
		failed := Remote{FetchOK: false, FetchOut: "fatal: couldn't find remote ref main\n"}
		has(t, f.run("", &failed, "topic"), "origin に main が無い", "exit 3")
	})
	t.Run("an unreadable branch list of origin is an unverified remote", func(t *testing.T) {
		f := newFlow(t)
		failed := Remote{FetchOK: true, HeadsOK: false, HeadsErr: "fatal: unable to access: timed out\n"}
		first := f.run("", &failed, "topic")
		has(t, first, "リモート未確認", "exit 3")
		has(t, f.run("", &failed, "topic", "--accept", acceptOf(t, first)), "ワークツリーを切った")
	})
	t.Run("a base other than the default branch needs the accept code", func(t *testing.T) {
		f := newFlow(t)
		f.makeFeatureWithOrigin()
		wrong := f.run("", nil, "topic", "--accept", "deadbeef")
		has(t, wrong, "確認コードが今の状態と合わない", "exit 3")
		first := f.run("", nil, "topic")
		has(t, first, "取り込み先 feature/x (", "リモートのデフォルトは main", "取り込み先がデフォルトブランチ（main）でない")
		hasNot(t, first, "書きかけ:")
		c := acceptOf(t, first)
		has(t, f.run("", nil, "topic", "--accept", c), "ワークツリーを切った")
		if got := gitIn(f.t, f.wt("topic"), "rev-parse", "HEAD"); got != f.head("feature/x") {
			t.Errorf("起点 %s", got)
		}
		if rec := f.record("topic"); rec["confirm"] != c || rec["base_branch"] != "feature/x" {
			t.Errorf("記録 %v", rec)
		}
	})
	t.Run("a cwd branch that differs from the base needs the base flag", func(t *testing.T) {
		f := newFlow(t)
		other := f.wt("other")
		gitIn(f.t, f.root, "worktree", "add", "-q", other, "-b", "other")
		first := f.run(other, nil, "topic")
		has(t, first, "cwd のブランチは other で、取り込み先と違う", "--base 'main'", "reason confirm-base")
		if !f.cutOff("topic") {
			t.Error("切れている")
		}
		has(t, f.run(other, nil, "topic", "--base", "main"), "ワークツリーを切った")
		if got := gitIn(f.t, f.wt("topic"), "rev-parse", "HEAD"); got != f.head("main") {
			t.Errorf("起点 %s", got)
		}
	})
	t.Run("a base that is not the current base is refused", func(t *testing.T) {
		f := newFlow(t)
		has(t, f.run("", nil, "topic", "--base", "feature/y"), "fail base-mismatch 1", "今の取り込み先（main")
	})
}

// 書きかけ（ユーザのチェックアウトの未コミットの変更）は、選ばないと切らない。--copy-wip なら確認した時点のスナップショットが
// 起点になり、ユーザの index とワークツリーは変えない。数えないのは .claude/worktrees/ の置き場だけ。
func TestCutWip(t *testing.T) {
	snapshot := func(f *flowFixture) string {
		return gitIn(f.t, f.root, "status", "--porcelain=v1", "--untracked-files=all") + "\n" + gitIn(f.t, f.root, "diff", "--cached") + "\n" +
			gitIn(f.t, f.root, "diff") + "\n" + f.head("HEAD")
	}
	t.Run("a wip without a choice stops even with the code", func(t *testing.T) {
		f := newFlow(t)
		f.dirtyRoot()
		before := snapshot(f)
		first := f.run("", nil, "topic")
		has(t, first, "書きかけ: 変更 2・新規 1（staged 1", "--no-wip", "--copy-wip", "reason confirm-wip")
		if snapshot(f) != before {
			t.Error("聞くだけなのに index かワークツリーが変わった")
		}
		again := f.run("", nil, "topic", "--accept", acceptOf(t, first))
		has(t, again, "reason confirm-wip")
		if !f.cutOff("topic") {
			t.Error("切れている")
		}
	})
	t.Run("no-wip cuts from head and leaves the wip only in the root", func(t *testing.T) {
		f := newFlow(t)
		f.dirtyRoot()
		c := acceptOf(t, f.run("", nil, "topic"))
		before := snapshot(f)
		done := f.run("", nil, "topic", "--accept", c, "--no-wip")
		has(t, done, "書きかけは含まない")
		if got := gitIn(f.t, f.wt("topic"), "rev-parse", "HEAD"); got != f.head("HEAD") {
			t.Errorf("起点 %s", got)
		}
		if _, err := os.Stat(filepath.Join(f.wt("topic"), "fresh.txt")); err == nil {
			t.Error("書きかけがコピーされている")
		}
		if got, _ := os.ReadFile(filepath.Join(f.wt("topic"), "tracked.txt")); string(got) != "v1\n" {
			t.Errorf("tracked.txt = %q", got)
		}
		if snapshot(f) != before || f.record("topic")["snap_wip"] != "no" {
			t.Errorf("ルートが変わった、または記録 %v", f.record("topic"))
		}
	})
	t.Run("copy-wip takes the wip at the time of confirmation", func(t *testing.T) {
		f := newFlow(t)
		f.dirtyRoot()
		c := acceptOf(t, f.run("", nil, "topic"))
		pending := f.head("refs/ccnavi/direct/topic/pending")
		before := snapshot(f)
		done := f.run("", nil, "topic", "--accept", c, "--copy-wip")
		has(t, done, "確認した時点の書きかけを含む")
		if got := gitIn(f.t, f.wt("topic"), "rev-parse", "HEAD"); got != pending || f.head("refs/ccnavi/direct/topic/s") != pending || f.head(pending+"^") != f.head("HEAD") {
			t.Errorf("起点 %s（候補 %s）", got, pending)
		}
		for rel, want := range map[string]string{"tracked.txt": "v2 書きかけ\n", "fresh.txt": "新規\n", "staged.txt": "staged\n"} {
			if got, _ := os.ReadFile(filepath.Join(f.wt("topic"), rel)); string(got) != want {
				t.Errorf("%s = %q", rel, got)
			}
		}
		if rec := f.record("topic"); rec["snap_wip"] != "yes" || rec["snap_sha"] != pending {
			t.Errorf("記録 %v", rec)
		}
		if snapshot(f) != before {
			t.Error("ユーザの index かワークツリーが変わった")
		}
		if exec.Command("git", "-C", f.root, "show-ref", "--verify", "--quiet", "refs/ccnavi/direct/topic/pending").Run() == nil {
			t.Error("切ったのに pending が残っている")
		}
	})

	// .claude/worktrees/ は書きかけに数えない。.gitignore に行が無い配布先は、最初のワークツリーを切ると置き場が未追跡で残る。
	leave := func(t *testing.T, ignore string) *flowFixture {
		f := newFlow(t)
		put(f.t, filepath.Join(f.root, ".gitignore"), "logs/\n.ccnavi/\n"+ignore)
		put(f.t, filepath.Join(f.root, "README.md"), "# readme\n")
		gitIn(f.t, f.root, "add", ".gitignore", "README.md")
		gitIn(f.t, f.root, "commit", "-q", "-m", "gitignore")
		gitIn(f.t, f.root, "push", "-q", "origin", "main")
		has(t, f.run("", nil, "first"), "ワークツリーを切った")
		return f
	}
	t.Run("the worktrees dir alone is not wip", func(t *testing.T) {
		f := leave(t, "")
		if !strings.Contains(gitIn(f.t, f.root, "status", "--porcelain", "--untracked-files=all"), "?? .claude/worktrees/") {
			t.Fatal("前提: 置き場が未追跡で残っている")
		}
		hasNot(t, f.run("", nil, "second", "--dry-run"), "書きかけ")
		has(t, f.run("", nil, "second"), "ワークツリーを切った")
	})
	t.Run("the count leaves out only the worktrees dir", func(t *testing.T) {
		f := leave(t, "")
		put(f.t, filepath.Join(f.root, ".claude", "worktrees", "stray.txt"), "s\n")
		gitIn(f.t, f.root, "add", "-f", ".claude/worktrees/stray.txt")
		put(f.t, filepath.Join(f.root, "wip", "handoff", "a.md"), "a\n")
		has(t, f.run("", nil, "second", "--dry-run"), "書きかけ 変更 0・新規 1（staged 0）")
	})
	for _, c := range []struct {
		name, ignore string
	}{{"the worktrees dir untracked", ""}, {"the worktrees dir ignored", ".claude/worktrees/\n"}} {
		t.Run("the snapshot does not take the other worktrees: "+c.name, func(t *testing.T) {
			f := leave(t, c.ignore)
			put(f.t, filepath.Join(f.root, "README.md"), "# readme 書きかけ\n")
			put(f.t, filepath.Join(f.root, "notes.txt"), "メモ\n")
			asked := f.run("", nil, "second")
			has(t, asked, "書きかけ: 変更 1・新規 1（staged 0", "reason confirm-wip")
			hasNot(t, asked, "作れませんでした")
			done := f.run("", nil, "second", "--accept", acceptOf(t, asked), "--copy-wip")
			has(t, done, "ワークツリーを切った")
			tree := gitIn(f.t, f.wt("second"), "ls-tree", "-r", "HEAD")
			if !strings.Contains(tree, "notes.txt") || strings.Contains(tree, ".claude/worktrees") || strings.Contains(tree, "160000") {
				t.Errorf("スナップショットの木:\n%s", tree)
			}
			if got, _ := os.ReadFile(filepath.Join(f.wt("second"), "README.md")); string(got) != "# readme 書きかけ\n" {
				t.Errorf("README.md = %q", got)
			}
		})
	}
	t.Run("an ignored worktrees dir and a new file with no-wip", func(t *testing.T) {
		f := leave(t, ".claude/worktrees/\n")
		put(f.t, filepath.Join(f.root, "notes.txt"), "メモ\n")
		c := acceptOf(t, f.run("", nil, "second"))
		has(t, f.run("", nil, "second", "--accept", c, "--no-wip"), "ワークツリーを切った")
		if _, err := os.Stat(filepath.Join(f.wt("second"), "notes.txt")); err == nil {
			t.Error("書きかけがコピーされている")
		}
	})
}

// 切る前に止まる判断（C1〜C3・同じ識別子・既にあるもの・親のワークツリー・終わっていない取り込み・--dry-run）。
func TestCutRefusals(t *testing.T) {
	for _, c := range []struct {
		name  string
		setup func(f *flowFixture)
		cwd   func(f *flowFixture) string
		args  []string
		has   []string
		after func(t *testing.T, f *flowFixture)
		// placeTaken は、行き先が初めからある（切れていないことを、行き先が無いことでは見られない）。
		placeTaken bool
	}{
		{name: "a detached main checkout", setup: func(f *flowFixture) { gitIn(f.t, f.root, "checkout", "-q", "--detach") },
			has: []string{"fail detached 1", "detached HEAD"}},
		{name: "a base tied to a ticket", setup: func(f *flowFixture) {
			put(f.t, filepath.Join(f.root, ".ccnavi", "approved", "doing", "t1.md"), "---\nticket: t1\nbranch: main\n---\n本文\n")
		}, has: []string{"fail base-bound 1", "チケットに結び付く"}},
		{name: "a marker that is left", setup: func(f *flowFixture) {
			put(f.t, filepath.Join(f.root, ".git", "ccnavi-integrate", "topic", "marker"), "version 4\nk abc\n")
		}, has: []string{"fail marker-left 1", "取り込みの記録", "integrate-done topic"}},
		{name: "a branch of the same name on origin", setup: func(f *flowFixture) {
			gitIn(f.t, f.root, "push", "-q", "origin", "main:refs/heads/topic")
		}, has: []string{"origin にブランチ topic が既にある", "ccnavi-start.sh' --mr", "ccnavi-start.sh' --issue", "reason origin-same-name"}},
		{name: "a branch of the same identifier on origin (slash)", setup: func(f *flowFixture) {
			gitIn(f.t, f.root, "push", "-q", "origin", "main:refs/heads/feature/7-x")
		}, args: []string{"feature-7-x"}, has: []string{"feature/7-x", "ccnavi-start.sh' --mr", "reason same-id"}},
		{name: "a branch of the same identifier on origin (case)", setup: func(f *flowFixture) {
			gitIn(f.t, f.root, "push", "-q", "origin", "main:refs/heads/Topic")
		}, has: []string{"Topic", "ccnavi-start.sh' --mr", "reason same-id"}},
		{name: "a local branch of the same identifier", setup: func(f *flowFixture) { gitIn(f.t, f.root, "branch", "feature/7-x") },
			args: []string{"feature-7-x"}, has: []string{"feature/7-x", "reason same-id"}},
		{name: "the same identifier is refused before the existing directory check", setup: func(f *flowFixture) {
			gitIn(f.t, f.root, "branch", "Topic")
			put(f.t, filepath.Join(f.wt("topic"), "stray.txt"), "x")
		}, placeTaken: true, has: []string{"Topic", "同じ識別子", "reason same-id"}, after: func(t *testing.T, f *flowFixture) {
			if _, err := os.Stat(filepath.Join(f.wt("topic"), "stray.txt")); err != nil {
				t.Error("既にあったファイルが消えた")
			}
		}},
		{name: "an existing directory that is not the worktree", setup: func(f *flowFixture) {
			put(f.t, filepath.Join(f.wt("topic"), "stray.txt"), "x")
		}, placeTaken: true, has: []string{"fail dest-exists 1", "ワークツリーではありません"}},
		{name: "an existing worktree of another branch", setup: func(f *flowFixture) {
			gitIn(f.t, f.root, "worktree", "add", "-q", f.wt("other"), "-b", "other")
			if err := os.Rename(f.wt("other"), f.wt("renamed-away")); err != nil {
				f.t.Fatal(err)
			}
			gitIn(f.t, f.root, "worktree", "repair", f.wt("renamed-away"))
		}, placeTaken: true, args: []string{"renamed-away"}, has: []string{"fail dest-exists 1", "other"}},
		{name: "an existing branch points to adopt as the second step", setup: func(f *flowFixture) { gitIn(f.t, f.root, "branch", "taken") },
			args: []string{"taken"}, has: []string{"fail branch-exists 1", "1 つ目: sh ", "worktree add", "2 つ目: sh ", "taken' --adopt --base <統合先>", "ユーザの確認が要ります"}},
		{name: "a retired argument", args: []string{"topic", "--from-origin"}, has: []string{"fail retired-arg 2", "廃止しました", "--accept"}},
		{name: "a parent tree with an approved ticket may not switch", cwd: parentTree(Leftover{Stage: "doing", MR: "12"}),
			has: []string{"直接作業へは切り替えられない", "親のワークツリー（p0001）から", "NOTE_WORKTREE_PARENT_CLOSE", "reason parent-active"}},
		{name: "a finished parent tree may not switch", cwd: parentTree(Leftover{Stage: "review", MR: "12"}),
			has: []string{"完了済み（レビュー待ち）", "reason parent-active"}},
		{name: "a parent tree with a started child may not switch even if only a proposal", cwd: parentTree(Leftover{Stage: "todo", Doing: 1}),
			has: []string{"着手済みの子が 1 件", "reason parent-active"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			f := newFlow(t)
			if c.setup != nil {
				c.setup(f)
			}
			cwd, args := "", c.args
			if args == nil {
				args = []string{"topic"}
			}
			if c.cwd != nil {
				cwd = c.cwd(f)
				f.leftover = leftoverOf[cwd]
			}
			out := f.run(cwd, nil, args...)
			for _, h := range c.has {
				// 文面ファイルの文は、文面の検査が見る。ここは文が出ていること（引けなかった文でないこと）だけ見る
				if strings.HasPrefix(h, "NOTE_") {
					hasNot(t, out, h)
					continue
				}
				has(t, out, h)
			}
			if c.after != nil {
				c.after(t, f)
			}
			if !c.placeTaken && !f.cutOff(args[0]) && c.name != "an existing branch points to adopt as the second step" {
				t.Errorf("止まるはずが %s が切れている", args[0])
			}
		})
	}

	t.Run("a parent tree with only a todo proposal may switch", func(t *testing.T) {
		f := newFlow(t)
		path := parentTree(Leftover{Leftover: true, Proposal: true, MR: "12"})(f)
		f.leftover = leftoverOf[path]
		out := f.run(path, nil, "topic", "--base", "main")
		has(t, out, "ワークツリーを切った")
		hasNot(t, out, "引けませんでした")
	})
	t.Run("the workspace root is not refused", func(t *testing.T) {
		f := newFlow(t)
		parentTree(Leftover{Stage: "doing"})(f)
		has(t, f.run("", nil, "topic"), "ワークツリーを切った")
	})
	t.Run("a dry run creates nothing and reports the wip", func(t *testing.T) {
		f := newFlow(t)
		f.dirtyRoot()
		out := f.run("", nil, "topic", "--dry-run")
		has(t, out, "dry-run", "書きかけ 変更 2・新規 1", "--copy-wip か --no-wip")
		if !f.cutOff("topic") || gitIn(f.t, f.root, "branch", "--list", "topic") != "" {
			t.Error("--dry-run が切っている")
		}
		if gitIn(f.t, f.root, "for-each-ref", "refs/ccnavi") != "" {
			t.Error("--dry-run が ref を置いている")
		}
	})
	t.Run("an existing worktree with no record points to adopt", func(t *testing.T) {
		f := newFlow(t)
		gitIn(f.t, f.root, "worktree", "add", "-q", f.wt("manual"), "-b", "manual", "main")
		out := f.run("", nil, "manual")
		has(t, out, "ワークツリーは既にある", "取り込みの記録は、無い（ccnavi-direct/manual）", "manual' --adopt --base <取り込み先>")
		hasNot(t, out, "確認に答えられません")
	})
	t.Run("a refused worktree add removes the record and the ref", func(t *testing.T) {
		f := newFlow(t)
		// 行き先の親をファイルにして worktree add を失敗させる。
		put(f.t, filepath.Join(f.root, ".claude", "worktrees"), "file")
		out := f.run("", nil, "topic")
		has(t, out, "fail worktree-add-refused 1")
		if _, err := os.Stat(filepath.Join(f.root, ".git", "ccnavi-direct", "topic")); err == nil {
			t.Error("記録が残っている")
		}
		if exec.Command("git", "-C", f.root, "show-ref", "--verify", "--quiet", "refs/ccnavi/direct/topic/s").Run() == nil {
			t.Error("S の ref が残っている")
		}
	})
}

// leftoverOf は、親のワークツリーのパスごとの、残っているものの答え（shask.Leftover の代わり。その答えは shask のテストが見る）。
var leftoverOf = map[string]func(string, string) Leftover{}

// parentTree は、親のワークツリー（別ブランチ p0001）を切り、そこに残っているものの答えを leftover に決める。
func parentTree(answer Leftover) func(f *flowFixture) string {
	return func(f *flowFixture) string {
		path := f.wt("p0001")
		gitIn(f.t, f.root, "worktree", "add", "-q", "-b", "p0001", path)
		leftoverOf[path] = func(string, string) Leftover { return answer }
		return path
	}
}
