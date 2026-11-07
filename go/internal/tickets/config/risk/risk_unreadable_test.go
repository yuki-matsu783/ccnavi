package risk

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/testkit"
)

// 変更の種類か先のコミットを読めなければ、0 件と数えずに測れないと答える（閉じる側が重いほうとして扱う）。
// PATH の先頭に、指定の引数を含む呼び出しだけ失敗させる git を置いて再現する。
func TestMeasureRefusesWhenAStepCannotBeRead(t *testing.T) {
	if ospath.Windows {
		t.Skip("sh の git の代役を PATH から起こせない")
	}
	real, err := exec.LookPath("git")
	if err != nil {
		t.Skip("git が無い")
	}
	repo := testkit.GitRoot(t)
	runGit(t, repo, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(repo, "a.txt"), "a\n")
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-q", "-m", "base")
	base := runGit(t, repo, "rev-parse", "HEAD")
	testkit.Put(t, filepath.Join(repo, "a.txt"), "a\nb\n")
	runGit(t, repo, "commit", "-q", "-am", "work")
	for _, c := range []struct{ failing, said string }{
		{"--name-status", "変更の種類を読めない"},
		{"rev-parse", "コミットを読めない"},
	} {
		shim := t.TempDir()
		script := "#!/bin/sh\nfor a in \"$@\"; do [ \"$a\" = '" + c.failing + "' ] && exit 128; done\nexec '" + real + "' \"$@\"\n"
		if err := os.WriteFile(filepath.Join(shim, "git"), []byte(script), 0o755); err != nil {
			t.Fatal(err)
		}
		t.Setenv("PATH", shim+string(os.PathListSeparator)+os.Getenv("PATH"))
		if diff, why := Measure(repo, base, "HEAD"); diff != nil || !strings.Contains(why, c.said) {
			t.Errorf("%s: %v %q", c.failing, diff, why)
		}
	}
}
