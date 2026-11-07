package learning

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// テストの道具。git のリポジトリとファイルを作る（git とファイルは testkit）。

// repo は空でないコミットを 1 つ持つリポジトリ。
func repo(t *testing.T) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	put(t, root, "README", "x\n")
	commit(t, root)
	return root
}

func commit(t *testing.T, root string) {
	t.Helper()
	testkit.Git(t, root, "add", "-f", "-A")
	testkit.Git(t, root, "commit", "-q", "--allow-empty", "-m", "c")
}

// put は root からの相対（区切りは "/"）にファイルを書く。
func put(t *testing.T, root, rel, text string) string {
	t.Helper()
	return testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
}

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func headings(text string) int {
	n := 0
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, headingMk) {
			n++
		}
	}
	return n
}
