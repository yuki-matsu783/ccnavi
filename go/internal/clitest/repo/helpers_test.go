package repo

import (
	"cmp"
	"os/exec"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// gitIn は dir で git を 1 回起こし、標準出力を返す。環境は run.Environment（走った機械の git の設定を締め出した環境）。
// extra は既定の環境への差分。失敗したらテストを止める。
func gitIn(t *testing.T, dir string, extra map[string]string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = run.Environment(run.Opts{Env: extra})
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("git %s: %v", strings.Join(args, " "), err)
	}
	return strings.TrimSpace(string(out))
}

// sortedKeys は map の鍵を並べて返す（表示と比べを決まった順にする）。
func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.SortFunc(keys, cmp.Compare[string])
	return keys
}
