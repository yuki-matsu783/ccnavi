package testkit

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
)

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

// CopyMessages はリポジトリの文面ファイルをワークスペースへ写す。
func CopyMessages(t *testing.T, root string) {
	t.Helper()
	from := filepath.Join(RepoRoot(t), ".ccnavi", "messages")
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

// Settings はそのワークスペースの設定。
func Settings(t *testing.T, root string) *config.Settings {
	t.Helper()
	conf, _ := config.Load(root)
	return conf
}

// ApprovedDir は親のワークツリーの承認済みチケットの置き場。
func ApprovedDir(root, parent string) string {
	return filepath.Join(Worktree(root, parent), ".ccnavi", "approved")
}

// TodoDir は親のワークツリーの承認待ちの提案の置き場。
func TodoDir(root, parent string) string {
	return filepath.Join(Worktree(root, parent), "wip", "proposals", "todo")
}

// Rel は文の中の root を <root> にする。
func Rel(root, s string) string { return strings.ReplaceAll(s, root, "<root>") }
