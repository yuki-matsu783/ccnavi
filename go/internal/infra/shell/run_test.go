package shell

import (
	"testing"

	envnames "ccnavi/internal/infra/env"
)

// git の hook の中から起こされたときに設定されている GIT_DIR などは、sh に渡さない。渡すと sh の中の git が
// dir のツリーでなく hook のリポジトリを見る。呼び手が足した変数はそのまま渡す。
func TestScriptDropsTheRepositoryOfTheEnvironment(t *testing.T) {
	for _, name := range envnames.Repository() {
		t.Setenv(name, "/elsewhere")
	}
	script := `for n in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR; do eval "printf '%s=%s;' $n \"\${$n-unset}\""; done; printf 'X=%s' "$X"`
	ran, err := Script([]string{"-c", script}, t.TempDir(), []string{"X=kept"}, SyntaxTimeout)
	if err != nil {
		t.Skipf("sh を起こせない: %v", err)
	}
	want := "GIT_DIR=unset;GIT_WORK_TREE=unset;GIT_INDEX_FILE=unset;GIT_COMMON_DIR=unset;X=kept"
	if got := string(ran.Stdout); got != want {
		t.Fatalf("got %q, want %q (stderr %q)", got, want, ran.Stderr)
	}
}
