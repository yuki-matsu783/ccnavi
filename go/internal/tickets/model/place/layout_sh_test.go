package place

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/testkit"
)

// 実行ファイルを起こさずに動く sh が持つ置き場の名前（ccnavi-common.sh の ccnavi_worktrees_dir など）と同じ表
// （tests/fixtures/layout.json）で、実行ファイルの既定を確かめる。
func TestLayoutTable(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "layout.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct{ Worktrees, Projects, Approved, Proposals string }
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct{ name, got, want string }{
		{"worktrees", filepath.ToSlash(tree.WorktreesDir), table.Worktrees},
		{"projects", config.DefaultProjects, table.Projects},
		{"approved", config.DefaultApproved, table.Approved},
		{"proposals", config.DefaultTickets, table.Proposals},
	} {
		if c.want == "" || c.got != c.want {
			t.Errorf("%s: 実行ファイル %q、表 %q", c.name, c.got, c.want)
		}
	}
}
