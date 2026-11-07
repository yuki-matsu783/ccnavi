package mirror

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// `ccnavi user mirror` の揃え方のうち、mirror_test.go に無い 2 つ（tests/config/test_mirror_sync.py から移した）。
// 揃えてもコミットは作らない（取り込むのはユーザ）。揃えるものが無ければ、何も書かずにそう言う。
func TestApplyMakesNoCommitAndSaysWhenNothingDiffers(t *testing.T) {
	root := workspace(t)
	lib := project(root, "lib")
	if err := os.RemoveAll(filepath.Join(lib, ".git")); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, lib, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(lib, "README"), "x\n")
	testkit.Commit(t, lib, "init")
	head := testkit.Head(t, lib)

	plan := Prepare(settingsOf(root), root, nil)
	if done := Apply(settingsOf(root), plan, plan.Expect()); !done.OK {
		t.Fatalf("%+v", done)
	}
	if got := testkit.Head(t, lib); got != head {
		t.Errorf("コミットを作った: %s → %s", head, got)
	}
	if read(t, commonPath(lib, "risks.yml")) != commonRisk {
		t.Error("ミラーを書いていない")
	}

	again := Prepare(settingsOf(root), root, nil)
	done := Apply(settingsOf(root), again, again.Expect())
	if done.OK || !strings.Contains(done.Error, "揃えるミラーはありません") {
		t.Errorf("%+v", done)
	}
}
