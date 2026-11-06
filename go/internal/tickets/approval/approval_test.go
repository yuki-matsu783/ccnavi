package approval

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

func parse(t *testing.T, text string) *ticket.Ticket {
	t.Helper()
	got, problems := ticket.Parse(text)
	if got == nil {
		t.Fatalf("読めない: %v", problems)
	}
	return got
}

// scopeParent は範囲だけを変えた親（tests/ticket/test_scope_revision.py の見本）。
func scopeParent(t *testing.T, section string, globs ...string) *ticket.Ticket {
	lines := []string{"---", "version: 1", "ticket: login", "title: ログイン", "plan: [research]"}
	if len(globs) > 0 {
		lines = append(lines, section+":")
		for _, g := range globs {
			lines = append(lines, `- {glob: "`+g+`", match: "Write|Edit"}`)
		}
	}
	return parse(t, strings.Join(append(lines, "---", "", "本文", ""), "\n"))
}

func TestPlanDiffers(t *testing.T) {
	entry := func(e string) *ticket.Ticket {
		return parse(t, "---\nversion: 1\nticket: login\ntitle: t\nplan: [research]\nallow:\n- {"+e+`, match: "Write|Edit"}`+"\n---\n\nb\n")
	}
	planned := func(plan string) *ticket.Ticket {
		return parse(t, parentText("i0001", plan))
	}
	cases := []struct {
		name     string
		a, b     *ticket.Ticket
		differ   bool
		samePlan bool
	}{
		{"範囲だけの違いも改版", scopeParent(t, "allow", "src/*", "docs/*"), scopeParent(t, "allow", "src/*"), true, true},
		{"別のタイプの項は違い", scopeParent(t, "ask", "src/*"), scopeParent(t, "allow", "src/*"), true, true},
		{"項の並び順は違いでない", scopeParent(t, "allow", "docs/*", "src/*"), scopeParent(t, "allow", "src/*", "docs/*"), false, true},
		{"重複は違いでない", scopeParent(t, "allow", "src/*", "src/*"), scopeParent(t, "allow", "src/*"), false, true},
		{"glob と regex は同じ文字列でも違う", entry(`regex: "src/app"`), entry(`glob: "src/app"`), true, true},
		{"古い前置に似た glob も regex と違う", entry(`regex: "src/app"`), entry(`glob: "regex:src/app"`), true, true},
		{"同じ計画", planned("  - research\n  - {type: design, after: [1]}"), planned("  - research\n  - {type: design, after: [1]}"), false, true},
		{"定義の違い", planned("  - research"), planned("  - design"), true, false},
	}
	for _, c := range cases {
		if got := PlanDiffers(c.a, c.b); got != c.differ {
			t.Errorf("%s: PlanDiffers = %v", c.name, got)
		}
		if got := SamePlan(c.a, c.b, phases.Plan); got != c.samePlan {
			t.Errorf("%s: SamePlan = %v", c.name, got)
		}
	}
	// 推移的に同じ先行だけの違いは計画の違いにしない（2 番は 1 番を待ち、3 番は 1・2 番を待つ）。
	a := planned("  - research\n  - {type: research, after: [1]}\n  - {type: design, after: [1, 2]}")
	b := planned("  - research\n  - {type: research, after: [1]}\n  - {type: design, after: [2]}")
	if !SamePlan(a, b, phases.Plan) || PlanDiffers(a, b) {
		t.Error("推移的に同じ先行を違いにした")
	}
}

func TestSamePhasesReadsTheDefinitions(t *testing.T) {
	base := parse(t, parentText("i0001", "  - research"))
	reordered := parse(t, strings.Replace(parentText("i0001", "  - research"),
		"    kind: work\n    title: 調査\n", "    title: 調査\n    kind: work\n", 1))
	changed := parse(t, strings.Replace(parentText("i0001", "  - research"), "title: 調査", "title: 下調べ", 1))
	if !SamePhases(base, reordered) {
		t.Error("キーの順の違いを違いにした")
	}
	if SamePhases(base, changed) || !PlanDiffers(changed, base) {
		t.Error("定義の違いを見落とした")
	}
	if SamePhases(base, scopeParent(t, "allow", "src/*")) {
		t.Error("コピーの有無を同じとした")
	}
}

func TestAuthoritative(t *testing.T) {
	tk := func(tr, project string) *ticket.Ticket {
		return &ticket.Ticket{ID: "i0001", Tree: tr, State: place.Doing, Project: project, Path: "/" + tr}
	}
	alone := []*ticket.Ticket{tk("", "")}
	if got := authoritative(alone, alone); len(got) != 1 || got[0].Tree != "" {
		t.Errorf("親のツリーが無ければ全部残す: %v", got)
	}
	mixed := []*ticket.Ticket{tk("", ""), tk("p1", "p1")}
	if got := authoritative(mixed, mixed); len(got) != 2 {
		t.Errorf("リポジトリをまたぐ衝突はまとめない: %v", got)
	}
	home := []*ticket.Ticket{tk("", ""), tk("i0001", "")}
	if got := authoritative(home, home); len(got) != 1 || got[0].Tree != "i0001" {
		t.Errorf("親のツリーの側だけを残す: %v", got)
	}
}

func TestMoveFile(t *testing.T) {
	const text = "---\nticket: i0001\n---\nbody\n"
	dir := t.TempDir()
	source := put(t, filepath.Join(dir, "doing", "i0001.md"), text)
	target := filepath.Join(dir, "done", "i0001.md")
	if failed := MoveFile(source, target); failed != "" {
		t.Fatal(failed)
	}
	if data, _ := os.ReadFile(target); string(data) != text {
		t.Errorf("中身が変わった: %q", data)
	}
	if _, err := os.Stat(source); err == nil {
		t.Error("元が残った")
	}
	// 行き先に同じ名前が在れば動かさず、どちらにも触らない。
	put(t, source, text)
	put(t, target, "older\n")
	if failed := MoveFile(source, target); !strings.Contains(failed, "移動先に既にある") {
		t.Errorf("断らない: %q", failed)
	}
	if data, _ := os.ReadFile(target); string(data) != "older\n" {
		t.Errorf("行き先を書き換えた: %q", data)
	}
	if data, _ := os.ReadFile(source); string(data) != text {
		t.Errorf("元を書き換えた: %q", data)
	}
}

func TestAdmitKeepsTheBytesAndRefusesAChangedProposal(t *testing.T) {
	dir := t.TempDir()
	text := "---\r\nversion: 1\r\nticket: i0003\r\ntitle: t\r\nallow:\r\n- {glob: \"src/*\", match: \"Write\"}\r\n---\r\n本文\r\n"
	proposal := put(t, filepath.Join(dir, "todo", "i0003.md"), text)
	approved := filepath.Join(dir, "approved")
	tk, problems := ticket.Load(proposal)
	if tk == nil {
		t.Fatal(problems)
	}
	put(t, proposal, text+"x")
	if failed := Admit(approved, tk); !strings.Contains(failed, "提案の内容が更新されました") {
		t.Fatalf("差し替えを見落とした: %q", failed)
	}
	put(t, proposal, text)
	if failed := Admit(approved, tk); failed != "" {
		t.Fatal(failed)
	}
	if data, _ := os.ReadFile(CopyPath(approved, "i0003")); string(data) != text {
		t.Errorf("バイト列が変わった: %q", data)
	}
	if _, err := os.Stat(proposal); err == nil {
		t.Error("提案が残った")
	}
}

func TestPlaceAndMisplacedHome(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{Tickets: config.DefaultTickets}
	if got := PlaceProblems(root, conf, &ticket.Ticket{ID: "i0003", TreeRoot: root}); got != nil {
		t.Errorf("ワークスペースルートの提案を止めた: %v", got)
	}
	text := MisplacedHomeText(root, "i0001", "skill-proposals")
	for _, want := range []string{
		"親 i0001 の承認済みチケットが親のワークツリー", "skill-proposals に在る", "ccnavi-worktree.sh i0001",
		"承認を取り直してもらう", "手で動かさない", "ccnavi-user-push-approved.sh と ccnavi-sync.sh",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("%q が無い: %s", want, text)
		}
	}
}

func TestPlaceProblemsInADifferentlyNamedWorktree(t *testing.T) {
	root := workspace(t, "skill-proposals")
	conf := settingsOf(t, root)
	stray := worktree(root, "skill-proposals")
	parent := &ticket.Ticket{ID: "i0003", TreeRoot: stray}
	got := PlaceProblems(root, conf, parent)
	if len(got) != 1 || !strings.Contains(got[0].Detail, "提案 i0003 を識別子と違う名前のワークツリー skill-proposals") ||
		!strings.Contains(got[0].Detail, "承認の前に直す") {
		t.Errorf("親: %v", got)
	}
	child := &ticket.Ticket{ID: "i0001-01-01", Parent: "i0001", TreeRoot: stray}
	if got := PlaceProblems(root, conf, child); len(got) != 1 || !strings.Contains(got[0].Detail, "子の提案 i0001-01-01") {
		t.Errorf("子: %v", got)
	}
	if got := HomeProblem(root, conf, child); !strings.Contains(got, "skill-proposals に在る") {
		t.Errorf("承認済み: %q", got)
	}
}

func TestBranchProblems(t *testing.T) {
	mine := &ticket.Ticket{ID: "web-1-x"}
	others := []*ticket.Ticket{
		{ID: "Web-1-X"}, {ID: "WEB-1-x", Project: "p"}, {ID: "web-1-x"}, {ID: "web-1-x-01-01", Parent: "web-1-x"},
	}
	got := BranchProblems(mine, others)
	if len(got) != 1 || !strings.Contains(got[0].Detail, "（Web-1-X）") {
		t.Errorf("%v", got)
	}
	if BranchProblems(&ticket.Ticket{ID: "web-1-x-01-01", Parent: "web-1-x"}, others) != nil {
		t.Error("子を見た")
	}
}

func TestApprovedTimes(t *testing.T) {
	root := workspace(t, "i0001")
	conf := settingsOf(t, root)
	home := worktree(root, "i0001")
	doing := filepath.Join(home, ".ccnavi", "approved", "doing")
	put(t, filepath.Join(home, "wip", "proposals", "todo", "i0001-01-01.md"), childText("i0001-01-01", "i0001", "1", "wip/*"))
	runGit(t, home, "add", "-A")
	runGit(t, home, "commit", "-q", "-m", "proposal")
	if err := os.MkdirAll(doing, 0o755); err != nil {
		t.Fatal(err)
	}
	// GitHub の画面での移動は rename のコミットになる。--no-renames で足したことにする。
	runGit(t, home, "mv", filepath.Join("wip", "proposals", "todo", "i0001-01-01.md"), filepath.Join(".ccnavi", "approved", "doing", "i0001-01-01.md"))
	runGit(t, home, "commit", "-q", "-m", "Rename")
	when := strings.TrimSpace(runGit(t, home, "log", "-1", "--format=%cI"))
	put(t, filepath.Join(doing, "i0001.md"), parentText("i0001", "  - research"))
	found, _ := ScanAll(conf, root, false)
	times := ApprovedTimes(conf, found)
	for _, tk := range found {
		got := times[tk.Path]
		switch tk.ID {
		case "i0001-01-01":
			if got != (ApprovedTime{when, FromCommit}) {
				t.Errorf("コミットの時刻: %v", got)
			}
		case "i0001":
			if got != (ApprovedTime{"", Uncommitted}) || got.Label() != "未コミット（手で置いた）" {
				t.Errorf("未コミット: %v", got)
			}
		}
	}
	if found, ok := addedTimes(conf, t.TempDir()); ok || len(found) != 0 {
		t.Errorf("リポジトリでないツリーで時刻を引いた: %v", found)
	}
}

func TestPredecessorLabels(t *testing.T) {
	if got := (Predecessor{"x", PredScattered, "a:doing, b:doing"}).Label(); got != "複数の場所にある: a:doing, b:doing" {
		t.Error(got)
	}
	if !(Predecessor{State: PredReview}).Waiting() || (Predecessor{State: PredCancelled}).Waiting() || !(Predecessor{State: PredDone}).Met() {
		t.Error("待てば満たす状態の見分けが違う")
	}
	// 識別子が対応表に無い表（nil）でも引ける。
	child := &ticket.Ticket{ID: "p-01-01", Parent: "p", Phase: 1, Predecessors: []string{"q-01-01"}}
	if got := UnmetPredecessors(child, nil); len(got) != 1 || got[0].State != PredMissing {
		t.Errorf("%v", got)
	}
}
