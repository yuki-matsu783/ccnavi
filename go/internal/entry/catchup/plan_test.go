package catchup

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
)

// sync plan（取り込む親子のチケットとリポジトリを選ぶ）と、retire の記録（ワークツリーを片付けた後の親）の選び方を、
// 使い捨てのワークスペースで確かめる。sh は起こさない。sh を通す確認は tests/sh/test_sync_sh.py に数本だけ残す。

func gitIn(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
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

type planFixture struct {
	t    *testing.T
	root string
	tree string // 親のワークツリー i0001
}

// newPlan は、origin のあるワークスペースと、承認済みの親チケット（mr: 42）を持つ親のワークツリー i0001 を組む。
func newPlan(t *testing.T) *planFixture {
	t.Helper()
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	f := &planFixture{t: t, root: filepath.Join(base, "ws")}
	if err := os.MkdirAll(f.root, 0o755); err != nil {
		t.Fatal(err)
	}
	gitIn(t, f.root, "init", "-q", "-b", "main")
	gitIn(t, f.root, "config", "user.email", "t@example.invalid")
	gitIn(t, f.root, "config", "user.name", "t")
	put(t, filepath.Join(f.root, ".gitignore"), ".claude/worktrees/\nlogs/\n")
	put(t, filepath.Join(f.root, "README.md"), "seed\n")
	gitIn(t, f.root, "add", "--", ".gitignore", "README.md")
	gitIn(t, f.root, "commit", "-q", "-m", "seed")
	gitIn(t, f.root, "remote", "add", "origin", filepath.Join(base, "origin.git"))
	f.tree = f.parent("i0001")
	return f
}

func (f *planFixture) parent(name string) string {
	path := filepath.Join(f.root, ".claude", "worktrees", name)
	gitIn(f.t, f.root, "worktree", "add", "-q", path, "-b", name, "main")
	put(f.t, filepath.Join(path, ".ccnavi", "approved", "doing", name+".md"),
		"---\nversion: 1\nticket: "+name+"\nmr: 42\nissue: 1\n---\n")
	return path
}

// plan は `sync plan` の答えの全文（終了コードは 0）。
func (f *planFixture) plan(wants ...string) string {
	f.t.Helper()
	conf, _ := config.Load(f.root)
	var out, errw bytes.Buffer
	if code := Sync(&out, &errw, f.root, conf, f.root, append([]string{"plan", "/s"}, wants...)); code != 0 {
		f.t.Fatalf("終了コード %d: %s%s", code, out.String(), errw.String())
	}
	return out.String()
}

func (f *planFixture) retire(name, mr, branch string) {
	body := `{"parent": "` + name + `", "project": "", "branch": "` + branch + `", "children": []`
	if mr != "" {
		body += `, "mr": ` + mr
	}
	put(f.t, filepath.Join(f.root, "logs", "state", "retire", name+".json"), body+"}\n")
}

func (f *planFixture) removeTree() {
	gitIn(f.t, f.root, "worktree", "remove", "--force", f.tree)
}

func count(text, line string) int { return strings.Count("\n"+text, "\n"+line) }

func TestSyncPlanPicksTheFamilies(t *testing.T) {
	t.Run("no arguments takes every parent worktree and not the others", func(t *testing.T) {
		f := newPlan(t)
		gitIn(t, f.root, "worktree", "add", "-q", filepath.Join(f.root, ".claude", "worktrees", "free"), "-b", "free")
		out := f.plan()
		if count(out, "family i0001 self i0001 "+f.tree) != 1 || strings.Contains(out, "free") {
			t.Errorf("答え:\n%s", out)
		}
		if count(out, "repo self yes "+f.root) != 1 {
			t.Errorf("リポジトリの行:\n%s", out)
		}
	})
	t.Run("a repeated argument is taken once", func(t *testing.T) {
		f := newPlan(t)
		if out := f.plan("i0001", "i0001"); count(out, "family i0001 ") != 1 {
			t.Errorf("答え:\n%s", out)
		}
	})
	t.Run("a worktree that is not a parent is refused", func(t *testing.T) {
		f := newPlan(t)
		gitIn(t, f.root, "worktree", "add", "-q", filepath.Join(f.root, ".claude", "worktrees", "free"), "-b", "free")
		out := f.plan("free")
		if !strings.Contains(out, "親のワークツリーではない") || count(out, "noticket") != 1 || strings.Contains(out, "\nfamily ") {
			t.Errorf("答え:\n%s", out)
		}
	})
	t.Run("a missing worktree says how to cut it", func(t *testing.T) {
		f := newPlan(t)
		out := f.plan("i0002")
		if !strings.Contains(out, "切り直して") || !strings.Contains(out, "worktree add .claude/worktrees/i0002 <ブランチ>") || count(out, "failed") != 1 {
			t.Errorf("答え:\n%s", out)
		}
	})
	t.Run("bad names are refused with exit 2", func(t *testing.T) {
		for _, bad := range []string{"../x", "a/b", "-x", "a..b"} {
			f := newPlan(t)
			out := f.plan(bad)
			if count(out, "exit 2") != 1 || !strings.Contains(out, "親の識別子の形ではありません") || strings.Contains(out, "\nfamily ") {
				t.Errorf("%s:\n%s", bad, out)
			}
		}
	})
}

// ready の後の片付けの記録（retire/<P>.json）は、親のワークツリーが無いときだけ片付けの対象に数える。
func TestSyncPlanListsRetiredRecords(t *testing.T) {
	t.Run("a record is listed once the worktree is gone", func(t *testing.T) {
		f := newPlan(t)
		f.retire("i0001", "42", "i0001")
		f.removeTree()
		for _, wants := range [][]string{nil, {"i0001"}} {
			out := f.plan(wants...)
			if count(out, "retired i0001 self 42 i0001") != 1 || strings.Contains(out, "\nfamily ") {
				t.Errorf("%v:\n%s", wants, out)
			}
		}
	})
	t.Run("a record without an mr number is only announced", func(t *testing.T) {
		f := newPlan(t)
		f.retire("i0001", "", "i0001")
		f.removeTree()
		for _, wants := range [][]string{nil, {"i0001"}} {
			out := f.plan(wants...)
			if !strings.Contains(out, "片付けの記録（retire/i0001.json）が利用できない") || !strings.Contains(out, "mr") || strings.Contains(out, "\nretired ") {
				t.Errorf("%v:\n%s", wants, out)
			}
		}
	})
	t.Run("a record of another parent is not taken", func(t *testing.T) {
		f := newPlan(t)
		f.retire("other", "42", "other")
		dir := filepath.Join(f.root, "logs", "state", "retire")
		if err := os.Rename(filepath.Join(dir, "other.json"), filepath.Join(dir, "i0001.json")); err != nil {
			t.Fatal(err)
		}
		f.removeTree()
		if out := f.plan(); strings.Contains(out, "\nretired ") {
			t.Errorf("答え:\n%s", out)
		}
	})
	t.Run("a record is not listed while the worktree exists", func(t *testing.T) {
		f := newPlan(t)
		f.retire("i0001", "42", "i0001")
		out := f.plan()
		if strings.Contains(out, "\nretired ") || count(out, "family i0001 ") != 1 {
			t.Errorf("答え:\n%s", out)
		}
	})
}
