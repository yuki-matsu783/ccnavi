package core

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/pyoracle"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/review/request"
)

// 承認とレビュー済みは取り返しがつかないので、同じ形のワークスペースを Python 用と Go 用に作り、同じ操作を同じ順に
// 打って、終了コード・標準出力・標準エラー（JSON を含む）・書いたファイルが 1 字も違わないことを見る。

// 承認の流れ: 承認を頼む前のゲート（きれい・親のワークツリーが汚れた）→ preview（テキスト・JSON）→ verify
// （テキスト・JSON）→ --yes（ダイジェスト無し・一覧の違い・中身の違い・絞りが通らない・テキストで通る）→ 別の親と
// 外れる提案を足して、ターミナルの承認（いいえ・はい。--placed-out）。
const approveOracle = `
import io, json, os, sys
from ccnavi.infra import fsio, settings
from ccnavi.tickets.core import approve
from ccnavi.tickets.model import via
c = json.load(sys.stdin)
root = c["root"]
def rel(s):
    return s.replace(root, "<root>")
def wt(name):
    return os.path.join(root, ".claude", "worktrees", name)
def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
conf, _ = settings.load(root)
out = []
def run(name, f, *args):
    o, e = io.StringIO(), io.StringIO()
    code = f(o, e, *args)
    out.append([name, code, rel(o.getvalue()), rel(e.getvalue())])
    return o.getvalue()
def gate(o, e, ids):
    return approve.approval_request_gate(e, root, conf, ids)
family = ["i0001", "i0001-01-01"]
placed = os.path.join(root, "placed.json")
with fsio.clock(c["clock"]), via.session(via.VIA_CLI):
    run("gate", gate, family)
    put(os.path.join(wt("i0001"), "README.md"), "changed\n")
    run("gate-dirty", gate, family)
    run("gate-other", gate, ["nope"])
    run("preview", approve.preview, conf, root, False, [])
    digest = json.loads(run("preview-json", approve.preview, conf, root, True, []))["digest"]
    run("preview-only", approve.preview, conf, root, False, ["i0001", "nope"])
    run("verify", approve.verify, conf, root, False, [])
    run("verify-json", approve.verify, conf, root, True, [])
    with via.session(via.VIA_BOARD):
        run("yes-no-digest", approve.approve_yes, conf, root, family, True, [], " ")
        run("yes-list", approve.approve_yes, conf, root, ["i0001"], True, [], digest)
        run("yes-digest", approve.approve_yes, conf, root, family, True, [], "0" * 64)
        run("yes-filter", approve.approve_yes, conf, root, family, False, ["nope"], digest)
        run("yes-json", approve.approve_yes, conf, root, [" i0001-01-01", "i0001", ""], True, [], digest.upper())
        run("yes-again", approve.approve_yes, conf, root, family, True, [], digest)
    put(os.path.join(wt("i0002"), "wip", "proposals", "todo", "i0002.md"), c["i0002"])
    put(os.path.join(wt("i0004"), "wip", "proposals", "todo", "i0004.md"), c["i0004"])
    blocker = os.path.join(wt("i0004"), ".ccnavi", "approved", "doing")
    put(blocker, "x\n")
    later = json.loads(run("preview-blocked", approve.preview, conf, root, True, []))
    with via.session(via.VIA_BOARD):
        run("yes-partial", approve.approve_yes, conf, root, [b["ticket"] for b in later["batch"]], True, [], later["digest"])
    os.remove(blocker)
    put(os.path.join(wt("i0003"), "wip", "proposals", "todo", "i0003.md"), c["i0003"])
    put(blocker, "x\n")
    with via.session(via.VIA_TERMINAL):
        run("agree-stop", lambda o, e: approve.approve(io.StringIO("y\n"), o, e, conf, root, [], placed))
    with open(placed, encoding="utf-8") as f:
        out.append(["placed-stop", 0, rel(f.read()), ""])
    os.remove(blocker)
    with via.session(via.VIA_TERMINAL):
        run("agree-no", lambda o, e: approve.approve(io.StringIO("n\n"), o, e, conf, root))
        run("agree-yes", lambda o, e: approve.approve(io.StringIO(" Y \n"), o, e, conf, root, [], placed))
        run("agree-none", lambda o, e: approve.approve(io.StringIO(""), o, e, conf, root, ["i0003"]))
with open(placed, encoding="utf-8") as f:
    out.append(["placed", 0, rel(f.read()), ""])
files = {}
for name in ("i0001", "i0002", "i0003", "i0004"):
    for base in (os.path.join(wt(name), ".ccnavi"), os.path.join(wt(name), "wip")):
        for here, _, names in os.walk(base):
            for n in names:
                p = os.path.join(here, n)
                with open(p, encoding="utf-8") as f:
                    files[rel(p).replace(os.sep, "/")] = rel(f.read())
json.dump({"out": out, "files": files}, sys.stdout, ensure_ascii=False)
`

// i0003 は欄の誤り（workflow）で外れる親。
var (
	i0002Text = parentText("i0002")
	i0003Text = parentText("i0003", "workflow: dag")
	i0004Text = parentText("i0004")
)

func approveWorkspace(t *testing.T) string {
	root := workspace(t, "i0001", "i0002", "i0003", "i0004")
	put(t, filepath.Join(root, ".ccnavi", "config", "common", "phases.yml"), "version: 1\n"+phasesCopy)
	todo := filepath.Join(worktree(root, "i0001"), "wip", "proposals", "todo")
	put(t, filepath.Join(todo, "i0001.md"), parentText("i0001"))
	put(t, filepath.Join(todo, "i0001-01-01.md"), childText("i0001-01-01", "i0001"))
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
	pyRoot, root := approveWorkspace(t), approveWorkspace(t)
	want := pyoracle.Answers[answers](t, "approve", approveOracle, map[string]any{
		"root": pyRoot, "clock": fixedClock, "i0002": i0002Text, "i0003": i0003Text, "i0004": i0004Text,
	})
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
	put(t, filepath.Join(worktree(root, "i0001"), "README.md"), "changed\n")
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
	put(t, filepath.Join(worktree(root, "i0002"), "wip", "proposals", "todo", "i0002.md"), i0002Text)
	put(t, filepath.Join(worktree(root, "i0004"), "wip", "proposals", "todo", "i0004.md"), i0004Text)
	// i0004 の承認済みチケットの置き場をファイルで塞ぎ、書く途中で止まる形を作る（i0002 は置ける）。
	blocker := filepath.Join(worktree(root, "i0004"), ".ccnavi", "approved", "doing")
	put(t, blocker, "x\n")
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
	put(t, filepath.Join(worktree(root, "i0003"), "wip", "proposals", "todo", "i0003.md"), i0003Text)
	put(t, blocker, "x\n")
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
		bases = append(bases, filepath.Join(worktree(root, name), ".ccnavi"), filepath.Join(worktree(root, name), "wip"))
	}
	compare(t, answers{r.out, filesUnder(root, bases...)}, want)
}

// レビュー済みの流れ: 子をレビュー待ちに置いた親で依頼し（request の prepare・requested）、マーカーを push してから、
// confirm --check → 結果の無い・別の MR の・未解決の残る・変更要求の confirm → 依頼の後に HEAD を動かした --check →
// 戻して --actor 付きの confirm（通る）→ 重ね打ちの --check と confirm。
const confirmOracle = `
import io, json, os, subprocess, sys
from ccnavi.infra import fsio, settings
from ccnavi.tickets.core import review
from ccnavi.tickets.model import via
from ccnavi.tickets.review import request
c = json.load(sys.stdin)
root = c["root"]
tree = os.path.join(root, ".claude", "worktrees", "i0001")
logs = os.path.join(root, "logs")
env = dict(os.environ, **dict(kv.split("=", 1) for kv in c["env"]))
def git(*args):
    subprocess.run(["git", *args], cwd=tree, env=env, check=True, capture_output=True)
def rel(s):
    return s.replace(root, "<root>")
conf, _ = settings.load(root)
out = []
def run(name, f, *args):
    o, e = io.StringIO(), io.StringIO()
    code = f(o, e, *args)
    out.append([name, code, rel(o.getvalue()), rel(e.getvalue())])
    return o.getvalue()
def result(name):
    return os.path.join(logs, name + ".json")
with fsio.clock(c["clock"]), via.session(via.VIA_CLI):
    run("prepare", request.prepare, root, conf, tree, 1, os.path.join(root, "body.md"))
    run("requested", request.requested, root, conf, tree, 1, result("posted"))
    git("add", "-A")
    git("commit", "-q", "-m", "requested")
    git("push", "-q", "origin", "i0001")
    run("check", review.check_confirm, root, conf, tree, 1)
    run("check-phase", review.check_confirm, root, conf, tree, 2)
    run("no-result", review.confirm_local, root, conf, tree, 1, "")
    run("other-mr", review.confirm_local, root, conf, tree, 1, result("other"))
    run("threads", review.confirm_local, root, conf, tree, 1, result("threads"))
    run("changes", review.confirm_local, root, conf, tree, 1, result("changes"))
    with open(os.path.join(tree, "src", "a.go"), "a", encoding="utf-8") as f:
        f.write("// more\n")
    git("commit", "-q", "-am", "more")
    git("push", "-q", "origin", "i0001")
    run("moved", review.check_confirm, root, conf, tree, 1)
    git("reset", "-q", "--hard", "HEAD~1")
    git("push", "-q", "-f", "origin", "i0001")
    run("confirm", review.confirm_local, root, conf, tree, 1, result("clean"), "octo")
    run("again", review.check_confirm, root, conf, tree, 1)
    run("again-confirm", review.confirm_local, root, conf, tree, 1, result("clean"))
files = {}
for base in (os.path.join(tree, ".ccnavi"), os.path.join(tree, "wip")):
    for here, _, names in os.walk(base):
        for n in names:
            p = os.path.join(here, n)
            with open(p, encoding="utf-8") as f:
                files[rel(p).replace(os.sep, "/")] = rel(f.read())
json.dump({"out": out, "files": files}, sys.stdout, ensure_ascii=False)
`

// reviewWorkspace は親 i0001 の承認済みチケットと、フェーズ 1 のレビュー待ちの子を親のブランチに置いて push する。
// 子の merged_sha は親のブランチの作業のコミット。logs/ にはホストから取ってきた結果の見本を置く。
func reviewWorkspace(t *testing.T) string {
	root := workspace(t, "i0001")
	tree := worktree(root, "i0001")
	put(t, filepath.Join(tree, "src", "a.go"), "package a\n")
	runGit(t, tree, "add", "-A")
	runGit(t, tree, "commit", "-q", "-m", "child work")
	merged := strings.TrimSpace(runGit(t, tree, "rev-parse", "HEAD"))
	put(t, filepath.Join(tree, ".ccnavi", "approved", "doing", "i0001.md"), strings.Replace(parentText("i0001", "feedback: []"),
		"---\n\n", "started_at: \"2026-01-02T00:00:00+0900\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n", 1))
	put(t, filepath.Join(tree, "wip", "proposals", "review", "i0001-01-01.md"), childText("i0001-01-01", "i0001",
		`completed_at: "2026-01-03T00:00:00+0900"`, `finished_to: "review"`, `merged_sha: "`+merged+`"`))
	runGit(t, tree, "add", "-A")
	runGit(t, tree, "commit", "-q", "-m", "tickets")
	runGit(t, tree, "push", "-q", "origin", "i0001")
	put(t, filepath.Join(root, "body.md"), "依頼の本文\n")
	mr := `"host": "github", "mr": {"number": 7, "url": "https://h/pr/7"}`
	logs := filepath.Join(root, "logs")
	put(t, filepath.Join(logs, "posted.json"), `{`+mr+`, "url": "https://h/pr/7#c1", `+
		`"created_at": "2026-01-05T01:00:00Z", "author": "bot", "threads": [], "reviews": []}`)
	put(t, filepath.Join(logs, "other.json"), `{"host": "github", "mr": {"number": 8, "url": "https://h/pr/8"}, "threads": [], "reviews": []}`)
	put(t, filepath.Join(logs, "threads.json"), `{`+mr+`, "threads": [{"id": "d1", "url": "u1", "path": "src/a.go", "line": 1, `+
		`"body": "指摘 1", "last_id": "c1"}, {"id": "d3", "url": "u3", "resolved": true, "body": "済み"}], "reviews": []}`)
	put(t, filepath.Join(logs, "changes.json"), `{`+mr+`, "threads": [], "reviews": [{"state": "CHANGES_REQUESTED", `+
		`"url": "r/1", "submitted_at": "2026-01-05T02:00:00Z", "author": "rev"}]}`)
	put(t, filepath.Join(logs, "clean.json"), `{`+mr+`, "threads": [{"id": "d3", "url": "u3", "resolved": true, "body": "済み"}], "reviews": []}`)
	return root
}

func TestConfirmMatchesPython(t *testing.T) {
	pyRoot, root := reviewWorkspace(t), reviewWorkspace(t)
	want := pyoracle.Answers[answers](t, "confirm", confirmOracle, map[string]any{
		"root": pyRoot, "clock": fixedClock, "env": gitEnv,
	})
	conf, _ := config.Load(root)
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	defer via.Session(via.CLI, "")()
	r := &flowRun{t: t, root: root}
	tree := worktree(root, "i0001")
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
	runGit(t, tree, "add", "-A")
	runGit(t, tree, "commit", "-q", "-m", "requested")
	runGit(t, tree, "push", "-q", "origin", "i0001")
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
	runGit(t, tree, "commit", "-q", "-am", "more")
	runGit(t, tree, "push", "-q", "origin", "i0001")
	r.run("moved", check(1))
	runGit(t, tree, "reset", "-q", "--hard", "HEAD~1")
	runGit(t, tree, "push", "-q", "-f", "origin", "i0001")
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

// 手で動かした子の確定: 依頼の後に子を review/ から done/ へ手で動かし、未コミット（移動が未共有）の confirm →
// コミットして push し、移動の後に付いた指摘・変更要求・移動より前の指摘の confirm（版つきで受け入れ台帳に足す）。
const manualOracle = `
import io, json, os, shutil, subprocess, sys
from ccnavi.infra import fsio, settings
from ccnavi.tickets.core import review
from ccnavi.tickets.model import via
from ccnavi.tickets.review import request
c = json.load(sys.stdin)
root = c["root"]
tree = os.path.join(root, ".claude", "worktrees", "i0001")
logs = os.path.join(root, "logs")
env = dict(os.environ, **dict(kv.split("=", 1) for kv in c["env"]))
def git(*args):
    subprocess.run(["git", *args], cwd=tree, env=env, check=True, capture_output=True)
def rel(s):
    return s.replace(root, "<root>")
conf, _ = settings.load(root)
out = []
def run(name, f, *args):
    o, e = io.StringIO(), io.StringIO()
    code = f(o, e, *args)
    out.append([name, code, rel(o.getvalue()), rel(e.getvalue())])
def result(name):
    return os.path.join(logs, name + ".json")
with fsio.clock(c["clock"]), via.session(via.VIA_CLI):
    run("prepare", request.prepare, root, conf, tree, 1, os.path.join(root, "body.md"))
    run("requested", request.requested, root, conf, tree, 1, result("posted"))
    git("add", "-A")
    git("commit", "-q", "-m", "requested")
    git("push", "-q", "origin", "i0001")
    done = os.path.join(tree, ".ccnavi", "approved", "done")
    os.makedirs(done, exist_ok=True)
    shutil.move(os.path.join(tree, "wip", "proposals", "review", "i0001-01-01.md"), os.path.join(done, "i0001-01-01.md"))
    run("unshared", review.confirm_local, root, conf, tree, 1, result("old"))
    git("add", "-A")
    git("commit", "-q", "-m", "move by hand")
    git("push", "-q", "origin", "i0001")
    run("late", review.confirm_local, root, conf, tree, 1, result("late"))
    run("changes", review.confirm_local, root, conf, tree, 1, result("changes"))
    run("old", review.confirm_local, root, conf, tree, 1, result("old"))
files = {}
for base in (os.path.join(tree, ".ccnavi"), os.path.join(tree, "wip")):
    for here, _, names in os.walk(base):
        for n in names:
            p = os.path.join(here, n)
            with open(p, encoding="utf-8") as f:
                files[rel(p).replace(os.sep, "/")] = rel(f.read())
json.dump({"out": out, "files": files}, sys.stdout, ensure_ascii=False)
`

func TestManualConfirmMatchesPython(t *testing.T) {
	prepare := func(root string) string {
		mr := `"host": "github", "mr": {"number": 7, "url": "https://h/pr/7"}`
		thread := func(created string) string {
			return `{` + mr + `, "threads": [{"id": "d1", "url": "u1", "body": "指摘", "created_at": "` + created +
				`", "last_id": "c1"}], "reviews": []}`
		}
		put(t, filepath.Join(root, "logs", "old.json"), thread("2026-01-01T00:00:00Z"))
		put(t, filepath.Join(root, "logs", "late.json"), thread("2999-01-01T00:00:00Z"))
		return root
	}
	pyRoot, root := prepare(reviewWorkspace(t)), prepare(reviewWorkspace(t))
	want := pyoracle.Answers[answers](t, "manual", manualOracle, map[string]any{
		"root": pyRoot, "clock": fixedClock, "env": gitEnv,
	})
	conf, _ := config.Load(root)
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	defer via.Session(via.CLI, "")()
	r := &flowRun{t: t, root: root}
	tree := worktree(root, "i0001")
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
	runGit(t, tree, "add", "-A")
	runGit(t, tree, "commit", "-q", "-m", "requested")
	runGit(t, tree, "push", "-q", "origin", "i0001")
	done := filepath.Join(tree, ".ccnavi", "approved", "done", "i0001-01-01.md")
	if err := os.MkdirAll(filepath.Dir(done), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Join(tree, "wip", "proposals", "review", "i0001-01-01.md"), done); err != nil {
		t.Fatal(err)
	}
	r.run("unshared", confirm("old"))
	runGit(t, tree, "add", "-A")
	runGit(t, tree, "commit", "-q", "-m", "move by hand")
	runGit(t, tree, "push", "-q", "origin", "i0001")
	r.run("late", confirm("late"))
	r.run("changes", confirm("changes"))
	r.run("old", confirm("old"))
	compare(t, answers{r.out, filesUnder(root, filepath.Join(tree, ".ccnavi"), filepath.Join(tree, "wip"))}, want)
}
