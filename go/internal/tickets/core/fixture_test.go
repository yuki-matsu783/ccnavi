package core

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/tickets/model/pyoracle"
)

// テストの道具。git のワークスペース・origin・親のワークツリーと、チケットの見本を作る（review/decide の
// flow_test.go・proposal/agree の fixture_test.go と同じ形）。コミットの時刻を固定して、Python 用と Go 用の
// ワークスペースで sha をそろえる。

const commitDate = "2026-01-05T00:00:00+0000"
const fixedClock = "2026-01-06T03:04:05+0900"

var gitEnv = []string{"GIT_CONFIG_GLOBAL=" + os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
	"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com",
	"GIT_AUTHOR_DATE=" + commitDate, "GIT_COMMITTER_DATE=" + commitDate}

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

func put(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

// workspace は main と origin を持つワークスペースを作り、名前ごとに親のワークツリーを切って push する。
// main には追跡する README を置く（親のワークツリーの未コミットの変更を作るため）。
func workspace(t *testing.T, parents ...string) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "init", "-q", "-b", "main")
	put(t, filepath.Join(root, ".gitignore"), "/logs\n/remote.git\n/.ccnavi/messages\n/body.md\n/placed.json\n")
	put(t, filepath.Join(root, "README.md"), "r\n")
	runGit(t, root, "add", ".gitignore", "README.md")
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
	runGit(t, root, "init", "-q", "--bare", filepath.Join(root, "remote.git"))
	runGit(t, root, "remote", "add", "origin", filepath.Join(root, "remote.git"))
	runGit(t, root, "push", "-q", "-u", "origin", "main")
	for _, name := range parents {
		runGit(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name), "main")
		runGit(t, worktree(root, name), "push", "-q", "-u", "origin", name)
	}
	return root
}

func worktree(root, name string) string { return filepath.Join(root, ".claude", "worktrees", name) }

const phasesCopy = "phases:\n  design:\n    kind: work\n    title: 設計\n    review: true\n    when: 設計の案内\n" +
	"    scope:\n    - wip/design/*\n    - src/*\n"

// parentText は親の見本（計画は design の 1 つ）。extra は frontmatter に足す行。
func parentText(id string, extra ...string) string {
	head := "---\nversion: 1\nticket: " + id + "\nissue: 1\nmr: 1\n" + phasesCopy + "plan:\n  - design\n"
	for _, e := range extra {
		head += e + "\n"
	}
	return head + "human_review:\n  required: true\n  reason: t\ntitle: 親 " + id + "\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
		"---\n\n## 目的\n\nx\n\n## 範囲\n\nx\n\n## 対象外\n\nx\n\n## 完了の確かめ方\n\nx\n\n## 計画の理由\n\nx\n"
}

// childText は子の見本。extra は frontmatter に足す行。
func childText(id, parent string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: 1\n" +
		"human_review:\n  required: true\n  reason: t\ntitle: 子 " + id + "\nrationale: r\nallow:\n" +
		"  - match: Write|Edit\n    glob: \"wip/design/*\"\n"
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}

// filesUnder は bases の下のファイルの中身（root を <root> にしたパス → 中身）。
func filesUnder(root string, bases ...string) map[string]string {
	rel := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
	files := map[string]string{}
	for _, base := range bases {
		_ = filepath.WalkDir(base, func(path string, d fs.DirEntry, err error) error {
			if err != nil || d.IsDir() {
				return nil
			}
			data, _ := os.ReadFile(path)
			files[filepath.ToSlash(rel(path))] = rel(string(data))
			return nil
		})
	}
	return files
}

// step は 1 手の答え（名前・終了コード・標準出力・標準エラー）。Python の答えと JSON で比べる。
type step = []any

// answers は 1 本の流れの答え。
type answers struct {
	Out   []step            `json:"out"`
	Files map[string]string `json:"files"`
}

func compare(t *testing.T, got, want answers) {
	t.Helper()
	for i := range max(len(got.Out), len(want.Out)) {
		var g, w any
		if i < len(got.Out) {
			g = got.Out[i]
		}
		if i < len(want.Out) {
			w = want.Out[i]
		}
		if !reflect.DeepEqual(g, w) {
			t.Errorf("%d 番目が違う\n go: %q\n py: %q", i, g, w)
		}
	}
	keys := map[string]bool{}
	for k := range got.Files {
		keys[k] = true
	}
	for k := range want.Files {
		keys[k] = true
	}
	for path := range keys {
		g, inGo := got.Files[path]
		w, inPy := want.Files[path]
		if g != w || inGo != inPy {
			t.Errorf("%s が違う\n go: %q\n py: %q", path, g, w)
		}
	}
}
