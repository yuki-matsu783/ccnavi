package core

import (
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// テストの道具。origin と親のワークツリーを持つワークスペースと、チケットの見本を作る（git とファイルは testkit）。

const fixedClock = "2026-01-06T03:04:05+0900"

// workspace は main と origin を持つワークスペースを作り、名前ごとに親のワークツリーを切って push する。
// main には追跡する README を置く（親のワークツリーの未コミットの変更を作るため）。
func workspace(t *testing.T, parents ...string) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(root, ".gitignore"), "/logs\n/remote.git\n/.ccnavi/messages\n/body.md\n/placed.json\n")
	testkit.Put(t, filepath.Join(root, "README.md"), "r\n")
	testkit.Git(t, root, "add", ".gitignore", "README.md")
	testkit.Git(t, root, "commit", "-q", "-m", "init")
	testkit.CopyMessages(t, root)
	testkit.Git(t, root, "init", "-q", "--bare", filepath.Join(root, "remote.git"))
	testkit.Git(t, root, "remote", "add", "origin", filepath.Join(root, "remote.git"))
	testkit.Git(t, root, "push", "-q", "-u", "origin", "main")
	for _, name := range parents {
		testkit.Git(t, testkit.AddWorktree(t, root, name, "main"), "push", "-q", "-u", "origin", name)
	}
	return root
}

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
