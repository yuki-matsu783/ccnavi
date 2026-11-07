package notice

import (
	"strings"
	"testing"
	"time"

	"ccnavi/internal/tickets/testkit"
)

// 学びの統合の子（maintenance: learning-consolidate）の範囲は固定の定義が上限で、親の範囲を適用しない。
// 親の範囲の外を書く範囲でも、「親の範囲の外」として却下に数えない（ふつうの子は数える）。
func TestRejectsDoNotCutTheConsolidateChildByTheParent(t *testing.T) {
	root := workspace(t)
	testkit.AddWorktree(t, root, "i0001", "main")
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001"), parentText("i0001", []string{"  - work"}, []string{"src/*"}))
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001-00-01"), childText("i0001-00-01", "i0001", "0",
		[]string{"docs/wiki/patterns/*"}, "maintenance: learning-consolidate"))
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001-01-01"), childText("i0001-01-01", "i0001", "1",
		[]string{"src/*", "docs/wiki/patterns/*"}))
	found, err := FindRejects(testkit.Settings(t, root), root, RejectOptions{Budget: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	byParent := map[string]bool{}
	for _, r := range found.Rejects {
		if strings.Contains(r.Origin, originParent) {
			byParent[r.Ticket] = true
		}
	}
	if byParent["i0001-00-01"] {
		t.Errorf("統合の子を親の範囲で切った: %+v", found.Rejects)
	}
	if !byParent["i0001-01-01"] {
		t.Errorf("ふつうの子を親の範囲で切らない: %+v", found.Rejects)
	}
}
