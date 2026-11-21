package life

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 親を閉じたときのワークツリーとブランチの片付け（`worktree retire`・`sync`）。判断の中身（退避の後の片付け・取り消した子・
// 確かめられない状態・大文字小文字・壊れた記録・記録の列挙・放棄・MR のマージの後の片付け）は本体の lifecycle/retire のテストが見る。
// ここで見るのは、ready・worktree retire・sync を起こす通し。

// closedAndPushed は、親を閉じて ready を済ませ、退避の削除をコミットして push した木（Cached）。
func closedAndPushed(t *testing.T) (*ticketkit.Tree, ticketkit.Fixture) {
	t.Helper()
	tr := ticketkit.Cached(t, "closed-and-pushed", ticketkit.Options{Phases: samples.Phases}, func(tr *ticketkit.Tree) {
		buildClosable(tr)
		ticketkit.OK(tr.T, ready(tr, tr.Fixture()))
		tr.CommitParent("退避")
		tr.Parent("push", "--quiet", "origin", "i0001")
	})
	return tr, tr.Fixture()
}

func retire(tr *ticketkit.Tree, words ...string) run.Result {
	tr.T.Helper()
	if len(words) == 0 {
		words = []string{"retire", "i0001"}
	}
	return tr.Ccnavi(append([]string{"--cwd", tr.Root, "worktree"}, words...)...)
}

func branches(tr *ticketkit.Tree) []string {
	tr.T.Helper()
	return strings.Fields(tr.Git(tr.Root, "branch", "--format=%(refname:short)"))
}

func retireRecord(tr *ticketkit.Tree) string { return filepath.Join(tr.State, "retire", "i0001.json") }

func TestReadyNotesTheChildrenAndTheMRBeforeTheTicketsMoveToTheArchive(t *testing.T) {
	t.Parallel()
	tr, _ := closedAndPushed(t)
	var held struct {
		Branch   string  `json:"branch"`
		MR       float64 `json:"mr"`
		Children []struct {
			Ticket    string `json:"ticket"`
			Kind      string `json:"kind"`
			MergedSHA string `json:"merged_sha"`
		} `json:"children"`
	}
	jsonOf(t, ticketkit.Read(t, retireRecord(tr)), &held)
	merged := strings.TrimSpace(tr.Parent("rev-parse", childName))
	if held.Branch != "i0001" || held.MR != 7 {
		t.Errorf("記録 = %+v", held)
	}
	if len(held.Children) != 1 || held.Children[0].Ticket != childName || held.Children[0].Kind != "merged" || held.Children[0].MergedSHA != merged {
		t.Errorf("子の記録 = %+v（%s が merged のはず）", held.Children, merged)
	}
}

func TestRetireRemovesTheWorktreesAndTheMergedChildBranch(t *testing.T) {
	t.Parallel()
	tr, _ := closedAndPushed(t)
	ticketkit.Write(t, filepath.Join(tr.Root, ".gitignore"), "/scratchpad\n")
	child := tr.ChildTree(childName)
	exclude := strings.TrimSpace(tr.Git(child, "rev-parse", "--git-path", "info/exclude"))
	if !filepath.IsAbs(exclude) {
		exclude = filepath.Join(child, exclude)
	}
	ticketkit.Write(t, exclude, ".env\n")
	ticketkit.Write(t, filepath.Join(child, ".env"), "SECRET=1\n")
	done := ticketkit.OK(t, retire(tr))
	for _, w := range []string{"ワークツリー " + childName + " を削除しました", "ブランチ " + childName + " を削除しました", "ワークツリー i0001 を削除しました", "git が無視しているファイルを"} {
		ticketkit.Has(t, done.Stdout, w)
	}
	if ticketkit.Exists(child) || ticketkit.Exists(tr.ParentTree) {
		t.Error("ワークツリーが残った")
	}
	// 無視されたファイルは消えずに退避される。
	if !existsIn(tr.Root, "scratchpad/worktrees/"+childName+"/.env") {
		t.Error("無視されたファイルが退避されていない")
	}
	// 子のブランチは消え、親のブランチは MR のマージまで残る。
	if got := branches(tr); !slices.Equal(got, []string{"i0001", "main"}) {
		t.Errorf("ブランチ = %v", got)
	}
	// 打ち直しても何も起きない。
	again := ticketkit.OK(t, retire(tr))
	if n := strings.Count(again.Stdout, "削除しました"); n != 0 {
		t.Errorf("打ち直しで %d 件消した", n)
	}
}

// MR がマージされたと分かった後の片付け（親のブランチと片付けの記録）。先にワークツリーを消し、あとでブランチを消す。
func TestTidyAfterMergeRemovesTheParentBranchTheWorktreeAndTheNote(t *testing.T) {
	t.Parallel()
	tr, _ := closedAndPushed(t)
	sha := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "refs/remotes/origin/i0001"))
	done := ticketkit.OK(t, tr.Ccnavi("--cwd", tr.Root, "sync", "tidy", "i0001", "self", "i0001", sha))
	ticketkit.Has(t, done.Stdout, "ブランチ "+childName+" を削除しました")
	lines := ticketkit.Lines(done.Stdout)
	if lines[len(lines)-1] != "record yes" {
		t.Errorf("最後の行 = %q", lines[len(lines)-1])
	}
	if got := branches(tr); !slices.Equal(got, []string{"main"}) {
		t.Errorf("ブランチ = %v", got)
	}
	if ticketkit.Exists(retireRecord(tr)) {
		t.Error("片付けの記録が残った")
	}
	worktree, branch := -1, -1
	for i, l := range lines {
		if strings.Contains(l, "ワークツリー i0001 を削除") {
			worktree = i
		}
		if strings.Contains(l, "ブランチ i0001 を削除") {
			branch = i
		}
	}
	if worktree < 0 || branch < 0 || worktree >= branch {
		t.Errorf("削除の順（ワークツリー %d・ブランチ %d）", worktree, branch)
	}
}

// ready が書く片付けの記録（retire/<親>.json）。
func TestABrokenRecordIsNotOverwrittenAndReadyStops(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.Write(t, retireRecord(tr), "{broken")
	done := ready(tr, f)
	if done.Code != 1 {
		t.Fatalf("code %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	ticketkit.Has(t, done.Stderr, "上書きしない")
	if got := ticketkit.Read(t, retireRecord(tr)); got != "{broken" {
		t.Errorf("記録を上書きした: %q", got)
	}
	// 退避に進んでいないので、親はワークツリーに残り、直せば打ち直せる。
	if !ticketkit.Exists(tr.ApprovedPath("done", "i0001")) {
		t.Error("退避に進んだ")
	}
	if err := os.Remove(retireRecord(tr)); err != nil {
		t.Fatal(err)
	}
	ticketkit.OK(t, ready(tr, f))
	if mr := ticketkit.ReadJSON(t, retireRecord(tr))["mr"]; mr != float64(7) {
		t.Errorf("mr = %v", mr)
	}
}

func TestAnUnreadableClosedTicketStopsReady(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.Write(t, filepath.Join(tr.Approved, "done", "i0001-01-77.md"), "---\nticket: [x\n---\n")
	tr.CommitParent("読めない閉じたチケット")
	tr.Parent("push", "--quiet", "origin", "i0001")
	done := ready(tr, f)
	if done.Code != 1 {
		t.Fatalf("code %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	ticketkit.Has(t, done.Stderr, "読めない閉じたチケットがあり")
	if ticketkit.Exists(retireRecord(tr)) {
		t.Error("記録を書いた")
	}
}

// `ccnavi sync retired`（片付けの列挙元）の行の形。記録の読み方は本体の見本が見る。
func TestSyncRetiredLines(t *testing.T) {
	t.Parallel()
	tr, _ := closedAndPushed(t)
	retired := func(flags ...string) []string {
		t.Helper()
		done := ticketkit.OK(t, tr.Ccnavi(append(flags, "sync", "retired")...))
		lines := ticketkit.Lines(done.Stdout)
		if lines[0] != "retired 1" {
			t.Fatalf("1 行目 = %q", lines[0])
		}
		return lines[1:]
	}
	if got := retired(); !slices.Equal(got, []string{"retired i0001 self 7 i0001"}) {
		t.Fatalf("行 = %v", got)
	}
	// チケット制御を切っても動く。
	if got := retired("--ticket-control", "disable"); !slices.Equal(got, []string{"retired i0001 self 7 i0001"}) {
		t.Errorf("制御を切った行 = %v", got)
	}
	put := func(name, text string) { ticketkit.Write(t, filepath.Join(tr.State, "retire", name), text) }
	put("pj0001.json", `{"parent": "pj0001", "project": "app", "branch": "pj0001", "mr": 3}`)
	put("i0002.json", `{"parent": "i0002", "project": "", "mr": 5}`)
	put("i0003.json", `{"parent": "i0003", "project": "", "branch": "i0003"}`)
	put("i0004.json", "{broken")
	put("i0005.json", `{"parent": "i0009", "project": "", "mr": 5}`)
	put("i0006.json.tmp.1", `{"parent": "i0006", "mr": 5}`)
	put("note.txt", "x")
	lines := retired()
	var ok, incomplete []string
	for _, l := range lines {
		switch {
		case strings.HasPrefix(l, "retired"):
			ok = append(ok, l)
		case strings.HasPrefix(l, "incomplete"):
			incomplete = append(incomplete, l)
		}
	}
	slices.Sort(ok)
	slices.Sort(incomplete)
	if want := []string{"retired i0001 self 7 i0001", "retired i0002 self 5 -", "retired pj0001 app 3 pj0001"}; !slices.Equal(ok, want) {
		t.Errorf("retired = %v", ok)
	}
	if len(incomplete) != 2 || !strings.HasPrefix(incomplete[0], "incomplete i0003 ") || !strings.HasPrefix(incomplete[1], "incomplete i0004 ") {
		t.Errorf("incomplete = %v", incomplete)
	}
	if len(lines) != 5 {
		t.Errorf("行数 = %d: %v", len(lines), lines)
	}
}

// `ccnavi worktree retire --abandon <親>`: ワークツリーが消えてから、記録だけを消す。
func TestAbandonRemovesTheRecordOnlyOnceTheWorktreeIsGone(t *testing.T) {
	t.Parallel()
	tr, _ := closedAndPushed(t)
	abandon := func(name string) run.Result { return retire(tr, "retire", "--abandon", name) }
	refused := abandon("i0001")
	if refused.Code != 1 {
		t.Fatalf("code %d\n%s%s", refused.Code, refused.Stdout, refused.Stderr)
	}
	ticketkit.Has(t, refused.Stderr, "ワークツリー")
	if !ticketkit.Exists(retireRecord(tr)) {
		t.Fatal("記録が消えた")
	}
	ticketkit.OK(t, retire(tr))
	if !ticketkit.Exists(retireRecord(tr)) {
		t.Fatal("retire が記録を消した")
	}
	ticketkit.OK(t, abandon("i0001"))
	if ticketkit.Exists(retireRecord(tr)) {
		t.Error("記録が残った")
	}
	// ブランチには触らない。
	if !slices.Contains(branches(tr), "i0001") {
		t.Error("親のブランチを消した")
	}
	again := abandon("i0001")
	if again.Code != 1 {
		t.Errorf("code %d", again.Code)
	}
	ticketkit.Has(t, again.Stderr, "記録はありません")
	// 悪い名前と、retire 以外に付けた --abandon は断る。
	if r := abandon("../x"); r.Code != 1 {
		t.Errorf("悪い名前: code %d", r.Code)
	}
	if r := retire(tr, "drop", "--abandon", "i0001"); r.Code != 1 {
		t.Errorf("drop --abandon: code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}
