package life

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 親子のチケットで正とする側（立ち位置）。正とする側は、全部の親子のチケットで同じく親のブランチ（`.claude/worktrees/<P>` で
// HEAD が `<P>` を指すツリー）に固定する。決まらなければ（親のワークツリーが無い・HEAD が合わない・途中の操作がある）、
// 承認も状態の操作も実行前チェックも止める。判定は親のワークツリーの有無・HEAD・途中の操作だけで決まる（git もネットワークも使わない）。

type boardTicket struct {
	Ticket   string        `json:"ticket"`
	Blocked  any           `json:"blocked"`
	Children []boardTicket `json:"children"`
}

type board struct {
	Parents []boardTicket `json:"parents"`
	Tickets []boardTicket `json:"tickets"`
}

// blockedOf は板の ticket の blocked（無ければ空）。板に無ければ止める。
func blockedOf(tr *ticketkit.Tree, name string) string {
	tr.T.Helper()
	var b board
	jsonOf(tr.T, tr.Ccnavi("--explain", "--json").Stdout, &b)
	all := append([]boardTicket{}, b.Tickets...)
	for _, p := range b.Parents {
		all = append(all, p)
		all = append(all, p.Children...)
	}
	for _, x := range all {
		if x.Ticket == name {
			if s, ok := x.Blocked.(string); ok {
				return s
			}
			return ""
		}
	}
	tr.T.Fatalf("%s が板に無い", name)
	return ""
}

func dropParent(tr *ticketkit.Tree) {
	tr.T.Helper()
	tr.Git(tr.Root, "worktree", "remove", "--force", tr.ChildTree(childName))
	tr.Git(tr.Root, "worktree", "remove", "--force", tr.ParentTree)
}

// strayDoing は元ツリー（ワークスペースルート）の .ccnavi/approved/doing/<name>.md。親のワークツリーの外のコピー。
func strayDoing(tr *ticketkit.Tree, name string) string {
	return filepath.Join(tr.Root, ".ccnavi", "approved", "doing", name+".md")
}

func TestTheParentTreeIsTheAuthority(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	if d := ticketkit.Decision(t, writeTo(tr, tr.ChildTree(childName), "wip/research/a")); d == "deny" {
		t.Error("正しい子の書き込みが止まった")
	}
	if b := blockedOf(tr, childName); b != "" {
		t.Errorf("blocked = %q", b)
	}
}

// 元ツリーに未コミットで残ったチケットは、正とする側のツリーが無いときに元ツリーを採る形になるので、信用しない。
func TestACopyOnlyOutsideTheParentTreeIsNotTrusted(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	todo := tr.Propose("i0001-02-02", samples.ChildText("i0001-02-02", "i0001", 2, []string{"wip/design/*"}), "")
	ticketkit.Write(t, strayDoing(tr, "i0001-02-02"), ticketkit.Read(t, todo))
	if err := os.Remove(todo); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, p := range lintProblems(tr) {
		if p.Severity == "error" && strings.Contains(p.Detail, "i0001-02-02") && strings.Contains(p.Detail, "外") {
			found = true
		}
	}
	if !found {
		t.Error("親のワークツリーの外のコピーを --lint が名指ししない")
	}
	check := tr.Ccnavi("sync", "check", "i0001", "self")
	if check.Code != 1 {
		t.Fatalf("code %d\n%s%s", check.Code, check.Stdout, check.Stderr)
	}
	lines := ticketkit.Lines(check.Stdout)
	if lines[0] != "check 1" || !strings.HasPrefix(lines[1], "error ") {
		t.Errorf("sync check の出力:\n%s", check.Stdout)
	}
	// ユーザが親のブランチへ移してコミットする手順も言う。
	ticketkit.Has(t, check.Stdout, "移してコミットと push")
}

func TestAProposalOutsideTheParentTreeIsNotApproved(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	ticketkit.Write(t, filepath.Join(tr.Root, "wip", "proposals", "todo", "i0001-01-02.md"), samples.ChildText("i0001-01-02", "i0001", 1, []string{"wip/research/*"}))
	preview := agreePreview(tr)
	if len(preview.Batch) != 0 {
		t.Errorf("承認の束 = %v", preview.Batch)
	}
	if !preview.rejectedHas("ワークツリーの外") {
		t.Errorf("「ワークツリーの外」の拒否が無い: %+v", preview.Rejected)
	}
}

// 親のワークツリーが決まらない形（HEAD が合わない・detached・途中の操作）で、状態の操作が止まる理由の種別ごとの解き方。
func TestAFinishThatCannotFindTheAuthorityGuidesByKind(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name  string
		setup func(tr *ticketkit.Tree)
		has   []string
		lacks []string
	}{
		{"head_on_another_branch", func(tr *ticketkit.Tree) { tr.Parent("checkout", "--quiet", "-b", "elsewhere") },
			[]string{"HEAD のブランチ名を変換した識別子がフォルダ名（i0001）と合わない", "今の HEAD: elsewhere", "に戻す"},
			[]string{"<ブランチ名>", "切り直す"}},
		{"detached_head", func(tr *ticketkit.Tree) { tr.Parent("checkout", "--quiet", "--detach") },
			[]string{"HEAD がブランチを指していない"}, nil},
		{"rebase_in_progress", func(tr *ticketkit.Tree) { startRebase(tr, tr.ParentTree) },
			[]string{"途中の操作（rebase-merge）"}, []string{"切り直す"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := authority(t)
			c.setup(tr)
			r := ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", childName))
			for _, w := range c.has {
				ticketkit.Has(t, r.Stderr, w)
			}
			for _, w := range c.lacks {
				ticketkit.Lacks(t, r.Stderr, w)
			}
		})
	}
}

func TestTheParentTreeOnAnotherBranchIsUndecided(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	tr.Parent("checkout", "--quiet", "-b", "elsewhere")
	if d := ticketkit.Decision(t, writeTo(tr, tr.ChildTree(childName), "wip/research/a")); d != "deny" {
		t.Errorf("決まらない親の下の書き込み = %q（deny のはず）", d)
	}
	named := false
	for _, p := range lintProblems(tr) {
		if p.Where == "(.claude/worktrees/i0001)" && strings.Contains(p.Detail, "elsewhere") {
			named = true
		}
	}
	if !named {
		t.Error("別のブランチの親のワークツリーを --lint が名指ししない")
	}
}

// 子のチケットが親のワークツリーから消え、元ツリーにだけ残った形。状態の操作は元ツリーのコピーを動かさない。
func TestAStateOperationDoesNotMoveACopyOutsideTheParentTree(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	inside := tr.ApprovedPath("doing", childName)
	text := ticketkit.Read(t, inside)
	if err := os.Remove(inside); err != nil {
		t.Fatal(err)
	}
	stray := ticketkit.Write(t, strayDoing(tr, childName), text)
	r := ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", childName))
	ticketkit.Has(t, r.Stderr, "ワークツリーの外")
	if !ticketkit.Exists(stray) {
		t.Error("元ツリーのコピーが消えた")
	}
	if ticketkit.Exists(filepath.Join(tr.Root, ".ccnavi", "approved", "done")) {
		t.Error("元ツリーに done/ を作った")
	}
}

func TestTheCheckNamesAnUnreadableCopyInTheParentTree(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	ticketkit.OK(t, tr.Ccnavi("sync", "check", "i0001"))
	ticketkit.Write(t, filepath.Join(tr.Approved, "doing", "i0001-01-05.md"), "---\nticket: [\n---\n")
	check := tr.Ccnavi("sync", "check", "i0001")
	if check.Code != 1 {
		t.Fatalf("code %d\n%s%s", check.Code, check.Stdout, check.Stderr)
	}
	ticketkit.Has(t, check.Stdout, "i0001-01-05.md")
}

func TestTheCheckRefusesAPathLikeArgument(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	check := tr.Ccnavi("sync", "check", "../x")
	if check.Code != 1 {
		t.Errorf("code %d", check.Code)
	}
	ticketkit.Has(t, check.Stderr, "有効な識別子の形式ではありません")
}

// 親のワークツリーが無い親子のチケットは決まらず、状態の操作も承認も止める。記録は持たないので、片付けた（worktree remove）後は、
// 元ツリーに残ったコピーがあっても止めが外れない。
func TestAMissingParentTreeStopsTheStateAndTheApproval(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	copies := map[string]string{}
	for _, n := range []string{"i0001", childName} {
		copies[n] = ticketkit.Read(t, tr.ApprovedPath("doing", n))
	}
	parentTree := tr.ParentTree
	dropParent(tr)
	// 元ツリーに同じ親子のチケットを残しても、代用しない。
	for n, text := range copies {
		ticketkit.Write(t, strayDoing(tr, n), text)
	}
	finished := ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", childName))
	for _, w := range []string{"親のワークツリー", "がありません", "切り直す", "片付ければよい"} {
		ticketkit.Has(t, finished.Stderr, w)
	}
	for _, w := range []string{"--forget", "gone", "blocked"} {
		ticketkit.Lacks(t, finished.Stderr, w)
	}
	// レビューの操作も止まる。
	ticketkit.Fails(t, tr.Ccnavi("--cwd", parentTree, "review", "ready", "--result", fixtureJSON(tr)))
}

// 先行チケットの判定。先行の親子のチケットの親のワークツリーが無ければ満たしたとみなさず、どの置き場にも無ければ消してもらう。
func TestAPredecessorOfAFamilyWithoutItsParentTreeIsNotMet(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name, predecessor string
		setup             func(tr *ticketkit.Tree)
		want              []string
	}{
		// 先行 i0002-01-01 は元ツリーの done/ にしかない。親子のチケット i0002 は親のワークツリーが無く（決まらない）、
		// 元ツリーのコピーでは代用しない。
		{"parent_tree_missing", "i0002-01-01", func(tr *ticketkit.Tree) {
			text := strings.Replace(samples.ChildText("i0002-01-01", "i0002", 1, []string{"src/*"}), `completed_at: ""`, `completed_at: "2026-09-01T00:00:00+0900"`, 1)
			ticketkit.Write(t, filepath.Join(tr.Root, ".ccnavi", "approved", "done", "i0002-01-01.md"), text)
		}, []string{"親子のチケットが決まらない"}},
		// 閉じて片付いた別の親子のチケットの子は、どの置き場にも無い。取り込み済みなので predecessors から消してもらう。
		{"nowhere", "i0009-01-01", func(*ticketkit.Tree) {}, []string{"にもありません", "predecessors から削除してください"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := authority(t)
			c.setup(tr)
			text := strings.Replace(samples.ChildText("i0001-01-02", "i0001", 1, []string{"wip/research/*"}), "phase: 1\n", "phase: 1\npredecessors: ["+c.predecessor+"]\n", 1)
			tr.Propose("i0001-01-02", text, "")
			tr.CommitParent("tickets")
			after := agreePreview(tr)
			if after.batchHas("i0001-01-02") {
				t.Error("先行が満たされないのに承認の束に入った")
			}
			for _, w := range c.want {
				if !after.rejectedHas(w) {
					t.Errorf("拒否に %q が無い: %+v", w, after.Rejected)
				}
			}
		})
	}
}

// マージのコンフリクトマーカーなどで読めなくなった子の承認済みチケットが、親のツリーにある。
const conflictText = "<<<<<<< HEAD\nticket: x\n=======\nticket: y\n>>>>>>> other\n"

func startSecondChild(tr *ticketkit.Tree) string {
	tr.T.Helper()
	tr.Propose("i0001-01-02", samples.ChildText("i0001-01-02", "i0001", 1, []string{"wip/research/*"}), "")
	tr.CommitParent("approval-request second")
	ticketkit.OK(tr.T, tr.Approve())
	tree := tr.Worktree("i0001-01-02", "i0001")
	ticketkit.OK(tr.T, tr.Ccnavi("ticket", "start", "i0001-01-02"))
	return tree
}

// 読めない子のワークツリーで、親の範囲を借りて書けることはない。子のワークツリー自身に読めるコピーがあれば、親のワークツリーの外に
// しか無いチケットとして止まる（deny）。そのコピーも読めなければ、チケットのないツリーと同じ扱い（undeclared。親の範囲では許さない）。
func TestTheUnreadableChildIsNotGivenTheRangeOfTheParent(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	second := startSecondChild(tr)
	ticketkit.Write(t, tr.ApprovedPath("doing", "i0001-01-02"), conflictText)
	blocked := writeTo(tr, second, "wip/research/a")
	if d := ticketkit.Decision(t, blocked); d != "deny" {
		t.Errorf("判定 = %q（deny のはず）\n%s", d, blocked.Stdout)
	}
	ticketkit.Has(t, ticketkit.Reason(t, blocked), "DENY_TICKET_BLOCKED")
	ticketkit.Write(t, filepath.Join(second, ".ccnavi", "approved", "doing", "i0001-01-02.md"), conflictText)
	undeclared := writeTo(tr, second, "wip/research/a")
	if d := ticketkit.Decision(t, undeclared); d == "allow" {
		t.Errorf("読めない子に書けた\n%s", undeclared.Stdout)
	}
	ticketkit.Has(t, ticketkit.Reason(t, undeclared), "UNDECLARED")
}

// 読めない子があっても、読める兄弟と親の書き込みは止まらない。読めないことは `sync check` の表示と終了コードでだけ伝わる。
func TestTheSiblingsKeepWorkingAndTheCheckNamesTheUnreadableCopy(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	startSecondChild(tr)
	ticketkit.Write(t, tr.ApprovedPath("doing", "i0001-01-02"), conflictText)
	for _, where := range [][2]string{{tr.ChildTree(childName), "wip/research/a"}, {tr.ParentTree, "wip/handoff/x"}} {
		if d := ticketkit.Decision(t, writeTo(tr, where[0], where[1])); d == "deny" {
			t.Errorf("%s の書き込みが止まった", where[0])
		}
	}
	check := tr.Ccnavi("sync", "check", "i0001")
	if check.Code != 1 {
		t.Fatalf("code %d\n%s%s", check.Code, check.Stdout, check.Stderr)
	}
	ticketkit.Has(t, check.Stdout, "i0001-01-02.md")
}

// 承認では、閉じた識別子の再利用を検知しない（検知は開始時のホストへの照会だけ）。
func TestAProposalThatReusesAnArchivedIdentifierIsApproved(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	ticketkit.Write(t, filepath.Join(tr.Root, "logs", "archive", "self", "done", "i0002.md"),
		"---\nversion: 1\nticket: i0002\ntitle: 閉じた親\ncompleted_at: 2026-01-01T00:00:00+0900\n---\n")
	other := tr.Worktree("i0002", "main")
	tr.Propose("i0002", samples.ParentText("i0002", samples.Plan("research"), samples.ParentOpts{Copy: tr.Phases}), other)
	ticketkit.OK(t, tr.Approve())
	if !ticketkit.Exists(filepath.Join(other, ".ccnavi", "approved", "doing", "i0002.md")) {
		t.Error("承認済みの置き場に無い")
	}
}

func TestReadyNeedsTheParentInDone(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	r := ticketkit.Fails(t, tr.Ccnavi("--cwd", tr.ParentTree, "review", "ready", "--result", fixtureJSON(tr)))
	ticketkit.Has(t, r.Stderr, "done/ にありません")
	ticketkit.Has(t, r.Stderr, "finish i0001")
}

// settings.local.json に承認を左右する設定（CCNAVI_MODE・CCNAVI_RESTORE_IF_DENY）を置くと、--lint が名指しする。
func TestLocalSettingsMayHoldNothingThatDecidesApproval(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	path := filepath.Join(tr.Root, ".claude", "settings.local.json")
	named := func() []problem {
		var out []problem
		for _, p := range lintProblems(tr) {
			if strings.Contains(p.Detail, "settings.local.json") {
				out = append(out, p)
			}
		}
		return out
	}
	ticketkit.Write(t, path, `{"env": {"CCNAVI_LOG_LEVEL": "DEBUG"}}`)
	if got := named(); len(got) != 0 {
		t.Fatalf("無害な設定を名指しした: %v", got)
	}
	ticketkit.Write(t, path, `{"env": {"CCNAVI_MODE": "disable", "CCNAVI_RESTORE_IF_DENY": "disable", "CCNAVI_LOG_LEVEL": "DEBUG"}}`)
	got := named()
	if len(got) != 2 {
		t.Fatalf("名指しした件数 = %v", got)
	}
	var details []string
	for _, p := range got {
		if p.Severity != "error" {
			t.Errorf("重さ = %q", p.Severity)
		}
		details = append(details, p.Detail)
	}
	all := strings.Join(details, "\n")
	ticketkit.Has(t, all, "CCNAVI_MODE")
	ticketkit.Has(t, all, "CCNAVI_RESTORE_IF_DENY")
}
