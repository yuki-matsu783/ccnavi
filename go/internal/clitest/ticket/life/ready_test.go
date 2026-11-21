package life

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 閉じた親子のチケットの退避（`review ready` が `logs/archive/` へ移す）。
//   - ready が条件を確かめてから退避し、打ち直しても通ること
//   - write-and-push の見分け（`ccnavi write-and-push sort`）と実行後チェックが、退避の削除だけを ccnavi の書き込みとして外すこと
//   - ボードの JSON に退避のチケットが載ること（判定には混ぜない）
//
// 退避の構成と移し方、閉じた識別子の使い回し・子の連番が退避を見ることは本体の archive・approval・closeout のテストが見る。

// write-and-push の見分けの種類（ccnavi が書いたと内容で分かる・見分けられない）。
const (
	kindB = "b"
	kindD = "d"
)

func archiveSelf(tr *ticketkit.Tree) string { return filepath.Join(tr.Root, "logs", "archive", "self") }

func closedText(name, parent string) string {
	lines := []string{"---", "version: 1", "ticket: " + name}
	if parent == "" {
		lines = append(lines, "mr: 1", "issue: 1")
	} else {
		lines = append(lines, "parent: "+parent, "phase: 1")
	}
	lines = append(lines, "human_review:", "  required: false", "  reason: t", "title: "+name+" のタイトル", "rationale: r",
		"allow:", "  - match: Write|Edit", `    glob: "src/*"`,
		"ccnavi_approved:", "  approved_at: '2026-01-01T00:00:00+09:00'",
		`started_at: ""`, "completed_at: '2026-01-02T00:00:00+09:00'", `base_sha: ""`, "---", "", "本文", "")
	return strings.Join(lines, "\n")
}

// remoteDraft は origin（bare）を作って親のブランチを push し、Draft の結果ファイルを置く。
func remoteDraft(tr *ticketkit.Tree) ticketkit.Fixture {
	tr.T.Helper()
	bare := filepath.Join(tr.Root, "origin.git")
	tr.Git(tr.Root, "init", "--quiet", "--bare", bare)
	tr.Git(tr.Root, "remote", "add", "origin", bare)
	tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	f := tr.Fixture()
	ticketkit.Write(tr.T, f.Path, `{"host": "fixture", "mr": {"number": 7, "url": "u/7", "draft": true}}`)
	return f
}

// buildClosable は、親を閉じて片付け、push 済みにする（Draft のマージリクエストの結果ファイルは Root/fixture.json）。
func buildClosable(tr *ticketkit.Tree) {
	t := tr.T
	t.Helper()
	tr.PhaseFamily(samples.Plan("design"))
	tr.Propose(childName, samples.ChildText(childName, "i0001", 1, []string{"wip/design/*"}), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	f := remoteDraft(tr)
	tr.RunChild(childName, ticketkit.File{Path: "wip/design/plan.md", Text: "d\n"})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", childName))
	tr.CommitParent("close 01")
	tr.Merge(childName)
	tr.Parent("push", "--quiet", "origin", "i0001")
	ticketkit.OK(t, tr.Request(f))
	ticketkit.OK(t, tr.Confirm(f))
	tr.StartParent()
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("design"), samples.ParentOpts{Feedback: []samples.Step{}, Copy: tr.Phases}), "")
	ticketkit.OK(t, tr.Approve())
	closed := ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001"))
	ticketkit.Has(t, closed.Stdout, "logs/archive/")
	// 統合先にたまっていた過去の親（閉じた親子のチケット）。
	ticketkit.Write(t, filepath.Join(tr.Approved, "done", "old.md"), closedText("old", ""))
	ticketkit.Write(t, filepath.Join(tr.Approved, "done", "old-01-01.md"), closedText("old-01-01", "old"))
	ticketkit.Write(t, filepath.Join(tr.Approved, "phases", "old", "closed.json"), "{}\n")
	tr.CommitParent("状態の移動")
	tr.Parent("rm", "-r", "-q", "wip")
	tr.Parent("commit", "--quiet", "-m", "chore: wip を片付ける")
	tr.Parent("push", "--quiet", "origin", "i0001")
}

// closable は buildClosable を組んだ木（Cached）。t.Parallel() は呼ぶ前に済ませる。
func closable(t *testing.T) (*ticketkit.Tree, ticketkit.Fixture) {
	t.Helper()
	tr := ticketkit.Cached(t, "closable", ticketkit.Options{Phases: samples.Phases}, buildClosable)
	return tr, tr.Fixture()
}

func ready(tr *ticketkit.Tree, f ticketkit.Fixture) run.Result {
	tr.T.Helper()
	return tr.Ccnavi("--cwd", tr.ParentTree, "review", "ready", "--result", f.Path)
}

// sortedKinds は write-and-push の見分け（`ccnavi write-and-push sort`）。置き場の相対パス → 種類。
func sortedKinds(tr *ticketkit.Tree, since string) map[string]string {
	tr.T.Helper()
	args := []string{"write-and-push", "sort", "i0001"}
	if since != "" {
		args = append(args, since)
	}
	done := ticketkit.OK(tr.T, tr.Ccnavi(args...))
	kinds := map[string]string{}
	lines := ticketkit.Lines(done.Stdout)
	for _, line := range lines[1:] {
		kind, rel, _ := strings.Cut(line, " ")
		if kind != "why" {
			kinds[rel] = kind
		}
	}
	return kinds
}

func archiveRemove(tr *ticketkit.Tree, rel string) {
	tr.T.Helper()
	if err := os.Remove(filepath.Join(tr.Approved, filepath.FromSlash(rel))); err != nil {
		tr.T.Fatal(err)
	}
}

func boardOf(tr *ticketkit.Tree) map[string]any {
	tr.T.Helper()
	return ticketkit.ParseJSON(tr.T, tr.Ccnavi("--explain", "--json").Stdout)
}

func TestReadyMovesEveryClosedFamilyAndCanBeRunAgain(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	passed := ticketkit.OK(t, ready(tr, f))
	lines := ticketkit.Lines(passed.Stdout)
	ticketkit.Has(t, ticketkit.Read(t, lines[0]), "logs/archive/")
	if !strings.HasPrefix(lines[1], "tree ") || !run.SamePath(strings.TrimPrefix(lines[1], "tree "), tr.ParentTree) {
		t.Errorf("2 行目 = %q", lines[1])
	}
	arch := archiveSelf(tr)
	for _, rel := range []string{"done/i0001.md", "done/i0001-01-01.md", "done/old.md", "done/old-01-01.md"} {
		if !existsIn(arch, rel) {
			t.Errorf("退避に %s が無い", rel)
		}
		if existsIn(tr.Approved, rel) {
			t.Errorf("%s が置き場に残った", rel)
		}
	}
	// Draft を外したマーカーも一緒に退避される。
	if mr := ticketkit.ReadJSON(t, filepath.Join(arch, "phases", "i0001", "ready.json"))["mr"]; mr != float64(7) {
		t.Errorf("ready.json の mr = %v", mr)
	}
	if !existsIn(arch, "phases/old/closed.json") || existsIn(tr.Approved, "phases/i0001") {
		t.Error("マーカーの退避が合わない")
	}
	// git の上では削除だけ。
	status := ticketkit.Lines(tr.Parent("status", "--porcelain"))
	if len(status) == 0 {
		t.Fatal("変更が無い")
	}
	for _, line := range status {
		if !strings.HasPrefix(line, " D ") {
			t.Errorf("削除以外の変更: %q", line)
		}
	}
	// 削除をコミットして push してから打ち直しても通る（Draft を外し損ねたとき）。
	tr.CommitParent("退避")
	tr.Parent("push", "--quiet", "origin", "i0001")
	again := ticketkit.OK(t, ready(tr, f))
	if !strings.HasSuffix(ticketkit.Lines(again.Stdout)[0], "review-ready-i0001.md") {
		t.Errorf("打ち直しの 1 行目 = %q", ticketkit.Lines(again.Stdout)[0])
	}
	// ボードには退避のチケットとして載り、置き場のチケットには出ない。子のワークツリーは片付けてから見る
	// （残っていると、そこにコピーされた承認済みチケットが置き場の側に出る）。
	tr.Git(tr.Root, "worktree", "remove", "--force", tr.ChildTree(childName))
	b := boardOf(tr)
	archived := map[string]map[string]any{}
	for _, a := range b["archived"].([]any) {
		m := a.(map[string]any)
		archived[m["ticket"].(string)] = m
	}
	if len(archived) != 4 {
		t.Errorf("退避のチケット = %v", archived)
	}
	for _, name := range []string{"i0001", "i0001-01-01", "old", "old-01-01"} {
		if archived[name] == nil {
			t.Errorf("%s が退避の欄に無い", name)
		}
	}
	if p, _ := archived["i0001"]["path"].(string); !strings.HasPrefix(p, arch) {
		t.Errorf("退避の path = %q", p)
	}
	for _, x := range b["tickets"].([]any) {
		if x.(map[string]any)["ticket"] == "i0001" {
			t.Error("置き場のチケットに i0001 が残った")
		}
	}
}

// 追跡済みの rels を push 済みの状態にしてから書き換え、未コミットの変更にする。
func driftAfterPush(tr *ticketkit.Tree, rels ...string) {
	tr.T.Helper()
	for _, rel := range rels {
		writeIn(tr.T, tr.ParentTree, rel, "old\n")
	}
	tr.CommitParent("古いコピー")
	tr.Parent("push", "--quiet", "origin", "i0001")
	for _, rel := range rels {
		writeIn(tr.T, tr.ParentTree, rel, "new\n")
	}
}

// 親のワークツリーに未コミットの変更があると ready は止まる。ミラーの食い違いなら、直す人（ccnavi-user-mirror.sh）まで言う。
func TestReadyNamesTheUncommittedChangesAndBlamesTheMirrorOnlyWhenItIsTheMirror(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name  string
		rels  []string
		has   []string
		lacks []string
	}{
		{"mirror", []string{".ccnavi/config/common/rules.yml", "skills/process-test/SKILL.md"},
			[]string{"親のワークツリーに未コミットの変更がある", ".ccnavi/config/common/rules.yml", "skills/process-test/SKILL.md", "ccnavi-user-mirror.sh", "コミットも取り消しもしない"}, nil},
		{"other", []string{"src/keep.py"}, []string{"親のワークツリーに未コミットの変更がある"}, []string{"ccnavi-user-mirror.sh"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr, f := closable(t)
			driftAfterPush(tr, c.rels...)
			refused := ticketkit.Fails(t, ready(tr, f))
			for _, w := range c.has {
				ticketkit.Has(t, refused.Stderr, w)
			}
			for _, w := range c.lacks {
				ticketkit.Lacks(t, refused.Stderr, w)
			}
		})
	}
}

func TestReadyDoesNotMoveAnythingWhileTheParentIsNotClosable(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	tr.PhaseFamily(samples.Plan("design"))
	f := remoteDraft(tr)
	ticketkit.Fails(t, ready(tr, f))
	if ticketkit.Exists(filepath.Join(tr.Root, "logs", "archive")) {
		t.Error("logs/archive を作った")
	}
}

// 退避と関係の無い削除（開いた親の書きかけ）は、見分けで (d) のまま。退避したコピーが違えば (b) にしない。
func TestWriteAndPushCountsOnlyTheArchiveRemovals(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.Write(t, filepath.Join(tr.Approved, "stray", "stray.jsonl"), "{}\n")
	tr.CommitParent("stray")
	tr.Parent("push", "--quiet", "origin", "i0001")
	ticketkit.OK(t, ready(tr, f))
	archiveRemove(tr, "stray/stray.jsonl")
	got := sortedKinds(tr, "")
	for rel, want := range map[string]string{
		".ccnavi/approved/done/i0001.md":            kindB,
		".ccnavi/approved/done/old-01-01.md":        kindB,
		".ccnavi/approved/phases/i0001/closed.json": kindB,
		".ccnavi/approved/stray/stray.jsonl":        kindD,
	} {
		if got[rel] != want {
			t.Errorf("%s の種類 = %q（%q のはず）", rel, got[rel], want)
		}
	}
	// 退避したコピーが違えば (b) にしない。
	writeIn(t, archiveSelf(tr), "done/old-01-01.md", "書き換えた\n")
	again := sortedKinds(tr, "")
	if again[".ccnavi/approved/done/old-01-01.md"] != kindD || again[".ccnavi/approved/done/i0001.md"] != kindB {
		t.Errorf("書き換えた後の見分け = %v", again)
	}
}

func TestThePostCheckDoesNotReportTheArchiveRemovals(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	// 閉じていない親（done/ に無い）の子。退避の対象にならない。
	ticketkit.Write(t, filepath.Join(tr.Approved, "done", "zzz-01-01.md"), closedText("zzz-01-01", "zzz"))
	tr.CommitParent("stray")
	tr.Parent("push", "--quiet", "origin", "i0001")
	ticketkit.OK(t, ready(tr, f))
	if !existsIn(tr.Approved, "done/zzz-01-01.md") {
		t.Fatal("退避の対象でないチケットを動かした")
	}
	archiveRemove(tr, "done/zzz-01-01.md")
	r := tr.Hook("PostToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ls"})
	said := ticketkit.Out(r)
	// 退避と関係の無いチケットの削除は今までどおり報告する。
	ticketkit.Has(t, said, "done/zzz-01-01.md")
	for _, w := range []string{"done/i0001.md", "done/old-01-01.md", "phases/i0001/closed.json"} {
		ticketkit.Lacks(t, said, w)
	}
}

// ---- レビューの指摘の再現（直す前は失敗する）

// ready の後、閉じた子のワークツリーを片付ければ、古い作業中のコピーは残らない。
func TestAStaleDoingCopyInAChildTreeGoesWithTheChildWorktree(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.OK(t, ready(tr, f))
	tr.CommitParent("退避")
	child := tr.ChildTree(childName)
	if !existsIn(child, ".ccnavi/approved/doing/"+childName+".md") {
		t.Fatal("子のワークツリーに作業中のコピーが無い")
	}
	tr.Git(tr.Root, "worktree", "remove", "--force", child)
	for _, x := range boardOf(tr)["tickets"].([]any) {
		m := x.(map[string]any)
		status, _ := m["copy"].(map[string]any)["status"].(string)
		if (status == "open" || status == "review") && (m["ticket"] == childName || m["ticket"] == "i0001") {
			t.Errorf("%v が開いたまま", m["ticket"])
		}
	}
}

// 止まった退避は、次の ready が終わらせる。
func TestAStoppedMoveIsFinishedByTheNextReady(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	arch := archiveSelf(tr)
	writeIn(t, arch, "done/old-01-01.md", "違う中身\n")
	ticketkit.Fails(t, ready(tr, f))
	if err := os.Remove(filepath.Join(arch, "done", "old-01-01.md")); err != nil {
		t.Fatal(err)
	}
	ticketkit.OK(t, ready(tr, f))
	for _, rel := range []string{"done/i0001.md", "done/old.md", "done/old-01-01.md", "phases/old/closed.json"} {
		if existsIn(tr.Approved, rel) || !existsIn(arch, rel) {
			t.Errorf("%s が退避に移っていない", rel)
		}
	}
}

// コミットした削除も write-and-push が運ぶ。コミット後の未送信の確かめ（since）でも (b)。
func TestCommittedRemovalsAreCarriedByWriteAndPush(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.OK(t, ready(tr, f))
	kinds := sortedKinds(tr, "")
	if kinds[".ccnavi/approved/done/i0001.md"] != kindB || kinds[".ccnavi/approved/done/i0001-01-01.md"] != kindB {
		t.Errorf("見分け = %v", kinds)
	}
	tr.CommitParent("退避")
	kinds = sortedKinds(tr, strings.TrimSpace(tr.Parent("rev-parse", "HEAD~1")))
	if kinds[".ccnavi/approved/done/i0001.md"] != kindB {
		t.Errorf("コミット後の見分け = %v", kinds)
	}
}

// ready を経ない削除は運ばない。
func TestARemovalOutsideReadyIsNotCarried(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	tr.PhaseFamily(samples.Plan("design"))
	ticketkit.Write(t, filepath.Join(tr.Approved, "done", "old.md"), closedText("old", ""))
	tr.CommitParent("old")
	// 退避に同じ中身を置き、ready を経ずに消す。
	writeIn(t, archiveSelf(tr), "done/old.md", closedText("old", ""))
	archiveRemove(tr, "done/old.md")
	if got := sortedKinds(tr, "")[".ccnavi/approved/done/old.md"]; got == kindB {
		t.Errorf("ready を経ない削除を (b) にした")
	}
}

// logs がリンクなら、write-and-push は退避を信用しない。
func TestALinkedLogsDirIsNotTrustedByWriteAndPush(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("リンクを作れない機械")
	}
	tr, f := closable(t)
	ticketkit.OK(t, ready(tr, f))
	logs := filepath.Join(tr.Root, "logs")
	moved := filepath.Join(tr.Root, "elsewhere")
	if err := os.Rename(logs, moved); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(moved, logs); err != nil {
		t.Fatal(err)
	}
	for rel, kind := range sortedKinds(tr, "") {
		if kind == kindB {
			t.Errorf("%s を (b) にした", rel)
		}
	}
}

// 退避を済ませた後に、同じ中身のチケットがツリーに戻り（取り込みなど）、ready の外で消えても、済んだ ready の印は運ばない。
func TestAMarkOfAFinishedReadyDoesNotCarryALaterRemoval(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.OK(t, ready(tr, f))
	tr.CommitParent("退避")
	tr.Parent("checkout", "HEAD~1", "--", ".ccnavi/approved/done/old.md")
	tr.Parent("commit", "--quiet", "-m", "戻った")
	archiveRemove(tr, "done/old.md")
	if got := sortedKinds(tr, "")[".ccnavi/approved/done/old.md"]; got == kindB {
		t.Error("済んだ ready の印が後の削除を運んだ")
	}
}

// ready の打ち直しは、マージリクエストが Draft のときだけ通す（外れていれば終わっている・分からなければ通さない・印が無ければ通さない）。
func TestReadyAgainNeedsTheMergeRequestToBeADraft(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.OK(t, ready(tr, f))
	tr.CommitParent("退避")
	tr.Parent("push", "--quiet", "origin", "i0001")
	undrafted := ticketkit.Write(t, filepath.Join(tr.Root, "fixture-undrafted.json"), `{"host": "fixture", "mr": {"number": 7, "url": "u/7", "draft": false}}`)
	ticketkit.Fails(t, ready(tr, ticketkit.Fixture{T: t, Path: undrafted}))
	unknown := ticketkit.Write(t, filepath.Join(tr.Root, "fixture-unknown.json"), `{"host": "fixture", "mr": {"number": 7, "url": "u/7"}}`)
	ticketkit.Fails(t, ready(tr, ticketkit.Fixture{T: t, Path: unknown}))
	if err := os.Remove(filepath.Join(archiveSelf(tr), "ready", "i0001.json")); err != nil {
		t.Fatal(err)
	}
	ticketkit.Fails(t, ready(tr, f))
}

// 早めに閉じた理由は、ツリーの側からも読む。
func TestTheReasonOfAnEarlyCloseIsReadFromTheTreeToo(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.Write(t, filepath.Join(tr.Approved, "phases", "i0001", "close-early.json"), `{"reason": "ここまでで十分"}`)
	tr.CommitParent("早めに閉じた")
	tr.Parent("push", "--quiet", "origin", "i0001")
	// 退避の行き先をディレクトリにして、close-early.json の手前で止める。
	blocker := filepath.Join(archiveSelf(tr), "phases", "i0001", "close-early.json")
	if err := os.MkdirAll(blocker, 0o755); err != nil {
		t.Fatal(err)
	}
	ticketkit.Fails(t, ready(tr, f))
	if !existsIn(tr.Approved, "phases/i0001/close-early.json") {
		t.Error("止まったのに置き場から消えた")
	}
	if err := os.Remove(blocker); err != nil {
		t.Fatal(err)
	}
	again := ticketkit.OK(t, ready(tr, f))
	ticketkit.Has(t, ticketkit.Read(t, ticketkit.Lines(again.Stdout)[0]), "ここまでで十分")
}

func TestLintSeesAnArchivedParentTreeOffItsBranch(t *testing.T) {
	t.Parallel()
	tr, f := closable(t)
	ticketkit.OK(t, ready(tr, f))
	tr.CommitParent("退避")
	tr.Parent("checkout", "--quiet", "-b", "elsewhere")
	linted := tr.Ccnavi("--lint")
	ticketkit.Has(t, ticketkit.Out(linted), "i0001 のワークツリーが elsewhere の上にある")
}
