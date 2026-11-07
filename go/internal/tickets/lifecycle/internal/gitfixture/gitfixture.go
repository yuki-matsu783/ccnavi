// Package gitfixture は lifecycle のワークツリーとブランチの側のテストの道具（テストからだけ読む）。本物の git の
// ワークスペース・ワークツリー・bare のリモートを作り、チケットの見本を置き、片付けの後のワークスペースの形
// （ファイル・ワークツリー・ブランチ）を写し取る。コミットの時刻は決めておき、2 つのワークスペース（Python 用と
// Go 用）で同じ sha になるようにする。
package gitfixture

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/pyoracle"
)

const commitDate = "2026-01-05T00:00:00+0000"

// Git は git を走らせて標準出力を返す。失敗すればテストを落とす。
func Git(t *testing.T, dir string, args ...string) string {
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

// Workspace はワークスペース（main に空のコミットと、scratchpad・logs・remote.git を無視する .gitignore）を作り、
// 文面ファイルを写す。
func Workspace(t *testing.T) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	Git(t, root, "init", "-q", "-b", "main")
	Put(t, filepath.Join(root, ".gitignore"), "/scratchpad\n/logs\n/remote.git\n/.ccnavi/messages\n")
	Git(t, root, "add", ".gitignore")
	Git(t, root, "commit", "-q", "-m", "init")
	copyMessages(t, root)
	return root
}

// Remote は bare のリモート（root/remote.git）を origin に足す。
func Remote(t *testing.T, root string) {
	t.Helper()
	remote := filepath.Join(root, "remote.git")
	Git(t, root, "init", "-q", "--bare", remote)
	Git(t, root, "remote", "add", "origin", remote)
}

// Worktree はワークツリーの場所。
func Worktree(root, name string) string { return filepath.Join(root, ".claude", "worktrees", name) }

// AddWorktree は from から切った名前 = ブランチのワークツリーを作る。
func AddWorktree(t *testing.T, root, name, from string) string {
	t.Helper()
	Git(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name), from)
	return Worktree(root, name)
}

// Commit はそのツリーの変更を全部コミットする。
func Commit(t *testing.T, dir, message string) {
	t.Helper()
	Git(t, dir, "add", "-A")
	Git(t, dir, "commit", "-q", "--allow-empty", "-m", message)
}

// Head はそのツリーの HEAD の sha。
func Head(t *testing.T, dir string) string {
	return strings.TrimSpace(Git(t, dir, "rev-parse", "HEAD"))
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
		Put(t, filepath.Join(root, ".ccnavi", "messages", e.Name()), string(data))
	}
}

// Put はファイルを書く（親のディレクトリも作る）。
func Put(t *testing.T, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// Settings はそのワークスペースの設定。
func Settings(t *testing.T, root string) *config.Settings {
	t.Helper()
	conf, _ := config.Load(root)
	return conf
}

// Approved は親のワークツリーの承認済みチケットの置き場の、状態の下のファイル。
func Approved(root, parent, state, id string) string {
	return filepath.Join(Worktree(root, parent), ".ccnavi", "approved", state, id+".md")
}

// ParentText は親の見本。plan は計画の行（"  - work" の形）で、空なら計画も定義のコピーも書かない。extra は frontmatter
// に足す行。
func ParentText(id string, plan []string, extra ...string) string {
	head := "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n"
	if len(plan) > 0 {
		head += "phases:\n  work:\n    kind: work\n    title: 作業\n    review: false\n    scope:\n    - src/*\n" +
			"plan:\n" + strings.Join(plan, "\n") + "\n"
	}
	for _, e := range extra {
		head += e + "\n"
	}
	return head + "human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n" +
		"started_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

// ChildText は子の見本。extra は frontmatter に足す行。
func ChildText(id, parent, phase string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: false\n  reason: t\ntitle: 子 " + id + "\nrationale: r\nallow:\n" +
		"  - match: Write|Edit\n    glob: \"src/*\"\n"
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}

// Shape はワークスペースの形。ファイル（.git の中・remote.git・logs と文面ファイルを除く。ディレクトリは末尾の "/"）、
// ワークツリーの一覧（git worktree list --porcelain）、手元のブランチ。パスの root は <root> にする。
func Shape(t *testing.T, root string) map[string]any {
	t.Helper()
	var files []string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		rel = filepath.ToSlash(rel)
		if rel == "." {
			return nil
		}
		if d.Name() == ".git" || slices.Contains([]string{"remote.git", "logs", ".ccnavi/messages"}, rel) {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if d.IsDir() {
			rel += "/"
		}
		files = append(files, rel)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	slices.Sort(files)
	return map[string]any{
		"files":     files,
		"worktrees": Rel(root, Git(t, root, "worktree", "list", "--porcelain")),
		"branches":  strings.Fields(Git(t, root, "branch", "--format=%(refname:short)")),
	}
}

// Rel は文の中の root を <root> にする。
func Rel(root, s string) string { return strings.ReplaceAll(s, root, "<root>") }

// ShapePy は Shape と同じ形を Python で組む関数 shape(root)（Python の答えに入れる）。
const ShapePy = `
def shape(root):
    import os, subprocess
    skip = ("remote.git", "logs", ".ccnavi/messages")
    files = []
    for base, dirs, names in os.walk(root):
        at = os.path.relpath(base, root).replace(os.sep, "/")
        kept = []
        for d in dirs:
            r = d if at == "." else at + "/" + d
            if d == ".git" or r in skip:
                continue
            kept.append(d)
            files.append(r + "/")
        dirs[:] = kept
        for n in names:
            r = n if at == "." else at + "/" + n
            if n == ".git" or r in skip:
                continue
            files.append(r)
    files.sort()
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout
    return {"files": files, "worktrees": git("worktree", "list", "--porcelain").replace(root, "<root>"),
            "branches": git("branch", "--format=%(refname:short)").split()}
`
