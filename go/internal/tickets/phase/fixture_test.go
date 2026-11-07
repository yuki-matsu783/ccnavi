package phase

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/pyoracle"
	"ccnavi/internal/tickets/model/ticket"
)

// テストの道具。git のワークスペースと親のワークツリー、チケットの見本を作る。コミットの時刻は決めておき、
// 2 つのワークスペース（Python 用と Go 用）で同じ sha と時刻になるようにする。

const commitDate = "2026-01-05T00:00:00+0000"

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL="+os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com",
		"GIT_AUTHOR_DATE="+commitDate, "GIT_COMMITTER_DATE="+commitDate)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return string(out)
}

// workspace はワークスペースと、名前 = ブランチのワークツリーを作る。文面ファイルも写す。
func workspace(t *testing.T, worktrees ...string) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "init", "-q", "-b", "main")
	runGit(t, root, "commit", "-q", "--allow-empty", "-m", "init")
	for _, name := range worktrees {
		addWorktree(t, root, name, "main")
	}
	copyMessages(t, root)
	return root
}

func addWorktree(t *testing.T, root, name, from string) string {
	t.Helper()
	runGit(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name), from)
	return worktree(root, name)
}

func copyMessages(t *testing.T, root string) {
	t.Helper()
	from := filepath.Join(pyoracle.RepoRoot(t), ".ccnavi", "messages")
	entries, err := os.ReadDir(from)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		data, err := os.ReadFile(filepath.Join(from, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
		put(t, filepath.Join(root, ".ccnavi", "messages", e.Name()), string(data))
	}
}

func put(t *testing.T, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func settingsOf(t *testing.T, root string) *config.Settings {
	t.Helper()
	conf, _ := config.Load(root)
	return conf
}

func worktree(root, name string) string { return filepath.Join(root, ".claude", "worktrees", name) }

func approvedDir(root, parent string) string {
	return filepath.Join(worktree(root, parent), ".ccnavi", "approved")
}

const phasesCopy = `phases:
  research:
    kind: work
    title: 調査
    review: false
    scope:
    - wip/research/*
  design:
    kind: work
    title: 設計
    review: true
    when: 設計の案内
    scope:
    - wip/design/*
  implement:
    kind: work
    title: 実装
    review: true
    when: 実装の案内
    scope:
    - src/*
    - wip/design/*
`

// parentText は親の見本。plan は計画の行（"  - research" の形）で、空なら計画も定義のコピーも書かない。
func parentText(id string, plan ...string) string {
	head := "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n"
	if len(plan) > 0 {
		head += phasesCopy + "plan:\n" + strings.Join(plan, "\n") + "\n"
	}
	return head + "human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
		"started_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

// childText は子の見本。required は human_review.required の値（空なら書かない）、extra は frontmatter に足す行。
func childText(id, parent, phase, required string, globs []string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n"
	if required != "" {
		text += "human_review:\n  required: " + required + "\n  reason: t\n"
	}
	text += "title: 子 " + id + "\nrationale: r\nallow:\n"
	for _, g := range globs {
		text += "  - match: Write|Edit\n    glob: \"" + g + "\"\n"
	}
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}

func parsed(t *testing.T, text string) *ticket.Ticket {
	t.Helper()
	found, problems := ticket.Parse(text)
	if found == nil {
		t.Fatalf("読めない: %v", problems)
	}
	return found
}
