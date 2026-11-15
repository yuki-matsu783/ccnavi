package stop

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/records/prune"
)

// records/prune はStop の促しの記録を名前の頭（nudgePrefix）で見分けて消す。prune は上の層を読めないので名前を写して
// 持っている。写しが持ち主の定数とずれると記録が残り続けるので、古い記録が消えることで確かめる。
func TestPruneRemovesOldNudgePrefix(t *testing.T) {
	for _, name := range []string{env.LogRotateMB, env.LogKeepDays, env.StateKeepDays, env.StopReviewKeepDays} {
		t.Setenv(name, "")
	}
	root := t.TempDir()
	state := filepath.Join(root, "logs", "state")
	if err := os.MkdirAll(state, 0o777); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(state, nudgePrefix+"11111111-aaaa-bbbb-cccc-000000000001.json")
	if err := os.WriteFile(path, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-60 * 24 * time.Hour)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}
	prune.Run(root, filepath.Join(root, "logs", "decisions.jsonl"), state, "", prune.Options{})
	if _, err := os.Lstat(path); err == nil {
		t.Errorf("%s の古い記録が消えない（prune の名前の写しと違う）", nudgePrefix)
	}
}
