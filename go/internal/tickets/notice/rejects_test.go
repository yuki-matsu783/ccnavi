package notice

import (
	"path/filepath"
	"testing"
	"time"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/testkit"
)

const rejectsRules = `version: 1
deny:
  - id: no-secret
    match: Write|Edit
    glob: "*/src/secret/*"
    message: 秘密の置き場は触れません。
`

// rejectsWorkspace の形。
//
//	i0001        親。src/secret/* がルールの deny に当たる、regex の項は確かめられない
//	i0001-01-01  フェーズ work（範囲 src/*）。docs/* は親の範囲の外、wip/research/* は定義の範囲の外、
//	             .claude/hooks/* と .ccnavi/approved/* は組み込みの保護に当たる
//	i0001-02-01  フェーズ research。ワークツリーが無い（在るはずの場所で判定する）
//	i0002        却下が 4 件ある親（文に出すのは 3 件まで）
//	i0003        却下が無い親
func rejectsWorkspace(t *testing.T) string {
	root := workspace(t)
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), rejectsRules)
	plan := []string{"  - work", "  - {type: research, after: [1]}"}
	testkit.AddWorktree(t, root, "i0001", "main")
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001"),
		parentText("i0001", plan, []string{"src/*", "wip/*", "src/secret/*", "re:^src/[a-z]+[.]py"}))
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001-01-01"), childText("i0001-01-01", "i0001", "1",
		[]string{"src/*", "docs/*", "wip/research/*", ".claude/hooks/*", ".ccnavi/approved/*"}))
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001-02-01"), childText("i0001-02-01", "i0001", "2",
		[]string{"wip/research/*", "src/secret/a.md"}))
	testkit.AddWorktree(t, root, "i0001-01-01", "i0001")
	testkit.AddWorktree(t, root, "i0002", "main")
	testkit.Put(t, approvedPath(root, "i0002", "doing", "i0002"), parentText("i0002", nil,
		[]string{"src/secret/*", "src/secret/a/*", ".claude/hooks/*", ".ccnavi/config/common/*", "src/*"}))
	testkit.AddWorktree(t, root, "i0003", "main")
	testkit.Put(t, approvedPath(root, "i0003", "doing", "i0003"), parentText("i0003", nil, []string{"src/*"}))
	return root
}

var rejectsCwds = []string{"", ".claude/worktrees/i0001", ".claude/worktrees/i0001-01-01", ".claude/worktrees/i0002",
	".claude/worktrees/i0003"}

var guardedRels = []string{".claude/hooks/guard.sh", ".ccnavi/config/common/rules.yml", ".ccnavi/approved/doing/x.md",
	"src/app.py", "src/secret/k"}

func TestRejectsMatchesGolden(t *testing.T) {
	root := rejectsWorkspace(t)
	want := golden.Load[map[string]any](t, "rejects")
	conf := testkit.Settings(t, root)
	found, err := FindRejects(conf, root, RejectOptions{Budget: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	var all []any
	for _, r := range found.Rejects {
		all = append(all, []any{r.ticket, r.decision, r.request, testkit.Rel(root, r.reason), testkit.Rel(root, r.origin)})
	}
	var explain []string
	for _, line := range ExplainLines(found) {
		explain = append(explain, testkit.Rel(root, line))
	}
	only, _ := FindRejects(conf, root, RejectOptions{only: "I0001-01-01", Budget: time.Minute})
	var requests []any
	for _, r := range only.Rejects {
		requests = append(requests, r.request)
	}
	missing, _ := FindRejects(conf, root, RejectOptions{only: "nothing"})
	var notices []string
	for _, cwd := range rejectsCwds {
		at := root
		if cwd != "" {
			at = filepath.Join(root, filepath.FromSlash(cwd))
		}
		notices = append(notices, testkit.Rel(root, Rejects(conf, root, at, time.Now().Add(time.Minute))))
	}
	cut, _ := FindRejects(conf, root, RejectOptions{Budget: time.Nanosecond})
	target := tree.Tree{Name: "w", Root: testkit.Worktree(root, "i0001-01-01"), Kind: tree.KindWorktree}
	rs, err := ruleSetFor(conf, root, target)
	if err != nil {
		t.Fatal(err)
	}
	var denied []any
	for _, r := range guardedRels {
		if rule := deniedBy(rs, filepath.Join(target.Root, filepath.FromSlash(r))); rule != nil {
			denied = append(denied, rule.ID)
		} else {
			denied = append(denied, nil)
		}
	}
	testkit.SameJSON(t, map[string]any{
		"all": all, "unchecked": found.unchecked, "explain": explain, "only": requests, "missing": len(missing.Rejects),
		"notice": notices, "cut": ExplainLines(cut), "denied": denied,
	}, want)
}
