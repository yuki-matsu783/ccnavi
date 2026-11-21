package core

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 識別子と違う名前のワークツリーで書いた提案など、運用のミスを `--lint` が名指しする（どれも同じミスを検知して、正しい方法を案内する）。
//
//  1. 親の提案が識別子と違う名前のワークツリーに在る、子の提案が親と同名でない所に在る（warn）
//  2. 計画の無い親（warn。`ticket start` も止まる）
//  3. `.claude/worktrees/` の下で git に登録が無いディレクトリ（warn）
//  4. 直接作業のワークツリーの名前が `<先頭の語>-<番号>-<slug>` の形でない（warn。既存の名前は変えなくてよい）
//
// 子の親が承認済みの一覧に無く別のツリーに在る形（error のまま、文面に置き場所を添える）は Go の candidates のテストが見る。
func lintLinesOf(tr *ticketkit.Tree, needles ...string) []string {
	var out []string
	for _, line := range ticketkit.Lines(tr.Ccnavi("--lint", "--mode", "enable").Stdout) {
		all := true
		for _, n := range needles {
			all = all && strings.Contains(line, n)
		}
		if all {
			out = append(out, line)
		}
	}
	return out
}

// proposeIn はワークツリー treeName に提案を書いてコミットする。
func proposeIn(tr *ticketkit.Tree, treeName, name, text string) {
	tree := tr.ChildTree(treeName)
	tr.Propose(name, text, tree)
	tr.Git(tree, "add", "-A")
	tr.Git(tree, "commit", "--quiet", "-m", "approval-request "+name)
}

func designParent(tr *ticketkit.Tree, name string) string {
	return samples.ParentText(name, samples.Plan("design"), samples.ParentOpts{Copy: tr.Phases})
}

func TestLintNamesOperationalMistakes(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		name   string
		set    func(t *testing.T, tr *ticketkit.Tree)
		filter []string
		// want は 1 行の中に全部が含まれるべき言葉。空なら「該当する行が無い」ことを見る。
		want []string
		not  string
	}{
		{"親の提案が別名のワークツリーにある", func(t *testing.T, tr *ticketkit.Tree) {
			tr.Worktree("rename-x", "main")
			proposeIn(tr, "rename-x", "i0002", designParent(tr, "i0002"))
		}, []string{"i0002", "rename-x"},
			[]string{"提案 i0002 を rename-x に書いている", "ccnavi-start.sh --mr <MR の番号>", "このままだと親が取り込み済みにならない"}, "-b i0002"},
		{"親の提案が自分のワークツリーにあれば言わない", func(t *testing.T, tr *ticketkit.Tree) {
			tr.Propose("i0001", designParent(tr, "i0001"), "")
			tr.CommitParent("tickets")
		}, []string{"提案 i0001 を"}, nil, ""},
		{"子の提案が親のワークツリーの外にある", func(t *testing.T, tr *ticketkit.Tree) {
			tr.Worktree("rename-x", "main")
			tr.Propose("i0001", designParent(tr, "i0001"), "")
			tr.CommitParent("tickets")
			proposeIn(tr, "rename-x", "i0001-01-01", samples.ChildText("i0001-01-01", "i0001", 1, []string{"wip/design/*"}))
		}, []string{"i0001-01-01", "子の提案を rename-x に書いている"}, []string{"親 i0001 のワークツリー"}, ""},
		{"計画の無い親", func(t *testing.T, tr *ticketkit.Tree) {
			text := samples.ParentText("i0001", samples.Plan("design"), samples.ParentOpts{NoCopy: true})
			tr.Propose("i0001", strings.Replace(text, "plan:\n  - design\n", "", 1), "")
			tr.CommitParent("tickets")
		}, []string{"i0001", "plan がありません"}, []string{"親 i0001 に plan がありません", "--keyword 提案書式"}, ""},
		{"計画のある親は言わない", func(t *testing.T, tr *ticketkit.Tree) {
			tr.Propose("i0001", designParent(tr, "i0001"), "")
			tr.CommitParent("tickets")
		}, []string{"plan が無い"}, nil, ""},
		{"直接作業のワークツリーの名前が自由", func(t *testing.T, tr *ticketkit.Tree) {
			tr.Propose("i0001", designParent(tr, "i0001"), "")
			tr.CommitParent("tickets")
			tr.Worktree("rename-x", "main")
		}, []string{"直接作業のワークツリー名 rename-x"},
			[]string{"`<先頭の語>-<番号>-<slug>` の形ではありません", "ccnavi-worktree.sh <名前>` で切ってください", "既にある名前は変えなくてよい"}, ""},
		{"形に合う直接作業の名前は言わない", func(t *testing.T, tr *ticketkit.Tree) {
			tr.Propose("i0001", designParent(tr, "i0001"), "")
			tr.CommitParent("tickets")
			tr.Worktree("feature-12-direct", "main")
		}, []string{"直接作業のワークツリー"}, nil, ""},
		{"git に登録が無いディレクトリ", func(t *testing.T, tr *ticketkit.Tree) {
			tr.Propose("i0001", designParent(tr, "i0001"), "")
			tr.CommitParent("tickets")
			if err := os.MkdirAll(tr.ChildTree("leftover"), 0o755); err != nil {
				t.Fatal(err)
			}
		}, []string{"leftover"}, []string{".claude/worktrees/leftover は git に登録がありません", "ccnavi-clean.sh leftover"}, ""},
		{"別のリポジトリに登録されたワークツリーは言わない", func(t *testing.T, tr *ticketkit.Tree) {
			tr.Propose("i0001", designParent(tr, "i0001"), "")
			tr.CommitParent("tickets")
			other := tr.ChildTree("elsewhere")
			if err := os.MkdirAll(other, 0o755); err != nil {
				t.Fatal(err)
			}
			ticketkit.Write(t, filepath.Join(other, ".git"), "gitdir: /somewhere/projects/p/.git/worktrees/elsewhere\n")
		}, []string{"elsewhere は git に登録が無い"}, nil, ""},
	} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			tr := newPhaseTree(t)
			row.set(t, tr)
			lines := lintLinesOf(tr, row.filter...)
			if row.want == nil {
				if len(lines) != 0 {
					t.Errorf("言わないはずの行: %v", lines)
				}
				return
			}
			for _, line := range lines {
				ok := true
				for _, w := range row.want {
					ok = ok && strings.Contains(line, w)
				}
				if ok && (row.not == "" || !strings.Contains(line, row.not)) {
					return
				}
			}
			t.Errorf("%v を全部含む行が無い: %v", row.want, lines)
		})
	}
}

// 親のワークツリーの登録は言わない（`.claude/worktrees/i0001` は git に登録がある）。
func TestLintDoesNotSayTheRegisteredParentWorktreeIsUnregistered(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	tr.Propose("i0001", designParent(tr, "i0001"), "")
	tr.CommitParent("tickets")
	if err := os.MkdirAll(tr.ChildTree("leftover"), 0o755); err != nil {
		t.Fatal(err)
	}
	if lines := lintLinesOf(tr, ".claude/worktrees/i0001 は git"); len(lines) != 0 {
		t.Errorf("lines = %v", lines)
	}
}

// 直接作業の名前の形は、先頭の語の設定（CCNAVI_BRANCH_PREFIXES）に従う。
func TestTheFormFollowsTheBranchPrefixesSetting(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	tr.Propose("i0001", designParent(tr, "i0001"), "")
	tr.CommitParent("tickets")
	tr.Worktree("feature-12-direct", "main")
	lint := tr.Call(ticketkit.Call{Env: map[string]string{"CCNAVI_BRANCH_PREFIXES": "wip"}}, "--lint", "--mode", "enable")
	ticketkit.Has(t, lint.Stdout, "feature-12-direct が")
}
