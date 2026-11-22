package flow

// `--explain --json`（ボードの JSON）の受入テスト。
//
// VS Code のボード拡張が読む形を、判定と同じ関数で組んでいることを確かめる。見るのは次のとおり。
//
//  1. 提案・承認済みチケット・マーカー・ワークツリーの有無が、識別子ごとに 1 件にまとまって出る
//  2. 承認待ち（承認済みチケットの無い提案）が pending_approval に出る
//  3. 親のフェーズとレビュー待ちが parents に出る
//  4. チケット制御が disable なら、空のボードと理由を返す。settings（mode・ticket_control）は settings ファイルの重なりに従う
//
// 拡張側のフィクスチャ（extensions/vscode/ccnavi-board/test/fixtures/board.json）と同じ形であることも見る。形を変えたら
// `go test ./internal/clitest/ticket/flow -run BoardShape -update-board-fixtures` でフィクスチャを書き直す。
import (
	"ccnavi/internal/harness/ticketkit"
	"cmp"
	"encoding/json"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
)

// boardSceneBuild は親 1 本（research → design）。フェーズ 1 は閉じ、フェーズ 2 は着手済みで、同じフェーズに未承認の子が 1 枚ある。
// その子は着手済みの子のワークツリーにも入っている。
func boardSceneBuild(t *testing.T) *Tree {
	t.Helper()
	return cachedTree(t, "board-scene", func(tr *Tree) {
		tr.FamilyPlan(planOf("research", "design"), nil)
		tr.Propose("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), "")
		tr.CommitParent("tickets")
		ticketkit.OK(tr.T, tr.Approve())
		tr.RunChild("i0001-01-01", ticketkit.File{Path: "wip/research/summary.md", Text: "まとめ\n"})
		ticketkit.OK(tr.T, tr.CloseChild("i0001-01-01"))
		tr.Merge("i0001-01-01")

		tr.Propose("i0001-02-02", child("i0001-02-02", 2, "wip/design/*"), "")
		tr.CommitParent("tickets")
		ticketkit.OK(tr.T, tr.Approve())
		// 承認の後、ワークツリーを切る前に次の子を提案する。切ったワークツリーは親のブランチの承認済みチケットなので、
		// この提案がそこにも見える。
		tr.Propose("i0001-02-03", child("i0001-02-03", 2, "wip/design/*"), "")
		tr.CommitParent("tickets")
		tr.RunChild("i0001-02-02")
	})
}

func (tr *Tree) board(extra ...string) map[string]any {
	tr.T.Helper()
	return ticketkit.ParseJSON(tr.T, ticketkit.OK(tr.T, tr.Ccnavi(append([]string{"--explain", "--json"}, extra...)...)).Stdout)
}

func (tr *Tree) boardByID(extra ...string) map[string]map[string]any {
	tr.T.Helper()
	by := map[string]map[string]any{}
	for _, tk := range jl(tr.board(extra...), "tickets") {
		by[js(tk, "ticket")] = jm(tk)
	}
	return by
}

func idsOf(by map[string]map[string]any) []string {
	var ids []string
	for k := range by {
		ids = append(ids, k)
	}
	slices.Sort(ids)
	return ids
}

type treeState struct{ tree, state string }

func seen(t map[string]any, key string) []treeState {
	var out []treeState
	for _, s := range jl(t, key) {
		out = append(out, treeState{js(s, "tree"), js(s, "state")})
	}
	slices.SortFunc(out, func(a, b treeState) int {
		return cmp.Or(strings.Compare(a.tree, b.tree), strings.Compare(a.state, b.state))
	})
	return out
}

func TestBoardTicketsCarryProposalCopyMarksAndWorktree(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	board := tr.board()
	if jf(board, "version") != 1 {
		t.Errorf("version = %v", board["version"])
	}
	by := tr.boardByID()
	if got := idsOf(by); !slices.Equal(got, []string{"i0001", "i0001-01-01", "i0001-02-02", "i0001-02-03"}) {
		t.Fatalf("ids = %v", got)
	}
	closed := by["i0001-01-01"]
	if closed["proposal"] != nil || js(closed, "copy", "status") != "closed" || !jb(closed, "worktree", "exists") || js(closed, "completed_at") == "" || jb(closed, "in_progress") {
		t.Errorf("closed = %v", closed)
	}
	doing := by["i0001-02-02"]
	// 着手していて終わっていない承認済みチケットだけが作業中。拡張はこれで保存を止める。
	if doing["proposal"] != nil || js(doing, "copy", "status") != "open" || !jb(doing, "worktree", "exists") || !strings.HasSuffix(js(doing, "worktree", "path"), "i0001-02-02") ||
		js(doing, "parent") != "i0001" || jf(doing, "phase") != 2 || !jb(doing, "in_progress") {
		t.Errorf("doing = %v", doing)
	}
	waiting := by["i0001-02-03"]
	trees := []string{}
	for _, s := range seen(waiting, "seen_in") {
		trees = append(trees, s.tree)
	}
	if js(waiting, "proposal", "state") != "todo" || js(waiting, "proposal", "tree") != "i0001" || jm(waiting, "copy")["status"] != "none" || len(jm(waiting, "copy")) != 1 ||
		jb(waiting, "in_progress") || jb(waiting, "worktree", "exists") || !slices.Equal(trees, []string{"i0001", "i0001-02-02"}) {
		t.Errorf("waiting = %v", waiting)
	}
	parent := by["i0001"]
	// 親も着手していて閉じていなければ作業中。
	if js(parent, "parent") != "" || js(parent, "copy", "status") != "open" || !jb(parent, "in_progress") {
		t.Errorf("parent = %v", parent)
	}
}

// 提案の has_plan は、親の提案が全体計画（plan:）を持つか。feedback: だけの親は偽にする（計画の順序を直すタブを開けない）。
func TestTheProposalSaysWhetherItHasAPlan(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	tr.Propose("i0002", parentOf("i0002", "research", "design"), "")
	tr.Propose("i0003", parentWith("i0003", nil, noCopy()), "")
	tr.Propose("i0004", parentWith("i0004", nil, noCopyFeedback("design-feedback")), "")
	tr.CommitParent("tickets")
	by := tr.boardByID()
	if !jb(by["i0002"], "proposal", "has_plan") || jb(by["i0003"], "proposal", "has_plan") || jb(by["i0004"], "proposal", "has_plan") {
		t.Errorf("has_plan: i0002=%v i0003=%v i0004=%v", jv(by["i0002"], "proposal"), jv(by["i0003"], "proposal"), jv(by["i0004"], "proposal"))
	}
	wantCode(t, tr.Ccnavi("--plan-order", "i0004", "--json"), 1)
	if jb(by["i0001-02-03"], "proposal", "has_plan") || by["i0001-02-02"]["proposal"] != nil {
		t.Error("子の提案か、提案の無い承認済みチケットに has_plan が付いた")
	}
}

// 複数のツリーにあること自体は普通。正とするツリーに 1 つあれば散在ではない。
func TestScatteredIsEmptyWhileTheHomeTreeHoldsOneCopy(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	by := tr.boardByID()
	for id, tk := range by {
		if len(jl(tk, "scattered")) != 0 {
			t.Errorf("%s の scattered = %v", id, tk["scattered"])
		}
	}
	// 正常な場面でも、複数のツリーにあるし状態も食い違う（ワークツリーはブランチを切った時点のコピーを持つ）。
	// 数や状態の違いを食い違いに数えない。
	closed := seen(by["i0001-01-01"], "seen_in")
	states := map[string]bool{}
	for _, s := range closed {
		states[s.state] = true
	}
	if len(closed) != 3 || len(states) != 2 || !states["doing"] || !states["done"] {
		t.Errorf("seen_in = %v", closed)
	}
}

// moveProposal は提案を 1 つ、ツリーからツリーへ手で動かす。
func (tr *Tree) moveProposal(id, sourceTree, targetTree string) {
	tr.T.Helper()
	source := filepath.Join(sourceTree, "wip", "proposals", "todo", id+".md")
	text := ticketkit.Read(tr.T, source)
	removeFile(tr.T, source)
	ticketkit.Write(tr.T, filepath.Join(targetTree, "wip", "proposals", "todo", id+".md"), text)
}

func TestScatteredListsTheCopiesWhenTheHomeTreeHoldsNone(t *testing.T) {
	cases := []struct {
		name      string
		target    func(tr *Tree) string
		scattered []treeState
		seenIn    []treeState // 空なら見ない
	}{
		// 親のツリーが無ければ、元ツリーのコピーで代用せず、決まらないものとして全部出す。
		{"the root holds one", func(tr *Tree) string { return tr.Root },
			[]treeState{{"", "todo"}, {"i0001-02-02", "todo"}}, []treeState{{"", "todo"}, {"i0001-02-02", "todo"}}},
		// 親のツリーに無ければ、どれが正か決まらない。候補を全部出す。
		{"a worktree elsewhere holds one", func(tr *Tree) string { return filepath.Join(tr.Root, ".claude", "worktrees", "i0001-01-01") },
			[]treeState{{"i0001-01-01", "todo"}, {"i0001-02-02", "todo"}}, nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := boardSceneBuild(t)
			tr.moveProposal("i0001-02-03", tr.ParentTree, c.target(tr))
			by := tr.boardByID()
			if got := seen(by["i0001-02-03"], "scattered"); !slices.Equal(got, c.scattered) {
				t.Errorf("scattered = %v, want %v", got, c.scattered)
			}
			if c.seenIn != nil {
				if got := seen(by["i0001-02-03"], "seen_in"); !slices.Equal(got, c.seenIn) {
					t.Errorf("seen_in = %v, want %v", got, c.seenIn)
				}
			}
			// 巻き込まれていない識別子は空のまま。
			if len(jl(by["i0001-02-02"], "scattered")) != 0 {
				t.Errorf("i0001-02-02 の scattered = %v", by["i0001-02-02"]["scattered"])
			}
		})
	}
}

// 動かす途中で止まった形跡は、正とするツリーの中でも言う（--lint と同じ数え方）。
func TestScatteredSaysTheSameTreeHoldingTwoPlaces(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	ticketkit.Write(t, tr.ApprovedPath("done", "i0001-02-02"), ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-02-02")))
	by := tr.boardByID()
	if got := seen(by["i0001-02-02"], "scattered"); !slices.Equal(got, []treeState{{"i0001", "doing"}, {"i0001", "done"}}) {
		t.Errorf("scattered = %v", got)
	}
	ticketkit.Has(t, tr.Ccnavi("--lint", "--mode", "enable").Stdout, "i0001-02-02 が複数の場所にある")
}

// review/ は提案の置き場でもあり承認済みチケットでもある。同じ実体を 2 つと数えない。
func TestATicketWaitingForReviewIsNotScattered(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	for _, tk := range jl(tr.board(), "tickets") {
		if js(tk, "copy", "status") == "review" {
			t.Errorf("閉じる前にレビュー待ちがある: %v", js(tk, "ticket"))
		}
	}
	ticketkit.OK(t, tr.CloseChild("i0001-02-02"))
	waiting := tr.boardByID()["i0001-02-02"]
	if js(waiting, "copy", "status") != "review" || jb(waiting, "in_progress") || len(jl(waiting, "scattered")) != 0 {
		t.Errorf("waiting = %v", waiting)
	}
	// 同じファイルを 2 つの走査が拾っても、チケットは 1 ツリーに 1 つ。
	s := seen(waiting, "seen_in")
	if len(slices.Compact(slices.Clone(s))) != len(s) {
		t.Errorf("seen_in に重複: %v", s)
	}
}

// 場面 A。承認の前に切ったワークツリーの todo/ の提案は、承認のあとは提案に出さない（残りの古い提案が「未着手」に戻らない）。
func TestAStaleTodoCopyLeftInAWorktreeIsNotWaitingAfterApproval(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	ticketkit.OK(t, tr.Approve())
	stale := filepath.Join(tr.Root, ".claude", "worktrees", "i0001-02-02", "wip", "proposals", "todo", "i0001-02-03.md")
	if !ticketkit.Exists(stale) {
		t.Fatal("古い提案が残っていない")
	}
	board := tr.board()
	by := map[string]map[string]any{}
	for _, tk := range jl(board, "tickets") {
		by[js(tk, "ticket")] = jm(tk)
	}
	one := by["i0001-02-03"]
	if one["proposal"] != nil || js(one, "copy", "status") != "open" || len(jl(one, "scattered")) != 0 || len(jl(board, "pending_approval")) != 0 {
		t.Errorf("i0001-02-03 = %v, pending = %v", one, board["pending_approval"])
	}
	// ワークツリーにあること自体は seen_in に残る。
	if !slices.Contains(seen(one, "seen_in"), treeState{"i0001-02-02", "todo"}) {
		t.Errorf("seen_in = %v", seen(one, "seen_in"))
	}
	// 計画の同じ古い提案は --lint も言わない（承認の前に切ったワークツリーに残る普通の形）。
	ticketkit.Lacks(t, tr.Ccnavi("--lint", "--mode", "enable").Stdout, "i0001-02-02/wip/proposals/todo/i0001-02-03.md")
}

// 場面 B。review/ の古いチケットが残ったワークツリーがあっても、閉じたものは閉じたまま。
func TestAStaleReviewCopyLeftInAWorktreeIsNotInProgressAfterClosing(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	ticketkit.OK(t, tr.CloseChild("i0001-02-02"))
	tr.CommitParent("close")
	tr.Worktree("i0001-01-09", "i0001") // review/i0001-02-02 を持ったまま切る
	// 親のツリーで閉じる（レビューを終えて done/ へ）。
	review := tr.childPath("i0001-02-02", "review")
	text := ticketkit.Read(t, review)
	removeFile(t, review)
	ticketkit.Write(t, tr.ApprovedPath("done", "i0001-02-02"), text)
	one := tr.boardByID()["i0001-02-02"]
	if one["proposal"] != nil || js(one, "copy", "status") != "closed" {
		t.Errorf("one = %v", one)
	}
	// review/ の古いチケットは --lint も言わない（ボードと承認待ちからは外す）。
	ticketkit.Lacks(t, tr.Ccnavi("--lint", "--mode", "enable").Stdout, "i0001-01-09/wip/proposals/review/i0001-02-02.md")
}

// 正とするツリーと同じ置き場のチケットは、子のワークツリーに入っているだけ。名指ししない。
func TestAReviewCopyInAWorktreeMatchingTheHomeTreeIsNotNamed(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	ticketkit.OK(t, tr.CloseChild("i0001-02-02"))
	tr.CommitParent("close")
	tr.Worktree("i0001-01-09", "i0001")
	if got := js(tr.boardByID()["i0001-02-02"], "copy", "status"); got != "review" {
		t.Errorf("status = %q", got)
	}
	ticketkit.Lacks(t, tr.Ccnavi("--lint", "--mode", "enable").Stdout, "i0001-01-09/wip/proposals/review/i0001-02-02.md")
}

func TestPendingApprovalListsProposalsWithoutACopy(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	if got := strs(jl(tr.board(), "pending_approval")); !slices.Equal(got, []string{"i0001-02-03"}) {
		t.Errorf("pending_approval = %v", got)
	}
}

func TestParentsCarryPhasesAndGates(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	board := tr.board()
	parents := jl(board, "parents")
	if len(parents) != 1 || js(parents[0], "ticket") != "i0001" {
		t.Fatalf("parents = %v", parents)
	}
	p := parents[0]
	plan := jl(p, "plan")
	if jb(p, "closed") || len(plan) != 2 || plan[0] != "research" || js(plan[1], "type") != "design" || jl(plan[1], "after")[0] != 1.0 || !strings.Contains(js(p, "stage"), "作業中") {
		t.Errorf("parent = %v", p)
	}
	phases := map[int]any{}
	for _, ph := range jl(p, "phases") {
		phases[int(jf(ph, "number"))] = ph
	}
	if len(phases) != 2 {
		t.Fatalf("phases = %v", phases)
	}
	one, two := phases[1], phases[2]
	if js(one, "state") != "ended" || js(one, "type") != "research" || jb(one, "gate_closed") || jb(one, "review_waiting") {
		t.Errorf("phase 1 = %v", one)
	}
	if js(two, "state") != "active" || !slices.Equal(strs(jl(two, "tickets")), []string{"i0001-02-02"}) || js(two, "states", "i0001-02-02") != "doing" || !jb(two, "review_required") {
		t.Errorf("phase 2 = %v", two)
	}
}

func TestWithTicketControlDisabledTheBoardIsEmptyAndSaysWhy(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	board := tr.board("--ticket-control", "disable")
	if len(jl(board, "tickets")) != 0 || len(jl(board, "parents")) != 0 || js(board, "settings", "ticket_control") != "disable" {
		t.Errorf("board = %v", board)
	}
	found := false
	for _, p := range jl(board, "problems") {
		found = found || strings.Contains(p.(string), "CCNAVI_TICKET_CONTROL")
	}
	if !found {
		t.Errorf("problems = %v", board["problems"])
	}
}

func TestSettingsSayTicketControlIsEnabledByDefault(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	if got := js(tr.board(), "settings", "ticket_control"); got != "enable" {
		t.Errorf("ticket_control = %q", got)
	}
	// 承認済みチケットの置き場を空文字で指しても切れない。既定の置き場で有効のまま。
	board := tr.board("--approved", "")
	if js(board, "settings", "ticket_control") != "enable" || !strings.HasSuffix(js(board, "settings", "approved"), "approved") {
		t.Errorf("settings = %v", board["settings"])
	}
}

// hookSettings は .claude/settings.json と settings.local.json の env を置いて、settings を読む。
func (tr *Tree) hookSettings(settingsEnv, localEnv, env map[string]string, extra ...string) (map[string]any, string) {
	tr.T.Helper()
	for name, values := range map[string]map[string]string{"settings.json": settingsEnv, "settings.local.json": localEnv} {
		if values != nil {
			raw, _ := json.Marshal(map[string]any{"env": values})
			ticketkit.Write(tr.T, filepath.Join(tr.Root, ".claude", name), string(raw))
		}
	}
	r := ticketkit.OK(tr.T, tr.Call(ticketkit.Call{Env: env}, append([]string{"--explain", "--json"}, extra...)...))
	return jm(ticketkit.ParseJSON(tr.T, r.Stdout), "settings"), r.Stderr
}

// mode は settings ファイルを Claude Code が重ねるとおりに答える（local が settings.json より強い。settings の env が
// プロセスの環境より強い）。読めない値は enable で、そう言う。ticket_control も同じ重なりに従う。
func TestSettingsFollowTheSettingsFilesAsClaudeCodeLayersThem(t *testing.T) {
	type m = map[string]string
	cases := []struct {
		name               string
		settings, local, e m
		key, want          string
		stderr             string
	}{
		{"mode: nothing says otherwise", nil, nil, nil, "mode", "enable", ""},
		{"mode: settings.json", m{"CCNAVI_MODE": "dry-run"}, nil, nil, "mode", "dry-run", ""},
		{"mode: local wins over settings.json", m{"CCNAVI_MODE": "dry-run"}, m{"CCNAVI_MODE": "enable"}, nil, "mode", "enable", ""},
		{"mode: the process environment", nil, nil, m{"CCNAVI_MODE": "dry-run"}, "mode", "dry-run", ""},
		{"mode: settings win over the process environment", m{"CCNAVI_MODE": "enable"}, nil, m{"CCNAVI_MODE": "dry-run"}, "mode", "enable", ""},
		{"mode: disable in the settings env", m{"CCNAVI_MODE": "disable"}, nil, nil, "mode", "disable", ""},
		{"mode: an unreadable value", m{"CCNAVI_MODE": "off"}, nil, nil, "mode", "enable", "is not a mode"},
		{"ticket control: settings.json", m{"CCNAVI_TICKET_CONTROL": "disable"}, nil, nil, "ticket_control", "disable", ""},
		{"ticket control: local wins", m{"CCNAVI_TICKET_CONTROL": "disable"}, m{"CCNAVI_TICKET_CONTROL": "enable"}, nil, "ticket_control", "enable", ""},
		{"ticket control: an unreadable value", m{"CCNAVI_TICKET_CONTROL": "nope"}, m{}, nil, "ticket_control", "enable", ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := newTree(t)
			got, stderr := tr.hookSettings(c.settings, c.local, c.e)
			if js(got, c.key) != c.want {
				t.Errorf("%s = %q, want %q", c.key, js(got, c.key), c.want)
			}
			if c.stderr != "" {
				ticketkit.Has(t, stderr, c.stderr)
			}
		})
	}
}

// settings に出す値と、ボードの中身を組むときの値を食い違わせない。
func TestTicketControlFromTheSettingsFilesEmptiesTheBoard(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	ticketkit.Write(t, filepath.Join(tr.Root, ".claude", "settings.json"), `{"env": {"CCNAVI_TICKET_CONTROL": "disable"}}`)
	board := tr.board()
	if js(board, "settings", "ticket_control") != "disable" || len(jl(board, "tickets")) != 0 {
		t.Errorf("board = %v", board)
	}
}

// 承認待ちの子だけが要対応。順調な場面では不備は無い。レビュー準備中になれば子も親も要対応。
func TestAttentionAndIssuesFollowTheScene(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	by := tr.boardByID()
	want := map[string]bool{"i0001": false, "i0001-01-01": false, "i0001-02-02": false, "i0001-02-03": true}
	for id, tk := range by {
		if jb(tk, "attention") != want[id] {
			t.Errorf("%s の attention = %v", id, tk["attention"])
		}
		if len(jl(tk, "issues")) != 0 {
			t.Errorf("%s の issues = %v", id, tk["issues"])
		}
	}
	ticketkit.OK(t, tr.CloseChild("i0001-02-02"))
	by = tr.boardByID()
	if !jb(by["i0001-02-02"], "attention") || !jb(by["i0001"], "attention") || jb(by["i0001-01-01"], "attention") {
		t.Errorf("閉じた後の attention: %v %v %v", by["i0001-02-02"]["attention"], by["i0001"]["attention"], by["i0001-01-01"]["attention"])
	}
}

// finish が書いた merged_sha（合流した子の先頭）をボードの JSON に載せる（表示だけ）。
func TestTheMergedHeadOfAChildIsShown(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	ticketkit.OK(t, tr.CloseChild("i0001-02-02"))
	found := tr.childPath("i0001-02-02", "review")
	for _, state := range []string{"done", "doing"} {
		if p := tr.ApprovedPath(state, "i0001-02-02"); ticketkit.Exists(p) {
			found = p
		}
	}
	head, rest, _ := strings.Cut(ticketkit.Read(t, found), "\n---\n")
	head = regexp.MustCompile(`\nmerged_sha:[^\n]*`).ReplaceAllString(head, "")
	ticketkit.Write(t, found, head+"\nmerged_sha: \""+strings.Repeat("a", 40)+"\"\n---\n"+rest)
	by := tr.boardByID()
	if js(by["i0001-02-02"], "merged_sha") != strings.Repeat("a", 40) {
		t.Errorf("merged_sha = %v", by["i0001-02-02"]["merged_sha"])
	}
	// finish で閉じた子は、合流した先頭を持つ。
	if !regexp.MustCompile(`^[0-9a-f]{40}$`).MatchString(js(by["i0001-01-01"], "merged_sha")) {
		t.Errorf("merged_sha = %v", by["i0001-01-01"]["merged_sha"])
	}
}

// 拡張のフィクスチャ（board.json）と同じ形（キーの集合）。
func TestBoardShapeMatchesTheExtensionFixture(t *testing.T) {
	t.Parallel()
	tr := boardSceneBuild(t)
	out := ticketkit.OK(t, tr.Ccnavi("--explain", "--json")).Stdout
	if *updateBoardFixtures {
		rewriteFixture(t, "board.json", out, tr.Root)
	}
	fixture := readFixture(t, "board.json")
	board := ticketkit.ParseJSON(t, out)
	sameKeys := func(what string, a, b any) {
		t.Helper()
		if !slices.Equal(sortedKeys(jm(a)), sortedKeys(jm(b))) {
			t.Errorf("%s のキーが違う: フィクスチャ %v, 出力 %v", what, sortedKeys(jm(a)), sortedKeys(jm(b)))
		}
	}
	sameKeys("先頭", fixture, board)
	sameKeys("tickets[0]", jv(fixture, "tickets", 0), jv(board, "tickets", 0))
	sameKeys("tickets[0].copy", jv(fixture, "tickets", 0, "copy"), jv(board, "tickets", 0, "copy"))
	proposalOf := func(root map[string]any) any {
		for _, tk := range jl(root, "tickets") {
			if p := jv(tk, "proposal"); p != nil {
				return p
			}
		}
		return nil
	}
	sameKeys("proposal", proposalOf(fixture), proposalOf(board))
	sameKeys("parents[0]", jv(fixture, "parents", 0), jv(board, "parents", 0))
	sameKeys("parents[0].phases[0]", jv(fixture, "parents", 0, "phases", 0), jv(board, "parents", 0, "phases", 0))
	sameKeys("sums[0].phases", jv(fixture, "sums", 0, "phases"), jv(board, "sums", 0, "phases"))
}
