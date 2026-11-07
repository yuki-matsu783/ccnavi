package standing

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/testkit"
)

// NFD のブランチ名（使えない名前）を HEAD にしたツリーは、NFC の識別子と一致としない。sh の
// ccnavi_head_branch も同じ答え（tests/sh/test_branch_to_id_sh.py）。
func TestHeadBranchDoesNotMatchAnNFDBranch(t *testing.T) {
	nfc := "feature/が"
	nfd := pyfmt.NFD(nfc)
	root := testkit.GitRoot(t)
	tree := filepath.Join(root, "wt")
	testkit.Git(t, root, "init", "-q", tree)
	testkit.Git(t, tree, "checkout", "-q", "-b", nfd)
	if head := strings.TrimSpace(testkit.Git(t, tree, "symbolic-ref", "--short", "HEAD")); head != nfd {
		// macOS の git（core.precomposeunicode）は NFD の名前を NFC に直して作る
		t.Skip("この環境の git は NFD のブランチ名を作れない")
	}
	if got := HeadBranch(tree, strings.ReplaceAll(nfc, "/", "-")); got != "" {
		t.Errorf("NFD のブランチ名を一致とした: %q", got)
	}
}
