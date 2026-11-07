package useronly

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// 配る `ccnavi-user-*.sh` はどれも、hook の名前の止め（userScript）に当たる。名前に式の外の字を
// 入れると、その sh だけ止めが外れる。
func TestEveryUserScriptIsCaughtByTheName(t *testing.T) {
	entries, err := os.ReadDir(filepath.Join(testkit.RepoRoot(t), ".ccnavi", "scripts"))
	if err != nil {
		t.Fatal(err)
	}
	found := 0
	for _, e := range entries {
		name := e.Name()
		if !strings.HasPrefix(name, UserScriptPrefix) || !strings.HasSuffix(name, ".sh") {
			continue
		}
		found++
		if !userScript.search("sh .ccnavi/scripts/" + name + " x") {
			t.Errorf("%s が名前の止めに当たらない", name)
		}
	}
	if found == 0 {
		t.Fatal("ccnavi-user-*.sh が無い")
	}
}
