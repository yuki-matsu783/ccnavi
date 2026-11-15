package branch

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// front はチケットの frontmatter だけの本文。
func front(fields ...string) string {
	return "---\n" + strings.Join(fields, "\n") + "\n---\n本文\n"
}

// 直接作業の取り込み先のブランチ（ccnavi-worktree.sh が切る前に見る）が結び付くか。識別子の衝突はワークスペース全体、
// 結び付きは取り込み先のリポジトリだけを見る。files は置くファイル（ワークスペースからの相対 → 中身）、repo は
// 取り込み先のリポジトリ（projects/ の下の名前。空ならワークスペース）、want は答えと、理由に含まれる語。
func TestBaseBoundScenarios(t *testing.T) {
	const approved, proposals = ".ccnavi/approved", "wip/proposals"
	for _, c := range []struct {
		label        string
		files        map[string]string
		repo, branch string
		want, inWhy  string
	}{
		{"何も無い", nil, "p", "feature/x", "no", ""},
		{"識別子", map[string]string{"projects/p/" + approved + "/doing/feature-x.md": front("ticket: feature-x")}, "p", "feature/x", "yes", "feature-x.md"},
		{"branch 欄", map[string]string{"projects/p/" + approved + "/done/t1.md": front("ticket: t1", "branch: feature/x")}, "p", "feature/x", "yes", "t1.md"},
		{"どこかの提案", map[string]string{"projects/q/" + proposals + "/todo/feature-x.md": front("ticket: x")}, "p", "feature/x", "yes", "feature-x.md"},
		{"取り込み先の退避の親", map[string]string{"logs/archive/p/done/feature-x.md": front("ticket: feature-x")}, "p", "feature/x", "yes", "退避"},
		{"ワークスペースの退避の親", map[string]string{"logs/archive/self/done/feature-x.md": front("ticket: feature-x")}, "", "feature/x", "yes", "退避"},
		{"ほかのリポジトリの退避", map[string]string{"logs/archive/q/done/feature-x.md": front("ticket: feature-x")}, "p", "feature/x", "no", ""},
		{"そのリポジトリの退避", map[string]string{"logs/archive/q/done/feature-x.md": front("ticket: feature-x")}, "q", "feature/x", "yes", ""},
		{"退避の子", map[string]string{"logs/archive/p/done/feature-x.md": front("ticket: feature-x", "parent: x")}, "p", "feature/x", "no", ""},
		{"別のファイル名の承認済み", map[string]string{"projects/p/" + approved + "/doing/t2.md": front("ticket: feature-x")}, "p", "feature/x", "yes", "t2.md"},
		{"ほかのリポジトリの branch 欄", map[string]string{"projects/q/" + approved + "/doing/t1.md": front("ticket: t1", "branch: feature/x")}, "p", "feature/x", "no", ""},
		{"ワークスペース全体の識別子の衝突", map[string]string{"projects/q/" + approved + "/doing/feature-x.md": front("ticket: feature-x")}, "p", "feature/x", "yes", "projects/q"},
		{"デフォルトブランチに残るチケット", map[string]string{"projects/p/" + approved + "/done/old.md": front("ticket: old", "branch: other")}, "p", "main", "no", ""},
		{"読めない置き場", map[string]string{"projects/p/" + approved + "/doing/broken.md": "---\n: [\n---\n"}, "p", "feature/x", "unknown", ""},
	} {
		t.Run(c.label, func(t *testing.T) {
			root := testkit.GitRoot(t)
			testkit.Git(t, root, "init", "-q", "-b", "main")
			testkit.Commit(t, root, "init")
			for _, name := range []string{"p", "q"} {
				repo := filepath.Join(root, "projects", name)
				if err := os.MkdirAll(repo, 0o755); err != nil {
					t.Fatal(err)
				}
				testkit.Git(t, repo, "init", "-q", "-b", "main")
				testkit.Commit(t, repo, "init")
			}
			for rel, text := range c.files {
				testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
			}
			main := root
			if c.repo != "" {
				main = filepath.Join(root, "projects", c.repo)
			}
			got, why := BaseBound(testkit.Settings(t, root), root, main, c.branch)
			if string(got) != c.want || !strings.Contains(filepath.ToSlash(why), c.inWhy) {
				t.Errorf("%s %s: %s %q, want %s (%s)", c.repo, c.branch, got, why, c.want, c.inWhy)
			}
		})
	}
}
