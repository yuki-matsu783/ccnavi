package stop

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// テストの道具。見本と git のワークスペースは testkit（ClosingClock・ClosingParent など）。

const clock = testkit.ClosingClock

// commitAll は無視されたファイルも足してコミットする。
func commitAll(t *testing.T, dir, message string) {
	t.Helper()
	clock.Git(t, dir, "add", "-A", "-f")
	clock.Git(t, dir, "commit", "-q", "-m", message)
}

func approvedDir(treeRoot string) string { return filepath.Join(treeRoot, ".ccnavi", "approved") }
