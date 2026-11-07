package approval

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/pyoracle"
)

// テストの道具。git のワークスペースと親のワークツリー、チケットの見本を作る。

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL="+os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
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
		runGit(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name))
	}
	copyMessages(t, root)
	return root
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
    scope:
    - wip/design/*
`

// parentText は親の見本。plan は計画の行（"  - research" の形）。
func parentText(id string, plan ...string) string {
	return "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n" + phasesCopy +
		"plan:\n" + strings.Join(plan, "\n") + "\n" +
		"human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
		"started_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

// childText は子の見本。extra は frontmatter に足す行。
func childText(id, parent string, phase string, glob string, extra ...string) string {
	lines := ""
	for _, e := range extra {
		lines += e + "\n"
	}
	return "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: true\n  reason: t\ntitle: 子 " + id + "\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"" + glob + "\"\n" + lines + "---\n\n本文\n"
}
