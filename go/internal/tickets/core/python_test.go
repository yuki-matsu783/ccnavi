package core

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/review/request"
	"ccnavi/internal/tickets/testkit"
)

// 承認とレビュー済みは取り返しがつかないので、同じ形のワークスペースを Python 用と Go 用に作り、同じ操作を同じ順に
// 打って、終了コード・標準出力・標準エラー（JSON を含む）・書いたファイルが 1 字も違わないことを見る。

// i0003 は欄の誤り（workflow）で外れる親。
var (
	i0002Text = parentText("i0002")
	i0003Text = parentText("i0003", "workflow: dag")
	i0004Text = parentText("i0004")
)

func approveWorkspace(t *testing.T) string {
	root := workspace(t, "i0001", "i0002", "i0003", "i0004")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "phases.yml"), "version: 1\n"+phasesCopy)
	todo := filepath.Join(testkit.Worktree(root, "i0001"), "wip", "proposals", "todo")
	testkit.Put(t, filepath.Join(todo, "i0001.md"), parentText("i0001"))
	testkit.Put(t, filepath.Join(todo, "i0001-01-01.md"), childText("i0001-01-01", "i0001"))
	return root
}

type flowRun struct {
	t    *testing.T
	root string
	out  []step
}

func (r *flowRun) run(name string, f func(o, e *bytes.Buffer) int) string {
	var o, e bytes.Buffer
	code := f(&o, &e)
	rel := func(s string) string { return strings.ReplaceAll(s, r.root, "<root>") }
	r.out = append(r.out, step{name, float64(code), rel(o.String()), rel(e.String())})
	return o.String()
}

func (r *flowRun) must(code int, err error) int {
	if err != nil {
		r.t.Fatal(err)
	}
	return code
}

func TestApproveMatchesPython(t *testing.T) {
	root := approveWorkspace(t)
	want := golden.Load[answers](t, "approve")
	conf, _ := config.Load(root)
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	defer via.Session(via.CLI, "")()
	r := &flowRun{t: t, root: root}
	family := []string{"i0001", "i0001-01-01"}
	placed := filepath.Join(root, "placed.json")
	gate := func(ids []string) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int { return ApprovalRequestGate(e, conf, root, ids) }
	}
	preview := func(asJSON bool, only ...string) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int { return Preview(o, e, conf, root, asJSON, only) }
	}
	verify := func(asJSON bool) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int { return Verify(o, e, conf, root, asJSON, nil) }
	}
	yes := func(ids []string, asJSON bool, only []string, digest string) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int { return r.must(ApproveYes(o, e, conf, root, ids, asJSON, only, digest)) }
	}
	agree := func(answer string, only []string, out string) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int {
			return r.must(Approve(strings.NewReader(answer), o, e, conf, root, only, out))
		}
	}
	r.run("gate", gate(family))
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0001"), "README.md"), "changed\n")
	r.run("gate-dirty", gate(family))
	r.run("gate-other", gate([]string{"nope"}))
	r.run("preview", preview(false))
	shown, err := pyjson.Loads(r.run("preview-json", preview(true)))
	if err != nil {
		t.Fatal(err)
	}
	digest := shown.(*pyjson.Object).String("digest")
	r.run("preview-only", preview(false, "i0001", "nope"))
	r.run("verify", verify(false))
	r.run("verify-json", verify(true))
	board := via.Session(via.Board, "")
	r.run("yes-no-digest", yes(family, true, nil, " "))
	r.run("yes-list", yes([]string{"i0001"}, true, nil, digest))
	r.run("yes-digest", yes(family, true, nil, strings.Repeat("0", 64)))
	r.run("yes-filter", yes(family, false, []string{"nope"}, digest))
	r.run("yes-json", yes([]string{" i0001-01-01", "i0001", ""}, true, nil, strings.ToUpper(digest)))
	r.run("yes-again", yes(family, true, nil, digest))
	board()
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0002"), "wip", "proposals", "todo", "i0002.md"), i0002Text)
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0004"), "wip", "proposals", "todo", "i0004.md"), i0004Text)
	// i0004 の承認済みチケットの置き場をファイルで塞ぎ、書く途中で止まる形を作る（i0002 は置ける）。
	blocker := filepath.Join(testkit.Worktree(root, "i0004"), ".ccnavi", "approved", "doing")
	testkit.Put(t, blocker, "x\n")
	later, err := pyjson.Loads(r.run("preview-blocked", preview(true)))
	if err != nil {
		t.Fatal(err)
	}
	var laterIDs []string
	for _, b := range later.(*pyjson.Object).Value("batch").([]any) {
		laterIDs = append(laterIDs, b.(*pyjson.Object).String("ticket"))
	}
	board = via.Session(via.Board, "")
	r.run("yes-partial", yes(laterIDs, true, nil, later.(*pyjson.Object).String("digest")))
	board()
	remove(t, blocker)
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0003"), "wip", "proposals", "todo", "i0003.md"), i0003Text)
	testkit.Put(t, blocker, "x\n")
	terminal := via.Session(via.Terminal, "")
	r.run("agree-stop", agree("y\n", nil, placed))
	r.out = append(r.out, step{"placed-stop", float64(0), readRel(t, root, placed), ""})
	remove(t, blocker)
	r.run("agree-no", agree("n\n", nil, ""))
	r.run("agree-yes", agree(" Y \n", nil, placed))
	r.run("agree-none", agree("", []string{"i0003"}, ""))
	terminal()
	r.out = append(r.out, step{"placed", float64(0), readRel(t, root, placed), ""})
	var bases []string
	for _, name := range []string{"i0001", "i0002", "i0003", "i0004"} {
		bases = append(bases, filepath.Join(testkit.Worktree(root, name), ".ccnavi"), filepath.Join(testkit.Worktree(root, name), "wip"))
	}
	compare(t, answers{r.out, filesUnder(root, bases...)}, want)
}

// reviewWorkspace は親 i0001 の承認済みチケットと、フェーズ 1 のレビュー待ちの子を親のブランチに置いて push する。
// 子の merged_sha は親のブランチの作業のコミット。logs/ にはホストから取ってきた結果の見本を置く。
func reviewWorkspace(t *testing.T) string {
	root := workspace(t, "i0001")
	tree := testkit.Worktree(root, "i0001")
	testkit.Put(t, filepath.Join(tree, "src", "a.go"), "package a\n")
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "child work")
	merged := strings.TrimSpace(testkit.Git(t, tree, "rev-parse", "HEAD"))
	testkit.Put(t, filepath.Join(tree, ".ccnavi", "approved", "doing", "i0001.md"), strings.Replace(parentText("i0001", "feedback: []"),
		"---\n\n", "started_at: \"2026-01-02T00:00:00+0900\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n", 1))
	testkit.Put(t, filepath.Join(tree, "wip", "proposals", "review", "i0001-01-01.md"), childText("i0001-01-01", "i0001",
		`completed_at: "2026-01-03T00:00:00+0900"`, `finished_to: "review"`, `merged_sha: "`+merged+`"`))
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "tickets")
	testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	testkit.Put(t, filepath.Join(root, "body.md"), "依頼の本文\n")
	mr := `"host": "github", "mr": {"number": 7, "url": "https://h/pr/7"}`
	logs := filepath.Join(root, "logs")
	testkit.Put(t, filepath.Join(logs, "posted.json"), `{`+mr+`, "url": "https://h/pr/7#c1", `+
		`"created_at": "2026-01-05T01:00:00Z", "author": "bot", "threads": [], "reviews": []}`)
	testkit.Put(t, filepath.Join(logs, "other.json"), `{"host": "github", "mr": {"number": 8, "url": "https://h/pr/8"}, "threads": [], "reviews": []}`)
	testkit.Put(t, filepath.Join(logs, "threads.json"), `{`+mr+`, "threads": [{"id": "d1", "url": "u1", "path": "src/a.go", "line": 1, `+
		`"body": "指摘 1", "last_id": "c1"}, {"id": "d3", "url": "u3", "resolved": true, "body": "済み"}], "reviews": []}`)
	testkit.Put(t, filepath.Join(logs, "changes.json"), `{`+mr+`, "threads": [], "reviews": [{"state": "CHANGES_REQUESTED", `+
		`"url": "r/1", "submitted_at": "2026-01-05T02:00:00Z", "author": "rev"}]}`)
	testkit.Put(t, filepath.Join(logs, "clean.json"), `{`+mr+`, "threads": [{"id": "d3", "url": "u3", "resolved": true, "body": "済み"}], "reviews": []}`)
	return root
}

func TestConfirmMatchesPython(t *testing.T) {
	root := reviewWorkspace(t)
	want := golden.Load[answers](t, "confirm")
	conf, _ := config.Load(root)
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	defer via.Session(via.CLI, "")()
	r := &flowRun{t: t, root: root}
	tree := testkit.Worktree(root, "i0001")
	result := func(name string) string { return filepath.Join(root, "logs", name+".json") }
	check := func(number int) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int { return CheckConfirm(o, e, conf, root, tree, number) }
	}
	confirm := func(path, account string) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int { return r.must(ConfirmLocal(o, e, conf, root, tree, 1, path, account)) }
	}
	r.run("prepare", func(o, e *bytes.Buffer) int {
		return request.Prepare(o, e, conf, root, tree, 1, filepath.Join(root, "body.md"))
	})
	r.run("requested", func(o, e *bytes.Buffer) int {
		return r.must(request.Requested(o, e, conf, root, tree, 1, result("posted")))
	})
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "requested")
	testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	r.run("check", check(1))
	r.run("check-phase", check(2))
	r.run("no-result", confirm("", ""))
	r.run("other-mr", confirm(result("other"), ""))
	r.run("threads", confirm(result("threads"), ""))
	r.run("changes", confirm(result("changes"), ""))
	f, err := os.OpenFile(filepath.Join(tree, "src", "a.go"), os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	f.WriteString("// more\n")
	f.Close()
	testkit.Git(t, tree, "commit", "-q", "-am", "more")
	testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	r.run("moved", check(1))
	testkit.Git(t, tree, "reset", "-q", "--hard", "HEAD~1")
	testkit.Git(t, tree, "push", "-q", "-f", "origin", "i0001")
	r.run("confirm", confirm(result("clean"), "octo"))
	r.run("again", check(1))
	r.run("again-confirm", confirm(result("clean"), ""))
	compare(t, answers{r.out, filesUnder(root, filepath.Join(tree, ".ccnavi"), filepath.Join(tree, "wip"))}, want)
}

func readRel(t *testing.T, root, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return strings.ReplaceAll(string(data), root, "<root>")
}

func remove(t *testing.T, path string) {
	t.Helper()
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
}

func TestManualConfirmMatchesPython(t *testing.T) {
	prepare := func(root string) string {
		mr := `"host": "github", "mr": {"number": 7, "url": "https://h/pr/7"}`
		thread := func(created string) string {
			return `{` + mr + `, "threads": [{"id": "d1", "url": "u1", "body": "指摘", "created_at": "` + created +
				`", "last_id": "c1"}], "reviews": []}`
		}
		testkit.Put(t, filepath.Join(root, "logs", "old.json"), thread("2026-01-01T00:00:00Z"))
		testkit.Put(t, filepath.Join(root, "logs", "late.json"), thread("2999-01-01T00:00:00Z"))
		return root
	}
	root := prepare(reviewWorkspace(t))
	want := golden.Load[answers](t, "manual")
	conf, _ := config.Load(root)
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	defer via.Session(via.CLI, "")()
	r := &flowRun{t: t, root: root}
	tree := testkit.Worktree(root, "i0001")
	result := func(name string) string { return filepath.Join(root, "logs", name+".json") }
	confirm := func(name string) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int { return r.must(ConfirmLocal(o, e, conf, root, tree, 1, result(name), "")) }
	}
	r.run("prepare", func(o, e *bytes.Buffer) int {
		return request.Prepare(o, e, conf, root, tree, 1, filepath.Join(root, "body.md"))
	})
	r.run("requested", func(o, e *bytes.Buffer) int {
		return r.must(request.Requested(o, e, conf, root, tree, 1, result("posted")))
	})
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "requested")
	testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	done := filepath.Join(tree, ".ccnavi", "approved", "done", "i0001-01-01.md")
	if err := os.MkdirAll(filepath.Dir(done), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Join(tree, "wip", "proposals", "review", "i0001-01-01.md"), done); err != nil {
		t.Fatal(err)
	}
	r.run("unshared", confirm("old"))
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "move by hand")
	testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	r.run("late", confirm("late"))
	r.run("changes", confirm("changes"))
	r.run("old", confirm("old"))
	compare(t, answers{r.out, filesUnder(root, filepath.Join(tree, ".ccnavi"), filepath.Join(tree, "wip"))}, want)
}
