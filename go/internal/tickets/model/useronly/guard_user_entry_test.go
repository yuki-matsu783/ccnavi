package useronly

import (
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// `user <操作>` のどれにも、ユーザが打つ入口の sh が配ってある（tests/guard/test_user_entry.py から移した）。
// 印の無い入口を断る文がその sh を名指しするので、無い sh を指すと打つ道が無くなる。
func TestEveryVerbHasAScriptThatExists(t *testing.T) {
	scripts := filepath.Join(testkit.RepoRoot(t), ".ccnavi", "scripts")
	for _, v := range verbs {
		if v.Script == "" {
			t.Errorf("%v: sh が無い", v.words)
			continue
		}
		path := filepath.Join(scripts, UserScriptPrefix+v.Script+".sh")
		if _, err := os.Stat(path); err != nil {
			t.Errorf("%v: %v", v.words, err)
		}
	}
}
