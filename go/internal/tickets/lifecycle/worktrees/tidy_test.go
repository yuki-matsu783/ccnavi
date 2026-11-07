package worktrees

import (
	"slices"
	"testing"

	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// フェーズ 0（統合の子 <親>-00-NN）を指定したときは、フェーズ 0 の子だけが片付けの候補になる。0 を指定なしと
// 読み違えると、閉じた子すべてが候補になる。
func TestTidyTargetsPhaseZero(t *testing.T) {
	root := testkit.ShapedWorkspace(t)
	parent := testkit.AddWorktree(t, root, "i0001", "main")
	put := func(state, id, text string) { testkit.Put(t, testkit.Approved(root, "i0001", state, id), text) }
	put("doing", "i0001", testkit.ParentText("i0001", nil))
	put("done", "i0001-00-01", testkit.ChildText("i0001-00-01", "i0001", "0", completed, merged))
	put("done", "i0001-01-01", testkit.ChildText("i0001-01-01", "i0001", "1", completed, merged))
	testkit.Commit(t, parent, "approve")
	conf := testkit.Settings(t, root)
	ids := func(phaseNo *int) []string {
		var out []string
		for _, tk := range TidyTargets(conf, root, "i0001", phaseNo) {
			out = append(out, tk.ID)
		}
		return out
	}
	zero := ticket.ConsolidatePhase
	if got := ids(&zero); !slices.Equal(got, []string{"i0001-00-01"}) {
		t.Errorf("フェーズ 0: %v", got)
	}
	if got := ids(nil); !slices.Equal(got, []string{"i0001-00-01", "i0001-01-01"}) {
		t.Errorf("指定なし: %v", got)
	}
}
