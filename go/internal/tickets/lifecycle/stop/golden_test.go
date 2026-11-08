package stop

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/testkit"
)

// ワークスペースを作り、cwd ごとに促す相手・促しの文・1 回だけの記録（書いたバイト列も）・基準点の警告が、
// 記録した答え（testdata/stop.json）と 1 字も違わないことを見る。

// noPlanParent は計画の無い親。
func noPlanParent(id string, extra ...string) string {
	return "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n" + strings.Join(extra, "\n") + "\n" +
		"human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n---\n\n本文\n"
}

// stopWorkspace の形。
//
//	i0001 設計のフェーズで子 01-01 が作業中（子のワークツリーにコミットがある）
//	i0002 子が 2 つとも閉じた。01-01 は親へ合流済み、01-02 は合流していない
//	i0003 子は合流済みだが、合流の後に子のブランチへ修正を積んだ
//	i0004 依頼済み（requested）
//	i0005 計画が済んでレビューも済み、フィードバックの改版を提案済み
//	i0006 i0005 と同じで、改版はまだ
//	i0007 計画の無い親。基準点より先にコミットがある
//	i0008 子の基準点がワークツリーの HEAD の祖先でない
//	i0009 子は合流済みで、依頼だけが残る
func stopWorkspace(t *testing.T) string {
	root := clock.Workspace(t, "i0001", "i0002", "i0003", "i0004", "i0005", "i0006", "i0007", "i0008", "i0009")
	base := strings.TrimSpace(clock.Git(t, root, "rev-parse", "HEAD"))
	baseLine := "base_sha: " + base
	w := func(name string) string { return testkit.Worktree(root, name) }
	write := func(parent, state, id, text string) {
		testkit.Put(t, filepath.Join(approvedDir(w(parent)), state, id+".md"), text)
	}
	commit := func(dir, file string) string {
		testkit.Put(t, filepath.Join(dir, "src", file), file+"\n")
		commitAll(t, dir, file)
		return strings.TrimSpace(clock.Git(t, dir, "rev-parse", "HEAD"))
	}
	design := "  - design"

	write("i0001", "doing", "i0001", testkit.ClosingParent("i0001", []string{testkit.ClosingStarted, baseLine}, design))
	write("i0001", "doing", "i0001-01-01", testkit.ClosingChild("i0001-01-01", "i0001", "1", testkit.ClosingStarted, baseLine))
	commit(testkit.AddWorktree(t, root, "i0001-01-01", "i0001"), "a")

	write("i0002", "doing", "i0002", testkit.ClosingParent("i0002", []string{testkit.ClosingStarted, baseLine}, design))
	merged := commit(testkit.AddWorktree(t, root, "i0002-01-01", "i0002"), "b")
	clock.Git(t, w("i0002"), "merge", "-q", "--ff-only", "i0002-01-01")
	write("i0002", "done", "i0002-01-01", testkit.ClosingChild("i0002-01-01", "i0002", "1", testkit.ClosingStarted, testkit.ClosingDone, "merged_sha: "+merged))
	write("i0002", "done", "i0002-01-02", testkit.ClosingChild("i0002-01-02", "i0002", "1", testkit.ClosingStarted, testkit.ClosingDone))

	write("i0003", "doing", "i0003", testkit.ClosingParent("i0003", []string{testkit.ClosingStarted, baseLine}, design))
	child3 := testkit.AddWorktree(t, root, "i0003-01-01", "i0003")
	merged = commit(child3, "c")
	clock.Git(t, w("i0003"), "merge", "-q", "--ff-only", "i0003-01-01")
	commit(child3, "late")
	write("i0003", "done", "i0003-01-01", testkit.ClosingChild("i0003-01-01", "i0003", "1", testkit.ClosingStarted, testkit.ClosingDone, "merged_sha: "+merged))

	write("i0004", "doing", "i0004", testkit.ClosingParent("i0004", []string{testkit.ClosingStarted, baseLine}, design))
	write("i0004", "done", "i0004-01-01", testkit.ClosingChild("i0004-01-01", "i0004", "1", testkit.ClosingStarted, testkit.ClosingDone))
	testkit.Put(t, filepath.Join(approvedDir(w("i0004")), "phases", "i0004", "1.requested"), `{"at": "2026-01-02T00:00:00+0900"}`)

	for _, id := range []string{"i0005", "i0006"} {
		write(id, "doing", id, testkit.ClosingParent(id, []string{testkit.ClosingStarted, baseLine}, design))
		write(id, "done", id+"-01-01", testkit.ClosingChild(id+"-01-01", id, "1", testkit.ClosingStarted, testkit.ClosingDone))
		testkit.Put(t, filepath.Join(approvedDir(w(id)), "phases", id, "1.reviewed"), `{"at": "2026-01-02T00:00:00+0900"}`)
	}
	testkit.Put(t, filepath.Join(w("i0005"), "wip", "proposals", "todo", "i0005.md"),
		testkit.ClosingParent("i0005", []string{"feedback: []"}, design))

	write("i0007", "doing", "i0007", noPlanParent("i0007", testkit.ClosingStarted, baseLine))
	commit(w("i0007"), "d")
	commit(w("i0007"), "e")

	write("i0008", "doing", "i0008", noPlanParent("i0008", testkit.ClosingStarted, baseLine))
	write("i0008", "doing", "i0008-01-01", testkit.ClosingChild("i0008-01-01", "i0008", "1", testkit.ClosingStarted,
		"base_sha: 0123456789abcdef0123456789abcdef01234567"))
	testkit.AddWorktree(t, root, "i0008-01-01", "i0008")

	write("i0009", "doing", "i0009", testkit.ClosingParent("i0009", []string{testkit.ClosingStarted, baseLine}, design))
	merged = commit(testkit.AddWorktree(t, root, "i0009-01-01", "i0009"), "f")
	clock.Git(t, w("i0009"), "merge", "-q", "--ff-only", "i0009-01-01")
	write("i0009", "done", "i0009-01-01", testkit.ClosingChild("i0009-01-01", "i0009", "1", testkit.ClosingStarted, testkit.ClosingDone, "merged_sha: "+merged))
	return root
}

func cwds(root string) []string {
	out := []string{root, filepath.Join(root, "elsewhere")}
	for _, name := range []string{"i0001", "i0001-01-01", "i0002", "i0003", "i0004", "i0005", "i0006", "i0007", "i0008-01-01", "i0009"} {
		out = append(out, testkit.Worktree(root, name))
	}
	return out
}

func TestMatchesGolden(t *testing.T) {
	goRoot := stopWorkspace(t)
	goState := t.TempDir()
	want := golden.Load[map[string]any](t, "stop")
	conf := testkit.Settings(t, goRoot)
	rel := func(s string) string { return strings.ReplaceAll(s, goRoot, "<root>") }
	record := func(n Nudge) []any {
		before := NudgedBefore(goState, "s1", n)
		wrote := RememberNudge(goState, "s1", n)
		return []any{before, wrote, NudgedBefore(goState, "s1", n)}
	}
	var answers []any
	for _, cwd := range cwds(goRoot) {
		e := map[string]any{}
		if u := UnfinishedAtStop(conf, goRoot, cwd, nil); u != nil {
			e["unfinished"] = append([]any{u.Ticket.ID, rel(u.worktree), u.ahead, u.head, rel(FinishNudge(goRoot, u))}, record(u)...)
		}
		if p := PendingAtStop(conf, goRoot, cwd, nil); p != nil {
			children := p.Children
			if children == nil {
				children = []string{}
			}
			e["pending"] = append([]any{p.Parent.ID, p.Number, p.Step, rel(p.worktree), p.head, children, p.title,
				rel(RequestNudge(goRoot, p))}, record(p)...)
		}
		if w := WaitingAtStop(conf, goRoot, cwd, nil); w != nil {
			e["waiting"] = append([]any{w.Parent.ID, w.head, rel(w.todo), rel(w.proposal), rel(FeedbackNudge(goRoot, w))}, record(w)...)
		}
		answers = append(answers, e)
	}
	copies, _ := approval.Scan(conf, goRoot, false, nil)
	off := map[string]any{}
	for _, c := range copies {
		off[c.ID] = rel(BaseOffHead(conf, goRoot, c))
	}
	data, err := os.ReadFile(nudgePath(goState, "s1"))
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]any{
		"cwds": answers, "offHead": off, "record": base64.StdEncoding.EncodeToString(data),
		"path": []string{filepath.Base(nudgePath(goState, "")), filepath.Base(nudgePath(goState, "a/b"))},
	}
	testkit.SameJSON(t, got, want)
}
