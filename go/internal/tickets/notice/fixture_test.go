package notice

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/pyoracle"
)

// テストの道具。lifecycle/internal/gitfixture と同じ形（lifecycle の internal なのでここからは読めない）。
// コミットの時刻は決めておき、同じ形を 2 つ作ると sha も同じになる。

const commitDate = "2026-01-05T00:00:00+0000"

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL="+os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com",
		"GIT_AUTHOR_DATE="+commitDate, "GIT_COMMITTER_DATE="+commitDate)
	out, err := cmd.Output()
	if err != nil {
		var stderr string
		if e, ok := err.(*exec.ExitError); ok {
			stderr = string(e.Stderr)
		}
		t.Fatalf("git %v: %v\n%s", args, err, stderr)
	}
	return string(out)
}

// workspace は main に空でないコミットを 1 つ持つワークスペースを作り、文面ファイルを写す。
func workspace(t *testing.T) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "init", "-q", "-b", "main")
	put(t, filepath.Join(root, ".gitignore"), "/scratchpad\n/logs\n/remote.git\n/.ccnavi/messages\n/.claude/\n")
	runGit(t, root, "add", ".gitignore")
	runGit(t, root, "commit", "-q", "-m", "init")
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

func remote(t *testing.T, root string) {
	t.Helper()
	bare := filepath.Join(root, "remote.git")
	runGit(t, root, "init", "-q", "--bare", bare)
	runGit(t, root, "remote", "add", "origin", bare)
}

func worktree(root, name string) string { return filepath.Join(root, ".claude", "worktrees", name) }

func addWorktree(t *testing.T, root, name, from string) string {
	t.Helper()
	runGit(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name), from)
	return worktree(root, name)
}

func commitAll(t *testing.T, dir, message string) {
	t.Helper()
	runGit(t, dir, "add", "-A")
	runGit(t, dir, "commit", "-q", "--allow-empty", "-m", message)
}

func headOf(t *testing.T, dir string) string { return strings.TrimSpace(runGit(t, dir, "rev-parse", "HEAD")) }

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

// touch は更新時刻を決める（並びを Python と Go で同じにする）。
func touch(t *testing.T, path string, when time.Time) {
	t.Helper()
	if err := os.Chtimes(path, when, when); err != nil {
		t.Fatal(err)
	}
}

func settingsOf(t *testing.T, root string) *config.Settings {
	t.Helper()
	conf, _ := config.Load(root)
	return conf
}

func rel(root, s string) string { return strings.ReplaceAll(s, root, "<root>") }

// sameAsPython は Go の答えを JSON にして、Python の答えとキーごとに比べる。
func sameAsPython(t *testing.T, got, want map[string]any) {
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

// 承認済みチケットの見本。

func approvedPath(root, parent, state, id string) string {
	return filepath.Join(worktree(root, parent), ".ccnavi", "approved", state, id+".md")
}

const phasesCopy = "phases:\n  work:\n    kind: work\n    title: 作業\n    review: false\n    scope:\n    - src/*\n" +
	"  research:\n    kind: work\n    title: 調査\n    review: false\n    scope:\n    - wip/research/*\n"

// parentText は親の見本。plan は計画の行（"  - work" の形）、allow は範囲の glob、extra は frontmatter に足す行。
func parentText(id string, plan, allow []string, extra ...string) string {
	head := "---\nversion: 1\nticket: " + id + "\nmr: 7\nissue: 1\n"
	if len(plan) > 0 {
		head += phasesCopy + "plan:\n" + strings.Join(plan, "\n") + "\n"
	}
	for _, e := range extra {
		head += e + "\n"
	}
	return head + "human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" + allowLines(allow) +
		"started_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

// childText は子の見本。
func childText(id, parent, phase string, allow []string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: false\n  reason: t\ntitle: 子 " + id + "\nrationale: r\n" + allowLines(allow)
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}

func allowLines(globs []string) string {
	out := "allow:\n"
	for _, g := range globs {
		if strings.HasPrefix(g, "re:") {
			out += "  - match: Write|Edit\n    regex: \"" + strings.TrimPrefix(g, "re:") + "\"\n"
			continue
		}
		out += "  - match: Write|Edit\n    glob: \"" + g + "\"\n"
	}
	return out
}
