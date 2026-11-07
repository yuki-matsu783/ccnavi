package closing

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/pyoracle"
)

// テストの道具。コミットの時刻を固定した git のワークスペースを作る（同じ形を 2 つ作ると sha も同じになる）。

var gitEnv = []string{"GIT_CONFIG_GLOBAL=" + os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
	"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com",
	"GIT_AUTHOR_DATE=2026-01-01T00:00:00+0000", "GIT_COMMITTER_DATE=2026-01-01T00:00:00+0000"}

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), gitEnv...)
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
	return root
}

func addWorktree(t *testing.T, root, name, from string) string {
	t.Helper()
	runGit(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name), from)
	return worktree(root, name)
}

func commitAll(t *testing.T, dir, message string) {
	t.Helper()
	runGit(t, dir, "add", "-A", "-f")
	runGit(t, dir, "commit", "-q", "-m", message)
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

func approvedDir(treeRoot string) string { return filepath.Join(treeRoot, ".ccnavi", "approved") }

const phasesCopy = `phases:
  research:
    kind: work
    title: 調査
    review: false
    scope:
    - wip/research/*
    deliverables:
    - wip/research/summary.md
  design:
    kind: work
    title: 設計
    review: true
    scope:
    - wip/design/*
    - docs/*
    deliverables:
    - docs/design.md
`

// parentText は親の見本。extra は frontmatter に足す行、plan は計画の行（"  - research" の形）。
func parentText(id string, extra []string, plan ...string) string {
	lines := ""
	for _, e := range extra {
		lines += e + "\n"
	}
	return "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n" + phasesCopy +
		"plan:\n" + strings.Join(plan, "\n") + "\n" + lines +
		"human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n---\n\n本文\n"
}

// childText は子の見本。extra は frontmatter に足す行。
func childText(id, parent, phase string, extra ...string) string {
	lines := ""
	for _, e := range extra {
		lines += e + "\n"
	}
	return "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: true\n  reason: t\ntitle: 子 " + id + "\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"wip/*\"\n" + lines + "---\n\n本文\n"
}

const (
	started = `started_at: "2026-01-01T00:00:00+0900"`
	done    = `completed_at: "2026-01-02T00:00:00+0900"`
)

// sameAsPython は Go の答えを JSON にして、Python の答えとキーごとに比べる。
func sameAsPython(t *testing.T, got map[string]any, want map[string]any) {
	t.Helper()
	data, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	var norm map[string]any
	if err := json.Unmarshal(data, &norm); err != nil {
		t.Fatal(err)
	}
	for key, w := range want {
		if g := norm[key]; !reflect.DeepEqual(g, w) {
			a, _ := json.MarshalIndent(g, "", " ")
			b, _ := json.MarshalIndent(w, "", " ")
			t.Errorf("%s が Python と違う\nGo:\n%s\nPython:\n%s", key, a, b)
		}
	}
	for key := range norm {
		if _, ok := want[key]; !ok {
			t.Errorf("%s が Python の答えに無い", key)
		}
	}
}
