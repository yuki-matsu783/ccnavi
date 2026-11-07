package config

import (
	"os"
	"path/filepath"
	"testing"
)

// 戻す手順で案内する git は、ラッパーがあればその打ち方、無ければ `git`（tests/guard/test_post.py から移した）。
func TestGitCommandFollowsTheWrapper(t *testing.T) {
	root := t.TempDir()
	if got := GitCommand(root); got != "git" {
		t.Errorf("ラッパーが無い: %q", got)
	}
	path := filepath.Join(root, DefaultProjectHome, ScriptsDir, GitWrapper)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	if got, want := GitCommand(root), ScriptCommand(root, GitWrapper); got != want {
		t.Errorf("ラッパーがある: %q, want %q", got, want)
	}
}
