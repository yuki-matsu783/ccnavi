package life

import (
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 親のブランチ名と識別子の変換（識別子 = ブランチ名の `/` を `-` に置き換えたもの）。識別子 feature-12-login の親が feature/12-login を
// 直接チェックアウトする。見るのは次のとおり。
//  1. 承認画面と JSON にブランチの行は出ない。ブランチ名と同じ識別子の既存ブランチが別の所にあれば warn
//  2. 識別子が大文字小文字だけ違う親は承認しない
//  3. lint: 親のワークツリーの HEAD を変換した識別子がフォルダ名と合わなければ warn。既存のブランチを採用した親（親のワークツリーが在り、
//     HEAD が合う）には名前の形式の warn を出さない
//  4. write-and-push family の branch は親のワークツリーの HEAD から読む。読めない・合わないときは branch_refused
//  5. Chrome の入口: ブランチ名を変換した識別子の親チケットがそのブランチの上にあれば親子のチケットで、書くものはそのブランチだけ。
//     同じ識別子になる別のブランチに親チケットがあれば、先行の親子のチケットでも決めない

const (
	pbParent = "feature-12-login"
	pbBranch = "feature/12-login"
	pbChild  = pbParent + "-01-01"
	zeros    = "0000000000000000000000000000000000000000"
	// Chrome の要求に付ける時刻（記録に書く時刻）。
	chromeStamp = "2026-09-29T12:00:00+0900"
)

// pbWS はワークスペースと、親のワークツリー（.claude/worktrees/feature-12-login）の置き場。親の i0001 は持たない。
type pbWS struct {
	t    *testing.T
	root string
	tree string
}

func newPBWS(t *testing.T) *pbWS {
	t.Helper()
	root := base.TempDir(t)
	writeIn(t, root, ".claude/keep", "")
	run.Git(t, root, "init", "--quiet", "-b", "main")
	writeIn(t, root, "src/keep.py", "print(1)\n")
	writeIn(t, root, ".gitignore", ".claude/\n")
	run.Git(t, root, "add", "-A")
	run.Git(t, root, "commit", "--quiet", "-m", "init")
	ticketkit.Write(t, base.CommonPath(root, "rules"), samples.Rules)
	ticketkit.Write(t, base.ConfigPath(root, "phases"), samples.Phases)
	return &pbWS{t: t, root: root, tree: filepath.Join(root, ".claude", "worktrees", pbParent)}
}

func (w *pbWS) ccnavi(stdin string, args ...string) run.Result {
	w.t.Helper()
	prefix := []string{"--root", w.root, "--guard-core-files", "disable", "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	return run.Ccnavi(w.t, samples.Place(prefix, args), run.Opts{Input: stdin, Dir: base.Root()})
}

func (w *pbWS) git(dir string, args ...string) string {
	w.t.Helper()
	return run.Git(w.t, dir, args...)
}

// parentTree は親のワークツリーを切る。create なら branch を main から新しく切り、そうでなければ既存の branch を使う。
func (w *pbWS) parentTree(branch string, create bool) {
	w.t.Helper()
	args := []string{"worktree", "add", "--quiet", w.tree}
	if create {
		args = append(args, "-b", branch, "main")
	} else {
		args = append(args, branch)
	}
	w.git(w.root, args...)
}

// propose は親の提案を where（空なら親のワークツリー）の wip/proposals/todo に書く。
func (w *pbWS) propose(where, ident string) {
	w.t.Helper()
	if where == "" {
		where = w.tree
	}
	text := samples.ParentText(ident, samples.Plan("design"), samples.ParentOpts{Allow: []string{"src/*", "wip/*"}})
	writeIn(w.t, where, "wip/proposals/todo/"+ident+".md", text)
}

type pbPreview struct {
	Verify         struct{ OK bool } `json:"verify"`
	Batch          []map[string]any  `json:"batch"`
	BranchWarnings []any             `json:"branch_warnings"`
	Text           string            `json:"text"`
	Rejected       []struct {
		Ticket   string   `json:"ticket"`
		Problems []string `json:"problems"`
	} `json:"rejected"`
}

func (w *pbWS) preview() pbPreview {
	w.t.Helper()
	var p pbPreview
	jsonOf(w.t, w.ccnavi("", "--agree", "--preview", "--verify", "--json").Stdout, &p)
	return p
}

func (w *pbWS) approve() {
	w.t.Helper()
	ticketkit.OK(w.t, w.ccnavi("y\n", "user", "agree"))
}

func (w *pbWS) lint() string {
	w.t.Helper()
	return w.ccnavi("", "--lint", "--mode", "enable").Stdout
}

func TestTheScreenHasNoBranchLine(t *testing.T) {
	t.Parallel()
	w := newPBWS(t)
	w.parentTree(pbBranch, true)
	w.propose("", pbParent)
	body := w.preview()
	if !body.Verify.OK {
		t.Fatalf("verify が通らない: %+v", body)
	}
	for _, key := range []string{"branch", "existing_branch"} {
		if _, ok := body.Batch[0][key]; ok {
			t.Errorf("batch に %s がある", key)
		}
	}
	ticketkit.Lacks(t, body.Text, "■ ブランチ")
	if len(body.BranchWarnings) != 0 {
		t.Errorf("warn = %v", body.BranchWarnings)
	}
}

// 提案が親のワークツリーの外（ここではワークスペースルート）にあれば、承認の対象に入らない。親のブランチ名の警告は、親のワークツリーの
// 中の提案にだけ付く（その HEAD が親のブランチ）。
func TestAProposalOutsideTheParentWorktreeIsRefusedNotWarned(t *testing.T) {
	t.Parallel()
	w := newPBWS(t)
	w.git(w.root, "branch", pbBranch)
	w.propose(w.root, pbParent)
	body := w.preview()
	if len(body.Batch) != 0 || len(body.BranchWarnings) != 0 {
		t.Errorf("batch = %v, warn = %v", body.Batch, body.BranchWarnings)
	}
	found := false
	for _, r := range body.Rejected {
		found = found || strings.Contains(strings.Join(r.Problems, " "), "親のワークツリー")
	}
	if !found {
		t.Errorf("拒否の理由に「親のワークツリー」が無い: %+v", body.Rejected)
	}
}

func TestAProposalOnItsOwnBranchIsNotWarned(t *testing.T) {
	t.Parallel()
	w := newPBWS(t)
	w.git(w.root, "branch", pbBranch)
	w.parentTree(pbBranch, false)
	w.propose("", pbParent)
	if got := w.preview().BranchWarnings; len(got) != 0 {
		t.Errorf("warn = %v", got)
	}
}

// 大文字小文字を区別しないファイルシステム（macOS の既定）では、2 つの提案が 1 つのファイルになり、「大文字小文字だけが違う識別子」の
// 状態を作れない。
func TestIdentifiersDifferingOnlyInCaseAreRefused(t *testing.T) {
	t.Parallel()
	if !base.CanCaseSensitive() {
		t.Skip("大文字小文字を区別しないファイルシステム")
	}
	w := newPBWS(t)
	other := "Feature-12-Login"
	w.propose(w.root, other)
	w.propose(w.root, pbParent)
	body := w.preview()
	var all []string
	var tickets []string
	for _, r := range body.Rejected {
		all = append(all, r.Problems...)
		tickets = append(tickets, r.Ticket)
	}
	ticketkit.Has(t, strings.Join(all, " "), "大文字小文字だけが違う識別子")
	slices.Sort(tickets)
	if want := []string{other, pbParent}; !slices.Equal(tickets, want) {
		t.Errorf("拒否した提案 = %v", tickets)
	}
}

func TestLintAboutTheParentBranch(t *testing.T) {
	t.Parallel()
	t.Run("quiet_on_the_branch_of_the_identifier", func(t *testing.T) {
		t.Parallel()
		w := newPBWS(t)
		w.parentTree(pbBranch, true)
		w.propose("", pbParent)
		w.approve()
		ticketkit.Lacks(t, w.lint(), "の上にある")
	})
	t.Run("warns_when_the_parent_tree_is_on_another_branch", func(t *testing.T) {
		t.Parallel()
		w := newPBWS(t)
		w.parentTree(pbBranch, true)
		w.propose("", pbParent)
		w.approve()
		w.git(w.tree, "add", "-A")
		w.git(w.tree, "commit", "-q", "-m", "approve")
		w.git(w.tree, "checkout", "-q", "-b", "elsewhere")
		out := w.lint()
		ticketkit.Has(t, out, "親 "+pbParent+" のワークツリーが elsewhere の上にある")
		ticketkit.Has(t, out, "`/` を `-` に置き換えたものがフォルダ名（"+pbParent+"）")
	})
	t.Run("names_a_left_over_field_as_an_error", func(t *testing.T) {
		t.Parallel()
		w := newPBWS(t)
		w.parentTree(pbBranch, true)
		text := samples.ParentText(pbParent, samples.Plan("design"), samples.ParentOpts{Allow: []string{"src/*", "wip/*"}})
		lines := strings.Split(text, "\n")
		for i, l := range lines {
			if strings.HasPrefix(l, "ticket:") {
				lines = append(lines[:i+1], append([]string{"branch: " + pbBranch}, lines[i+1:]...)...)
				break
			}
		}
		writeIn(t, w.tree, "wip/proposals/todo/"+pbParent+".md", strings.Join(lines, "\n"))
		ticketkit.Has(t, w.lint(), "`branch` は指定できない欄")
	})
	// 既存のブランチを採用した親（ワークツリーが在り、HEAD が合う）は名前の形式を言わない。
	t.Run("does_not_say_the_name_form_of_an_adopted_parent", func(t *testing.T) {
		t.Parallel()
		w := newPBWS(t)
		ident := "weird-login"
		w.tree = filepath.Join(w.root, ".claude", "worktrees", ident)
		w.propose(w.root, ident)
		ticketkit.Has(t, w.lint(), ident+": 親の識別子は")
		if err := removeAll(filepath.Join(w.root, "wip")); err != nil {
			t.Fatal(err)
		}
		w.git(w.root, "worktree", "add", "--quiet", w.tree, "-b", "weird/login", "main")
		w.propose("", ident)
		ticketkit.Lacks(t, w.lint(), ident+": 親の識別子は")
	})
}

func TestWriteAndPushFamilyReadsTheBranchFromHead(t *testing.T) {
	t.Parallel()
	w := newPBWS(t)
	w.parentTree(pbBranch, true)
	w.propose("", pbParent)
	w.approve()
	done := w.ccnavi("", "write-and-push", "family", pbParent)
	ticketkit.Has(t, done.Stdout, "branch "+pbBranch+"\n")
}

func TestWriteAndPushFamilyRefusesWithoutAMatchingHead(t *testing.T) {
	t.Parallel()
	w := newPBWS(t)
	done := w.ccnavi("", "write-and-push", "family", "feature-99-none")
	ticketkit.Has(t, done.Stdout, "branch_refused ")
	ticketkit.Lacks(t, done.Stdout, "\nbranch ")
	w.parentTree(pbBranch, true)
	w.git(w.tree, "checkout", "-q", "-b", "elsewhere")
	done = w.ccnavi("", "write-and-push", "family", pbParent)
	ticketkit.Has(t, done.Stdout, "branch_refused ")
	ticketkit.Lacks(t, done.Stdout, "\nbranch ")
}

// ---- Chrome の入口

func workspaceFiles(t *testing.T) map[string]string {
	t.Helper()
	return map[string]string{
		".claude/settings.json":             `{"env": {"CCNAVI_TICKET_CONTROL": "enable"}}` + "\n",
		".ccnavi/config/project/phases.yml": samples.Phases,
		".ccnavi/config/common/rules.yml":   samples.Rules,
		".ccnavi/scripts/ccnavi-common.sh":  "#!/bin/sh\nCCNAVI_COMPAT=" + exe.Compat(t) + "\n",
	}
}

// approvedText は承認済みの印を frontmatter の終わりに足す（子の提案の形に合わせた置き換えで、親の本文では何も起きない）。
func approvedText(text string) string {
	return strings.Replace(text, "---\n\n本文", "ccnavi_approved:\n  approved_at: 2026-10-01T00:00:00+0900\n---\n\n本文", 1)
}

// familyFiles は親のブランチの置き場。承認済みの親チケットと、承認待ちの子の提案。
func familyFiles(ident string, predecessors ...string) map[string]string {
	child := samples.ChildTextReview(ident+"-01-01", ident, 1, []string{"wip/research/*"}, samples.Off)
	if len(predecessors) > 0 {
		lines := strings.Split(child, "\n")
		extra := []string{"predecessors:"}
		for _, p := range predecessors {
			extra = append(extra, "  - "+p)
		}
		lines = append(lines[:5], append(extra, lines[5:]...)...)
		child = strings.Join(lines, "\n")
	}
	return map[string]string{
		".ccnavi/approved/doing/" + ident + ".md":   approvedText(samples.ParentText(ident, samples.Plan("research"), samples.ParentOpts{Allow: []string{"src/*", "wip/*"}})),
		"wip/proposals/todo/" + ident + "-01-01.md": child,
	}
}

type snapshotBranch struct {
	Head  string            `json:"head"`
	Files map[string]string `json:"files"`
}

// chromeAsk は Chrome の入口に op の要求を 1 件渡す。branches が nil なら親のブランチ 1 本（familyFiles）。absent は nil なら
// 識別子と同じ名前のブランチ（同じ識別子になる別のブランチが無いかを確かめに読む）がホストに無い形。
func chromeAsk(t *testing.T, op string, branches map[string]snapshotBranch, absent []string, extra map[string]any) map[string]any {
	t.Helper()
	files := workspaceFiles(t)
	all := map[string]any{"main": map[string]any{"head": zeros, "files": files, "binary": []any{}}}
	if branches == nil {
		branches = map[string]snapshotBranch{pbBranch: {Head: zeros, Files: familyFiles(pbParent)}}
	}
	for name, b := range branches {
		all[name] = b
	}
	if absent == nil {
		absent = []string{pbParent}
	}
	request := map[string]any{
		"schema": samples.ChromeSchema, "op": op, "family": pbBranch, "stamp": chromeStamp,
		"settings": files[".claude/settings.json"],
		"snapshot": map[string]any{"integration": map[string]any{"name": "main", "source": "default", "head": zeros}, "branches": all, "absent": absent},
	}
	for k, v := range extra {
		request[k] = v
	}
	raw, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	return ticketkit.ParseJSON(t, samples.ChromeHandle(t, exe.ChromePath(t), string(raw), filepath.Join(base.TempDir(t), "memfs")))
}

type family struct {
	Name     string `json:"name"`
	Family   string `json:"family"`
	Conflict string `json:"conflict"`
}

func familiesOf(t *testing.T, answer map[string]any) []family {
	t.Helper()
	raw, err := json.Marshal(answer["families"])
	if err != nil {
		t.Fatal(err)
	}
	var out []family
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestChromeFamiliesAreNamedByTheConvertedBranch(t *testing.T) {
	t.Parallel()
	t.Run("an_approved_parent", func(t *testing.T) {
		t.Parallel()
		found := chromeAsk(t, "families", nil, nil, map[string]any{"candidates": []string{pbBranch}})
		if _, bad := found["error"]; bad {
			t.Fatal(found)
		}
		if got := familiesOf(t, found); len(got) != 1 || got[0] != (family{pbBranch, pbParent, ""}) {
			t.Errorf("families = %v", got)
		}
	})
	t.Run("a_proposal_is_a_family_too", func(t *testing.T) {
		t.Parallel()
		proposal := map[string]string{"wip/proposals/todo/" + pbParent + ".md": samples.ParentText(pbParent, samples.Plan("research"), samples.ParentOpts{Allow: []string{"src/*"}})}
		found := chromeAsk(t, "families", map[string]snapshotBranch{pbBranch: {zeros, proposal}}, nil, map[string]any{"candidates": []string{pbBranch}})
		if got := familiesOf(t, found); len(got) != 1 || got[0].Name != pbBranch || got[0].Family != pbParent {
			t.Errorf("families = %v", got)
		}
	})
	t.Run("a_branch_whose_converted_name_is_not_the_identifier_is_not_a_family", func(t *testing.T) {
		t.Parallel()
		found := chromeAsk(t, "families", map[string]snapshotBranch{"feature/12-elsewhere": {zeros, familyFiles(pbParent)}}, nil,
			map[string]any{"candidates": []string{"feature/12-elsewhere"}})
		if got := familiesOf(t, found); len(got) != 0 {
			t.Errorf("families = %v", got)
		}
	})
}

func TestTheChromeBoardAndPlanWriteOnlyTheBranch(t *testing.T) {
	t.Parallel()
	board := chromeAsk(t, "board", nil, nil, nil)
	if _, bad := board["error"]; bad {
		t.Fatal(board)
	}
	if board["ident"] != pbParent {
		t.Errorf("ident = %v", board["ident"])
	}
	var ids []string
	for _, e := range board["batch"].([]any) {
		ids = append(ids, e.(map[string]any)["ticket"].(string))
	}
	if !slices.Equal(ids, []string{pbChild}) {
		t.Fatalf("batch = %v", ids)
	}
	body := chromeAsk(t, "plan", nil, nil, map[string]any{"ids": ids, "actor": map[string]any{"account": "a"}})
	if _, bad := body["error"]; bad {
		t.Fatal(body)
	}
	changes := body["changes"].(map[string]any)
	if len(changes) != 1 || changes[pbBranch] == nil {
		t.Fatalf("changes = %v", changes)
	}
	found := false
	for _, r := range changes[pbBranch].([]any) {
		found = found || r.(map[string]any)["path"] == ".ccnavi/approved/doing/"+pbChild+".md"
	}
	if !found {
		t.Error("子の承認済みチケットを書かない")
	}
}

func TestTwoBranchesWithOneIdentifierAreNotJudged(t *testing.T) {
	t.Parallel()
	rival := map[string]string{".ccnavi/approved/doing/" + pbParent + ".md": approvedText(samples.ParentText(pbParent, samples.Plan("research"), samples.ParentOpts{Allow: []string{"src/*"}}))}
	branches := map[string]snapshotBranch{pbBranch: {zeros, familyFiles(pbParent)}, pbParent: {zeros, rival}}
	found := chromeAsk(t, "families", branches, nil, map[string]any{"candidates": []string{pbBranch, pbParent}})
	for _, f := range familiesOf(t, found) {
		ticketkit.Has(t, f.Conflict, "1 本ではありません")
	}
	board := chromeAsk(t, "board", branches, []string{}, nil)
	undecided, _ := board["undecided"].(string)
	ticketkit.Has(t, undecided, "1 本ではありません")
	plan := chromeAsk(t, "plan", branches, []string{}, map[string]any{"ids": []string{pbChild}})
	msg, _ := plan["error"].(string)
	ticketkit.Has(t, msg, "1 本ではありません")
}

func TestAPredecessorFamilyWithTwoBranchesIsNotJudged(t *testing.T) {
	t.Parallel()
	other := "feature-20-dep"
	dep := familyFiles(other)
	branches := map[string]snapshotBranch{
		pbBranch:         {zeros, familyFiles(pbParent, other+"-01-01")},
		"feature/20-dep": {zeros, dep},
		other:            {zeros, dep},
	}
	closure := chromeAsk(t, "closure", branches, nil, nil)
	if amb, _ := json.Marshal(closure["ambiguous"]); string(amb) != `["`+other+`"]` {
		t.Errorf("ambiguous = %s", amb)
	}
	board := chromeAsk(t, "board", branches, nil, nil)
	undecided, _ := board["undecided"].(string)
	ticketkit.Has(t, undecided, "先行の親子のチケット（feature-20-dep）になるブランチが 1 本ではありません")
}

func TestTheClosureAsksForTheIdentifierBranch(t *testing.T) {
	t.Parallel()
	closure := chromeAsk(t, "closure", nil, []string{}, nil)
	idents, _ := json.Marshal(closure["idents"])
	if string(idents) != `{"`+pbBranch+`":"`+pbParent+`"}` {
		t.Errorf("idents = %s", idents)
	}
	if rivals, _ := json.Marshal(closure["rivals"]); string(rivals) != `["`+pbParent+`"]` {
		t.Errorf("rivals = %s", rivals)
	}
	need, _ := json.Marshal(closure["need"])
	ticketkit.Has(t, string(need), pbParent)
}
