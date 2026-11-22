package core

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
)

// 不正なチケットがあっても、PreToolUse は異常終了せずに、無いときと同じ判定を返す。hook が非 0 で終わると Claude Code は判定を
// 読まないので、止めるはずの呼び出しが通る。不正な YAML の読み方そのものは go/internal/infra/yaml の go test が見る。
func TestBrokenTicketGitPushIsStillDenied(t *testing.T) {
	t.Parallel()
	fixture := readFile(t, filepath.Join(base.Root(), "tests", "fixtures", "rules.yml"))
	decision := func(t *testing.T, ticket *string) string {
		root := emptyRoot(t)
		writeFile(t, root, base.CommonRelpath("rules"), fixture)
		if ticket != nil {
			writeFile(t, root, "wip/proposals/review/T-1.md",
				"---\nticket: "+*ticket+"\ntitle: x\nccnavi_approved:\n  approved_at: 2026-01-01\n---\nbody\n")
		}
		out := judgeTool(t, root, "Bash", "git push origin main", "--guard-core-files", "disable")
		return str(out, "permissionDecision")
	}
	if d := decision(t, nil); d != "deny" {
		t.Errorf("clean = %s", d)
	}
	for name, ticket := range map[string]string{"int-tab": "!!int \t", "int-empty": "!!int"} {
		if d := decision(t, &ticket); d != "deny" {
			t.Errorf("%s = %s", name, d)
		}
	}
}
