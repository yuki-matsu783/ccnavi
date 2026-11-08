package diagnose

import (
	"path/filepath"
	"testing"
)

// パスはワークスペースの中なら相対で出す。中かは大文字小文字を畳んで見て、".." で始まる名前は中とする。
func TestShownFoldsCase(t *testing.T) {
	root := filepath.Join(string(filepath.Separator)+"ws", "Root")
	for path, want := range map[string]string{
		filepath.Join(root, "a", "b.yml"):                               "a/b.yml",
		filepath.Join(string(filepath.Separator)+"WS", "root", "c.yml"): "c.yml",
		filepath.Join(root, "..d", "e.yml"):                             "..d/e.yml",
		filepath.Join(root, "..", "f.yml"):                              filepath.Join(root, "..", "f.yml"),
		"":                                                              noneShown,
	} {
		if got := shown(root, path); got != want {
			t.Errorf("%q: %q", path, got)
		}
	}
}
