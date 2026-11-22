package base

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// ShrinkRepo は、ばらの object を pack にまとめ、見本の hook を消す。リポジトリの中身（履歴・ファイル）は変わらない。
func TestShrinkRepoPacksLooseObjectsAndDropsSampleHooksWithoutChangingHistory(t *testing.T) {
	t.Parallel()
	dir := TempDir(t)
	git := func(args ...string) string {
		cmd := exec.Command("git", append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)...)
		cmd.Dir = dir
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
		return string(out)
	}
	git("init", "--quiet")
	for _, name := range []string{"a", "b", "c"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(name), 0o644); err != nil {
			t.Fatal(err)
		}
		git("add", name)
		git("commit", "--quiet", "-m", name)
	}
	gitDir := filepath.Join(dir, ".git")
	if loose, _ := filepath.Glob(filepath.Join(gitDir, "objects", "??", "*")); len(loose) == 0 {
		t.Fatal("前提: ばらの object があるはず")
	}
	if samples, _ := filepath.Glob(filepath.Join(gitDir, "hooks", "*.sample")); len(samples) == 0 {
		t.Fatal("前提: 見本の hook があるはず")
	}
	before := git("log", "--format=%H")
	ShrinkRepo(t, gitDir)
	if loose, _ := filepath.Glob(filepath.Join(gitDir, "objects", "??", "*")); len(loose) != 0 {
		t.Errorf("ばらの object が残った: %v", loose)
	}
	if packs, _ := filepath.Glob(filepath.Join(gitDir, "objects", "pack", "*.pack")); len(packs) != 1 {
		t.Errorf("pack は 1 つのはず: %v", packs)
	}
	if samples, _ := filepath.Glob(filepath.Join(gitDir, "hooks", "*.sample")); len(samples) != 0 {
		t.Errorf("見本の hook が残った: %v", samples)
	}
	if after := git("log", "--format=%H"); after != before {
		t.Errorf("履歴が変わった:\n%s\n%s", before, after)
	}
	git("fsck", "--strict")
}
