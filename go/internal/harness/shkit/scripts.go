package shkit

import (
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// CopyScripts は ws/.ccnavi/scripts/ へ、names と共通部（base.CommonSh）を、できあがりの姿（base.ScriptsDir）から写し、その置き場を返す。
func CopyScripts(t testing.TB, ws string, names ...string) string {
	t.Helper()
	return CopyScriptsTo(t, filepath.Join(ws, ".ccnavi", "scripts"), names...)
}

// CopyScriptsTo は dir へ CopyScripts と同じものを写し、dir を返す。
func CopyScriptsTo(t testing.TB, dir string, names ...string) string {
	t.Helper()
	src := base.ScriptsDir()
	Mkdir(t, dir)
	for _, name := range append(append([]string{}, names...), base.CommonSh(t, src)...) {
		data, err := os.ReadFile(filepath.Join(src, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, name), data, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// ExeWrapper は、ccnavi の実行ファイルを起こすだけの sh を dir/ccnavi に置いて、そのパスを返す（CCNAVI_ENTRY_PATH に渡す）。
func ExeWrapper(t testing.TB, dir string) string {
	t.Helper()
	return WriteExec(t, filepath.Join(dir, "ccnavi"), "#!/bin/sh\nexec '"+exe.ShPath(t)+"' \"$@\"\n")
}

// Identity は、コミットできるように作者を決める（走った機械の git の設定は締め出してあるので、リポジトリごとに要る）。
func Identity(t testing.TB, repo string) {
	t.Helper()
	for _, kv := range [][2]string{{"user.email", "t@example.invalid"}, {"user.name", "t"}, {"commit.gpgsign", "false"}} {
		run.Git(t, repo, "config", kv[0], kv[1])
	}
}
