package testkit

import (
	"ccnavi/internal/infra/tree"
	"io/fs"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// lifecycle のテスト（片付け・統合・ブランチ・ops）が使うワークスペースとチケットの見本と、片付けの後の形の写し。

// ShapedWorkspace は Shape で写すワークスペース（main に scratchpad・logs・remote.git・文面ファイルを無視する
// .gitignore のコミット）を作り、文面ファイルを写す。
func ShapedWorkspace(t *testing.T) string {
	t.Helper()
	root := GitRoot(t)
	Git(t, root, "init", "-q", "-b", "main")
	Put(t, filepath.Join(root, ".gitignore"), "/scratchpad\n/logs\n/remote.git\n/.ccnavi/messages\n")
	Git(t, root, "add", ".gitignore")
	Git(t, root, "commit", "-q", "-m", "init")
	CopyMessages(t, root)
	return root
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
		if d.Name() == tree.DotGit || slices.Contains([]string{"remote.git", "logs", ".ccnavi/messages"}, rel) {
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

// shapePy は Shape と同じ形を Python で組む関数 shape(root)（Python の答えに入れる）。
const shapePy = `
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
