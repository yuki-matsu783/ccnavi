package base

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// ShrinkRepo は作り置きのリポジトリを、写す前にファイルの数で小さくする。中身と振る舞いは変えない（tests/__init__.py の shrink_repo の写し）。
//
// 作り置きを木ごと写すと、時間はほぼファイルとディレクトリの数で決まる。ばらの object（1 つずつファイルと 2 字のディレクトリ）を
// 1 つの pack にまとめ、git が init で置く使われない見本の hook（hooks/*.sample）を消す。gitDir は `.git` か bare。
func ShrinkRepo(t testing.TB, gitDir string) {
	t.Helper()
	requireMain()
	cmd := exec.Command("git", "--git-dir", gitDir, "repack", "-a", "-d", "-q")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git repack: %v\n%s", err, out)
	}
	samples, _ := filepath.Glob(filepath.Join(gitDir, "hooks", "*.sample"))
	for _, sample := range samples {
		if err := os.Remove(sample); err != nil {
			t.Fatal(err)
		}
	}
}
