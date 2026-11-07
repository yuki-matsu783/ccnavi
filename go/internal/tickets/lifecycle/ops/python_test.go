package ops

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/tickets/lifecycle/consolidate"
	"ccnavi/internal/tickets/lifecycle/internal/gitfixture"
	"ccnavi/internal/tickets/lifecycle/wapmerge"
	"ccnavi/internal/tickets/model/pyoracle"
)

// Python の commands・consolidate・write_and_push_merge と同じワークスペースを 2 つ作り、着手・判定の記録・合流の前の
// 検査・終了・取り消し・統合の子の作成を同じ順に打って（合流とコミットは sh の代わりにテストが打つ）、出力・終了
// コード・書いたファイル・ワークツリー・ブランチが 1 字も違わないことを見る。

const stamp = "2026-01-06T00:00:00+0900"

const risks = `version: 1
levels:
  medium: 20
  high: 40
  critical: 70
factors:
  - id: untested
    points: 30
    judge: テストの無い振る舞いの変更を含むか
    message: テストの無い変更
  - id: hot
    points: 50
    glob: "src/hot/*"
    message: 重要なパス
`

const record1 = "---\nticket: i0001-01-01\nparent: i0001\nlearnings: 1\n---\n\n## 落とし穴\n\n- 種類: 未定\n- 内容: x\n"

func recordNone(id, parent string) string {
	return "---\nticket: " + id + "\nparent: " + parent + "\nlearnings: none\n---\n"
}

// fixture のワークスペース。
//
//	i0001  計画のある親。子 01-01（気づき 1 件を書く）と 01-02（取り消す）
//	i0002  計画のある親。子 01-01 は重要なパスを変え、実績のリスクでレビュー待ちに入る
//	i0003  提案（todo/）だけの親
//	i0004  計画のある親。子 01-01 は気づき無しで閉じ、親も閉じる
func fixture(t *testing.T) string {
	root := gitfixture.Workspace(t)
	gitfixture.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "risks.yml"), risks)
	for _, parent := range []string{"i0001", "i0002", "i0004"} {
		tree := gitfixture.AddWorktree(t, root, parent, "main")
		var extra []string
		if parent != "i0002" {
			extra = append(extra, "feedback: []")
		}
		gitfixture.Put(t, gitfixture.Approved(root, parent, "doing", parent), gitfixture.ParentText(parent, []string{"  - work"}, extra...))
		children := []string{parent + "-01-01"}
		if parent == "i0001" {
			children = append(children, "i0001-01-02")
		}
		for _, c := range children {
			gitfixture.Put(t, gitfixture.Approved(root, parent, "doing", c), gitfixture.ChildText(c, parent, "1"))
		}
		gitfixture.Commit(t, tree, "approve "+parent)
	}
	gitfixture.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0003.md"), gitfixture.ParentText("i0003", []string{"  - work"}))
	return root
}

const oracle = `
import io, json, os, subprocess, sys
from ccnavi.infra import fsio, settings
from ccnavi.tickets.lifecycle import commands as ops, consolidate, write_and_push_merge as wapmerge
c = json.load(sys.stdin)
root = c["root"]
def rel(s):
    return s.replace(root, "<root>")
def wt(name):
    return os.path.join(root, ".claude", "worktrees", name)
def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
def commit(cwd, msg):
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "--allow-empty", "-m", msg)
out = {}
conf, _ = settings.load(root)
def run(name, f, *args, **kw):
    o, e = io.StringIO(), io.StringIO()
    code = f(o, e, root, conf, *args, **kw)
    out[name] = [code, rel(o.getvalue()), rel(e.getvalue())]
def wap(name, ident):
    o = io.StringIO()
    code = wapmerge.plan(o, conf, root, ident)
    out[name] = [code, rel(o.getvalue())]
def merge(parent, child):
    head = git(wt(child), "rev-parse", "HEAD").strip()
    git(wt(parent), "merge", "--no-ff", "--no-commit", head)
def texts():
    found = {}
    for base, _, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            r = os.path.relpath(p, root).replace(os.sep, "/")
            if "/.ccnavi/approved/" in "/" + r or r.startswith("wip/") or "/wip/" in r or r.startswith("logs/state/risk-judge-"):
                if "/.git/" in "/" + r + "/":
                    continue
                with open(p, encoding="utf-8", newline="") as f:
                    found[r] = rel(f.read())
    return found
C1, C2 = "i0001-01-01", "i0001-01-02"
C3, C4 = "i0002-01-01", "i0004-01-01"
with fsio.clock(c["stamp"]):
    run("check start parent", ops.check, "start", "i0001", "")
    run("start unknown", ops.start, "i0099")
    run("start child early", ops.start, C1)
    run("start parent", ops.start, "i0001")
    run("start parent again", ops.start, "i0001")
    commit(wt("i0001"), "start")
    git(root, "worktree", "add", "-q", "-b", C1, os.path.join(".claude", "worktrees", C1), "i0001")
    run("start c1", ops.start, C1)
    put(os.path.join(wt(C1), "src", "a.py"), "a = 1\n")
    put(os.path.join(wt(C1), "wip", "handoff", "learnings", C1 + ".md"), c["record1"])
    commit(wt(C1), "work c1")
    run("check finish c1", ops.check, "finish", C1, "")
    run("risk bad answer", ops.record_risk, C1, "untested", "maybe", "r")
    run("risk no reason", ops.record_risk, C1, "untested", "yes", "  ")
    run("risk parent", ops.record_risk, "i0001", "untested", "yes", "r")
    run("risk not judge", ops.record_risk, C1, "hot", "yes", "r")
    wap("wap parent", "i0001")
    run("finish c1 pending", ops.finish, C1)
    run("risk c1", ops.record_risk, C1, "untested", " NO ", "テストを足した")
    wap("wap c1", C1)
    merge("i0001", C1)
    run("finish c1", ops.finish, C1)
    commit(wt("i0001"), "finish c1")
    run("check cancel c2", ops.check, "cancel", C2, "")
    run("cancel c2 no reason", ops.cancel, C2, " ")
    run("cancel todo", ops.cancel, "i0003", "r")
    run("cancel c2", ops.cancel, C2, " やめた ")
    commit(wt("i0001"), "cancel c2")
    run("finish parent learning", ops.finish, "i0001")
    run("check consolidate", consolidate.check, "i0001")
    run("consolidate child", consolidate.run, C1)
    run("consolidate", consolidate.run, "i0001")
    run("consolidate again", consolidate.run, "i0001")
    run("check consolidate absent", consolidate.check, "i0098")
    run("start parent 2", ops.start, "i0002")
    commit(wt("i0002"), "start")
    git(root, "worktree", "add", "-q", "-b", C3, os.path.join(".claude", "worktrees", C3), "i0002")
    wap("wap c3 not started", C3)
    run("start c3", ops.start, C3)
    put(os.path.join(wt(C3), "src", "hot", "x.py"), "x = 1\n")
    wap("wap c3 dirty", C3)
    put(os.path.join(wt(C3), "wip", "handoff", "learnings", C3 + ".md"), c["record3"])
    commit(wt(C3), "work c3")
    put(os.path.join(wt(C3), "docs", "out.md"), "out\n")
    commit(wt(C3), "outside")
    wap("wap c3 outside", C3)
    git(wt(C3), "rm", "-q", "docs/out.md")
    commit(wt(C3), "back")
    put(os.path.join(wt(C3), "src", "cache.txt"), "child\n")
    git(wt(C3), "add", "-f", "src/cache.txt")
    commit(wt(C3), "cache")
    exclude = git(wt("i0002"), "rev-parse", "--git-path", "info/exclude").strip()
    put(os.path.join(wt("i0002"), exclude), "src/cache.txt\n")
    put(os.path.join(wt("i0002"), "src", "cache.txt"), "parent\n")
    wap("wap c3 ignored", C3)
    os.remove(os.path.join(wt("i0002"), "src", "cache.txt"))
    run("risk c3", ops.record_risk, C3, "untested", "yes", "テストが無い")
    wap("wap c3", C3)
    merge("i0002", C3)
    run("finish c3", ops.finish, C3)
    commit(wt("i0002"), "finish c3")
    run("finish parent 2", ops.finish, "i0002")
    run("check finish parent 2", ops.check, "finish", "i0002", "")
    run("start parent 4", ops.start, "i0004")
    commit(wt("i0004"), "start")
    git(root, "worktree", "add", "-q", "-b", C4, os.path.join(".claude", "worktrees", C4), "i0004")
    run("start c4", ops.start, C4)
    put(os.path.join(wt(C4), "src", "b.py"), "b = 1\n")
    put(os.path.join(wt(C4), "wip", "handoff", "learnings", C4 + ".md"), c["record4"])
    commit(wt(C4), "work c4")
    run("risk c4", ops.record_risk, C4, "untested", "no", "r")
    wap("wap c4", C4)
    merge("i0004", C4)
    run("finish c4", ops.finish, C4)
    commit(wt("i0004"), "finish c4")
    run("check finish parent 4", ops.check, "finish", "i0004", "")
    run("finish parent 4", ops.finish, "i0004")
    run("finish parent 4 again", ops.finish, "i0004")
out["texts"] = texts()
out["shape"] = shape(root)
json.dump(out, sys.stdout, ensure_ascii=False)
`

func TestMatchesPython(t *testing.T) {
	for _, kv := range [][2]string{
		{"GIT_CONFIG_GLOBAL", os.DevNull}, {"GIT_CONFIG_NOSYSTEM", "1"},
		{"GIT_AUTHOR_NAME", "t"}, {"GIT_AUTHOR_EMAIL", "t@example.com"},
		{"GIT_COMMITTER_NAME", "t"}, {"GIT_COMMITTER_EMAIL", "t@example.com"},
		{"GIT_AUTHOR_DATE", "2026-01-05T00:00:00+0000"}, {"GIT_COMMITTER_DATE", "2026-01-05T00:00:00+0000"},
		{env.Learning, "enable"},
	} {
		t.Setenv(kv[0], kv[1])
	}
	pyRoot, goRoot := fixture(t), fixture(t)
	in := map[string]any{"root": pyRoot, "stamp": stamp, "record1": record1, "record3": recordNone("i0002-01-01", "i0002"),
		"record4": recordNone("i0004-01-01", "i0004")}
	want := pyoracle.Answers[map[string]any](t, "ops", gitfixture.ShapePy+oracle, in)

	root := goRoot
	restore, err := fsio.FixClock(stamp)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	conf := gitfixture.Settings(t, root)
	rel := func(s string) string { return gitfixture.Rel(root, s) }
	wt := func(name string) string { return gitfixture.Worktree(root, name) }
	got := map[string]any{}
	run := func(name string, f func(o, e *bytes.Buffer) int) {
		var o, e bytes.Buffer
		code := f(&o, &e)
		got[name] = []any{code, rel(o.String()), rel(e.String())}
	}
	start := func(name, id string) {
		run(name, func(o, e *bytes.Buffer) int { return Start(o, e, conf, root, id) })
	}
	finish := func(name, id string) {
		run(name, func(o, e *bytes.Buffer) int { return Finish(o, e, conf, root, id) })
	}
	check := func(name string, verb Verb, id string) {
		run(name, func(o, e *bytes.Buffer) int { return Check(o, e, conf, root, verb, id, "") })
	}
	recordRisk := func(name, id, factor, answer, reason string) {
		run(name, func(o, e *bytes.Buffer) int { return RecordRisk(o, e, conf, root, id, factor, answer, reason) })
	}
	cancel := func(name, id, reason string) {
		run(name, func(o, e *bytes.Buffer) int { return Cancel(o, e, conf, root, id, reason, true) })
	}
	cons := func(name, id string, f func(o, e io.Writer, conf *config.Settings, root, id string) int) {
		run(name, func(o, e *bytes.Buffer) int { return f(o, e, conf, root, id) })
	}
	wap := func(name, id string) {
		var o bytes.Buffer
		code := wapmerge.Plan(&o, conf, root, id)
		got[name] = []any{code, rel(o.String())}
	}
	merge := func(parent, child string) {
		gitfixture.Git(t, wt(parent), "merge", "--no-ff", "--no-commit", gitfixture.Head(t, wt(child)))
	}
	const c1, c2, c3, c4 = "i0001-01-01", "i0001-01-02", "i0002-01-01", "i0004-01-01"

	check("check start parent", VerbStart, "i0001")
	start("start unknown", "i0099")
	start("start child early", c1)
	start("start parent", "i0001")
	start("start parent again", "i0001")
	gitfixture.Commit(t, wt("i0001"), "start")
	gitfixture.Git(t, root, "worktree", "add", "-q", "-b", c1, filepath.Join(".claude", "worktrees", c1), "i0001")
	start("start c1", c1)
	gitfixture.Put(t, filepath.Join(wt(c1), "src", "a.py"), "a = 1\n")
	gitfixture.Put(t, filepath.Join(wt(c1), "wip", "handoff", "learnings", c1+".md"), record1)
	gitfixture.Commit(t, wt(c1), "work c1")
	check("check finish c1", VerbFinish, c1)
	recordRisk("risk bad answer", c1, "untested", "maybe", "r")
	recordRisk("risk no reason", c1, "untested", "yes", "  ")
	recordRisk("risk parent", "i0001", "untested", "yes", "r")
	recordRisk("risk not judge", c1, "hot", "yes", "r")
	wap("wap parent", "i0001")
	finish("finish c1 pending", c1)
	recordRisk("risk c1", c1, "untested", " NO ", "テストを足した")
	wap("wap c1", c1)
	merge("i0001", c1)
	finish("finish c1", c1)
	gitfixture.Commit(t, wt("i0001"), "finish c1")
	check("check cancel c2", VerbCancel, c2)
	cancel("cancel c2 no reason", c2, " ")
	cancel("cancel todo", "i0003", "r")
	cancel("cancel c2", c2, " やめた ")
	gitfixture.Commit(t, wt("i0001"), "cancel c2")
	finish("finish parent learning", "i0001")
	cons("check consolidate", "i0001", consolidate.Check)
	cons("consolidate child", c1, consolidate.Run)
	cons("consolidate", "i0001", consolidate.Run)
	cons("consolidate again", "i0001", consolidate.Run)
	cons("check consolidate absent", "i0098", consolidate.Check)
	start("start parent 2", "i0002")
	gitfixture.Commit(t, wt("i0002"), "start")
	gitfixture.Git(t, root, "worktree", "add", "-q", "-b", c3, filepath.Join(".claude", "worktrees", c3), "i0002")
	wap("wap c3 not started", c3)
	start("start c3", c3)
	gitfixture.Put(t, filepath.Join(wt(c3), "src", "hot", "x.py"), "x = 1\n")
	wap("wap c3 dirty", c3)
	gitfixture.Put(t, filepath.Join(wt(c3), "wip", "handoff", "learnings", c3+".md"), recordNone(c3, "i0002"))
	gitfixture.Commit(t, wt(c3), "work c3")
	gitfixture.Put(t, filepath.Join(wt(c3), "docs", "out.md"), "out\n")
	gitfixture.Commit(t, wt(c3), "outside")
	wap("wap c3 outside", c3)
	gitfixture.Git(t, wt(c3), "rm", "-q", "docs/out.md")
	gitfixture.Commit(t, wt(c3), "back")
	gitfixture.Put(t, filepath.Join(wt(c3), "src", "cache.txt"), "child\n")
	gitfixture.Git(t, wt(c3), "add", "-f", "src/cache.txt")
	gitfixture.Commit(t, wt(c3), "cache")
	exclude := strings.TrimSpace(gitfixture.Git(t, wt("i0002"), "rev-parse", "--git-path", "info/exclude"))
	if !filepath.IsAbs(exclude) {
		exclude = filepath.Join(wt("i0002"), exclude)
	}
	gitfixture.Put(t, exclude, "src/cache.txt\n")
	gitfixture.Put(t, filepath.Join(wt("i0002"), "src", "cache.txt"), "parent\n")
	wap("wap c3 ignored", c3)
	os.Remove(filepath.Join(wt("i0002"), "src", "cache.txt"))
	recordRisk("risk c3", c3, "untested", "yes", "テストが無い")
	wap("wap c3", c3)
	merge("i0002", c3)
	finish("finish c3", c3)
	gitfixture.Commit(t, wt("i0002"), "finish c3")
	finish("finish parent 2", "i0002")
	check("check finish parent 2", VerbFinish, "i0002")
	start("start parent 4", "i0004")
	gitfixture.Commit(t, wt("i0004"), "start")
	gitfixture.Git(t, root, "worktree", "add", "-q", "-b", c4, filepath.Join(".claude", "worktrees", c4), "i0004")
	start("start c4", c4)
	gitfixture.Put(t, filepath.Join(wt(c4), "src", "b.py"), "b = 1\n")
	gitfixture.Put(t, filepath.Join(wt(c4), "wip", "handoff", "learnings", c4+".md"), recordNone(c4, "i0004"))
	gitfixture.Commit(t, wt(c4), "work c4")
	recordRisk("risk c4", c4, "untested", "no", "r")
	wap("wap c4", c4)
	merge("i0004", c4)
	finish("finish c4", c4)
	gitfixture.Commit(t, wt("i0004"), "finish c4")
	check("check finish parent 4", VerbFinish, "i0004")
	finish("finish parent 4", "i0004")
	finish("finish parent 4 again", "i0004")
	got["texts"] = fileTexts(t, root)
	got["shape"] = gitfixture.Shape(t, root)
	compare(t, got, want)
}

// fileTexts は承認済みの置き場・wip/・判定の問いのファイルの中身（root は <root>）。
func fileTexts(t *testing.T, root string) map[string]any {
	t.Helper()
	found := map[string]any{}
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if d.Name() == ".git" {
				return filepath.SkipDir
			}
			return nil
		}
		r, _ := filepath.Rel(root, path)
		r = filepath.ToSlash(r)
		if strings.Contains("/"+r, "/.ccnavi/approved/") || strings.HasPrefix(r, "wip/") || strings.Contains(r, "/wip/") ||
			strings.HasPrefix(r, "logs/state/risk-judge-") {
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			found[r] = gitfixture.Rel(root, string(data))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func compare(t *testing.T, got, want map[string]any) {
	t.Helper()
	data, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	var norm map[string]any
	if err := json.Unmarshal(data, &norm); err != nil {
		t.Fatal(err)
	}
	keys := make([]string, 0, len(want))
	for key := range want {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	for _, key := range keys {
		if g := norm[key]; !reflect.DeepEqual(g, want[key]) {
			a, _ := json.MarshalIndent(g, "", " ")
			b, _ := json.MarshalIndent(want[key], "", " ")
			t.Errorf("%s が Python と違う\nGo:\n%s\nPython:\n%s", key, a, b)
		}
	}
	for key := range norm {
		if _, ok := want[key]; !ok {
			t.Errorf("%s が Python の答えに無い", key)
		}
	}
}
