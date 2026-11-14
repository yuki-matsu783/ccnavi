package decide

import (
	"bufio"
	"bytes"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/review/request"
	"ccnavi/internal/tickets/testkit"
)

// 依頼から決め方までを、ワークスペースを作って同じ順で打たせ、終了コード・標準出力・
// 標準エラー・置いたファイル（マーカー・下書き・承認済みチケット）が、記録した答え（testdata/flow.json）と
// 1 字も違わないことを見る。順は
// prepare --check → prepare → requested → prepare（依頼済み）→ 依頼のマーカーをコミット → decide --check →
// preview（変更要求・ふつう）→ yes（古いダイジェスト・選択がそろわない・keep と issue）→ prepare（レビュー済み）→
// wip/ の片付け（ready の前提と RemoveWip）。

const fixedClock = "2026-01-06T03:04:05+0900"

const phasesCopy = "phases:\n  work:\n    kind: work\n    title: 作業\n    review: true\n    when: 作業の案内\n    scope:\n    - src/*\n    - wip/*\n"

// flowWorkspace はワークスペース・origin・親 i0001 のワークツリーを作り、フェーズ 1 の子をレビュー待ちに置いて
// 親のブランチを push する。子の merged_sha は親のブランチの作業のコミット。
func flowWorkspace(t *testing.T) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(root, ".gitignore"), "/logs\n/remote.git\n/.ccnavi/messages\n/body.md\n")
	testkit.Git(t, root, "add", ".gitignore")
	testkit.Git(t, root, "commit", "-q", "-m", "init")
	testkit.CopyMessages(t, root)
	testkit.Git(t, root, "init", "-q", "--bare", filepath.Join(root, "remote.git"))
	testkit.Git(t, root, "remote", "add", "origin", filepath.Join(root, "remote.git"))
	testkit.Git(t, root, "worktree", "add", "-q", "-b", "i0001", filepath.Join(".claude", "worktrees", "i0001"), "main")
	tree := filepath.Join(root, ".claude", "worktrees", "i0001")
	testkit.Put(t, filepath.Join(tree, "src", "a.go"), "package a\n")
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "child work")
	merged := strings.TrimSpace(testkit.Git(t, tree, "rev-parse", "HEAD"))
	testkit.Put(t, filepath.Join(tree, ".ccnavi", "approved", "doing", "i0001.md"), "---\nversion: 1\nticket: i0001\nmr: 1\nissue: 1\n"+
		phasesCopy+"plan:\n  - work\nhuman_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n"+
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\nstarted_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n")
	testkit.Put(t, filepath.Join(tree, "wip", "proposals", "review", "i0001-01-01.md"), "---\nversion: 1\nticket: i0001-01-01\n"+
		"parent: i0001\nphase: 1\nhuman_review:\n  required: true\n  reason: t\ntitle: 子\nrationale: r\nallow:\n"+
		"  - match: Write|Edit\n    glob: \"src/*\"\ncompleted_at: \"2026-01-03T00:00:00+0900\"\nfinished_to: \"review\"\n"+
		"merged_sha: \""+merged+"\"\n---\n\n本文\n")
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "tickets")
	testkit.Git(t, tree, "push", "-q", "-u", "origin", "i0001")
	testkit.Put(t, filepath.Join(root, "body.md"), "依頼の本文\n")
	threads := `[{"id": "d1", "url": "u1", "path": "src/a.go", "line": 1, "body": "指摘 1", "last_id": "c1"},` +
		`{"id": "d2", "url": "u2", "path": "", "line": 0, "body": "指摘 2\n2 行目", "last_id": "c2"},` +
		`{"id": "d3", "url": "u3", "resolved": true, "body": "済み"}]`
	testkit.Put(t, filepath.Join(root, "logs", "posted.json"), `{"host": "github", "mr": {"number": 7, "url": "https://h/pr/7"}, `+
		`"url": "https://h/pr/7#c1", "created_at": "2026-01-05T01:00:00Z", "author": "bot", "threads": [], "reviews": []}`)
	testkit.Put(t, filepath.Join(root, "logs", "threads.json"), `{"host": "github", "mr": {"number": 7, "url": "https://h/pr/7"}, `+
		`"threads": `+threads+`, "reviews": []}`)
	testkit.Put(t, filepath.Join(root, "logs", "changes.json"), `{"host": "github", "mr": {"number": 7, "url": "https://h/pr/7"}, `+
		`"threads": [], "reviews": [{"state": "CHANGES_REQUESTED", "url": "r/1", "submitted_at": "2026-01-05T02:00:00Z", "author": "rev"}]}`)
	return root
}

type flowAnswers struct {
	Out     [][]any           `json:"out"`
	Merge   [][]string        `json:"merge"`
	Removed []any             `json:"removed"`
	Files   map[string]string `json:"files"`
}

func TestFlowMatchesGolden(t *testing.T) {
	golang := flowWorkspace(t)
	// 見せたあとに本文が変わった指摘のダイジェスト（親・フェーズ・MR は同じ）。
	stale := Digest(decisionOf([]host.Thread{thread("u1", "前の本文")}, 1))
	want := golden.Load[flowAnswers](t, "flow")
	root := golang
	rel := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
	tree := filepath.Join(root, ".claude", "worktrees", "i0001")
	logs := filepath.Join(root, "logs")
	body := filepath.Join(root, "body.md")
	conf, _ := config.Load(root)
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	var out [][]any
	run := func(name string, f func(stdout, stderr *bytes.Buffer) int) string {
		var o, e bytes.Buffer
		code := f(&o, &e)
		out = append(out, []any{name, float64(code), rel(o.String()), rel(e.String())})
		return o.String()
	}
	noErr := func(code int, err error) int {
		if err != nil {
			t.Fatal(err)
		}
		return code
	}
	run("check", func(o, e *bytes.Buffer) int { return request.Check(o, e, conf, root, tree, 1, body) })
	path := strings.TrimSpace(run("prepare", func(o, e *bytes.Buffer) int { return request.Prepare(o, e, conf, root, tree, 1, body) }))
	data, _ := os.ReadFile(path)
	out = append(out, []any{"request-file", float64(0), rel(string(data)), ""})
	run("requested", func(o, e *bytes.Buffer) int {
		return noErr(request.Requested(o, e, conf, root, tree, 1, filepath.Join(logs, "posted.json")))
	})
	run("again", func(o, e *bytes.Buffer) int { return request.Prepare(o, e, conf, root, tree, 1, body) })
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "requested")
	testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	run("decide-check", func(o, e *bytes.Buffer) int { return Check(o, e, conf, root, tree, 1, "", "") })
	run("decide-check-bad", func(o, e *bytes.Buffer) int { return Check(o, e, conf, root, tree, 1, "{", "v2.x") })
	run("changes", func(o, e *bytes.Buffer) int {
		return Preview(o, e, conf, root, tree, 1, filepath.Join(logs, "changes.json"))
	})
	threads := filepath.Join(logs, "threads.json")
	shown := run("preview", func(o, e *bytes.Buffer) int { return Preview(o, e, conf, root, tree, 1, threads) })
	value, err := pyjson.Loads(shown)
	if err != nil {
		t.Fatalf("preview: %v %q", err, shown)
	}
	digest := value.(*pyjson.Object).String("digest")
	yes := func(choices, digest string) func(o, e *bytes.Buffer) int {
		return func(o, e *bytes.Buffer) int {
			return noErr(Yes(o, e, conf, root, tree, 1, threads, choices, digest, YesOptions{}))
		}
	}
	run("stale", yes(`{"u1": "keep"}`, stale))
	run("uneven", yes(`{"u1": "keep"}`, digest))
	run("yes", yes(`{"u1": "fix", "u2": "issue"}`, digest))
	run("reviewed", func(o, e *bytes.Buffer) int { return request.Prepare(o, e, conf, root, tree, 1, body) })
	testkit.Put(t, filepath.Join(tree, "wip", "design", "x.html"), "<p>x</p>\n")
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "wip")
	testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	testkit.Put(t, filepath.Join(tree, "wip", "u.txt"), "u\n")
	relAll := func(list []string) []string {
		out := []string{}
		for _, s := range list {
			out = append(out, rel(s))
		}
		return out
	}
	merge := [][]string{relAll(request.MergeProblems(conf, root, tree))}
	if err := os.Remove(filepath.Join(tree, "wip", "u.txt")); err != nil {
		t.Fatal(err)
	}
	merge = append(merge, relAll(request.MergeProblems(conf, root, tree)))
	removed, head, failed := request.RemoveWip(tree)
	removedAny := []any{}
	for _, r := range removed {
		removedAny = append(removedAny, r)
	}
	files := map[string]string{}
	for _, base := range []string{filepath.Join(tree, ".ccnavi"), filepath.Join(tree, "wip"), filepath.Join(logs, "state")} {
		_ = filepath.WalkDir(base, func(path string, d fs.DirEntry, err error) error {
			if err != nil || d.IsDir() {
				return nil
			}
			data, _ := os.ReadFile(path)
			files[filepath.ToSlash(rel(path))] = rel(string(data))
			return nil
		})
	}
	got := flowAnswers{Out: out, Merge: merge, Removed: []any{removedAny, head, failed}, Files: files}
	for i := range max(len(got.Out), len(want.Out)) {
		if i >= len(got.Out) || i >= len(want.Out) || !reflect.DeepEqual(got.Out[i], want.Out[i]) {
			var g, w any
			if i < len(got.Out) {
				g = got.Out[i]
			}
			if i < len(want.Out) {
				w = want.Out[i]
			}
			t.Errorf("%d 番目が違う\n go: %q\n py: %q", i, g, w)
		}
	}
	if !reflect.DeepEqual(got.Merge, want.Merge) {
		t.Errorf("ready の前提が違う\n go: %q\n py: %q", got.Merge, want.Merge)
	}
	if !reflect.DeepEqual(got.Removed, want.Removed) {
		t.Errorf("wip/ の片付けが違う\n go: %q\n py: %q", got.Removed, want.Removed)
	}
	for path := range mergeKeys(got.Files, want.Files) {
		if got.Files[path] != want.Files[path] {
			t.Errorf("%s が違う\n go: %q\n py: %q", path, got.Files[path], want.Files[path])
		}
	}
}

// Reviewed と Choose はターミナルで 1 件ずつ選ばせる。決めなければ何も置かない。
func TestAskStopsOnAnUnknownAnswer(t *testing.T) {
	d := decisionOf(nil, 1)
	d.Unresolved = append(d.Unresolved, thread("u1", "x"), thread("u2", "y"))
	var stdout, stderr bytes.Buffer
	choices, ok := ask(bufio.NewReader(strings.NewReader(" K \nx\n")), &stdout, &stderr, d)
	if ok || choices != nil || stderr.String() != msgUndecided {
		t.Errorf("%v %q", ok, stderr.String())
	}
	choices, ok = ask(bufio.NewReader(strings.NewReader("k\nI\n")), &stdout, &stderr, d)
	if text, _ := pyjson.Dumps(choices, pyjson.Options{}); !ok || text != `{"u1": "keep", "u2": "issue"}` {
		t.Errorf("%v %s", ok, text)
	}
	if !strings.Contains(stdout.String(), "[2/2] u2 a.py:3 y\n選択（k / f / i）: ") {
		t.Errorf("%q", stdout.String())
	}
}

func mergeKeys(a, b map[string]string) map[string]bool {
	out := map[string]bool{}
	for k := range a {
		out[k] = true
	}
	for k := range b {
		out[k] = true
	}
	return out
}

// requestedWorkspace はフェーズ 1 の依頼を済ませ、依頼のマーカーをコミットして push したワークスペース。
func requestedWorkspace(t *testing.T) (root, tree string, conf *config.Settings) {
	t.Helper()
	root = flowWorkspace(t)
	tree = filepath.Join(root, ".claude", "worktrees", "i0001")
	conf, _ = config.Load(root)
	var o, e bytes.Buffer
	if code := request.Prepare(&o, &e, conf, root, tree, 1, filepath.Join(root, "body.md")); code != 0 {
		t.Fatalf("prepare: %s", e.String())
	}
	if code, err := request.Requested(&o, &e, conf, root, tree, 1, filepath.Join(root, "logs", "posted.json")); code != 0 || err != nil {
		t.Fatalf("requested: %s", e.String())
	}
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "requested")
	testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	return root, tree, conf
}

// 受け入れる指摘には置く前に sh が返信して解決する。計画の段階（PlanOut）は判断を操作記録に保存して計画を書くだけで、
// 解決したあとの段階（PlanDone）で置く。解決済みになった指摘も見せた指摘として数え直し、操作記録に適用を残す。
func TestYesInThreeSteps(t *testing.T) {
	root, tree, conf := requestedWorkspace(t)
	threads := filepath.Join(root, "logs", "threads.json")
	var o, e bytes.Buffer
	if Preview(&o, &e, conf, root, tree, 1, threads) != 0 {
		t.Fatal(e.String())
	}
	value, _ := pyjson.Loads(o.String())
	digest := value.(*pyjson.Object).String("digest")
	plan := filepath.Join(root, "logs", "plan.json")
	choices := `{"u1": "keep", "u2": "fix"}`
	o.Reset()
	if code, _ := Yes(&o, &e, conf, root, tree, 1, threads, choices, digest, YesOptions{PlanOut: plan}); code != 0 || o.Len() != 0 {
		t.Fatalf("計画の段階: %d %q %q", code, o.String(), e.String())
	}
	planned := host.ReadAcceptPlan(plan)
	if planned == nil || !reflect.DeepEqual(planned.Keys, []string{"u1"}) || planned.Op == "" {
		t.Fatalf("計画: %+v", planned)
	}
	if _, err := os.Stat(filepath.Join(tree, ".ccnavi", "approved", "phases", "i0001", "1.reviewed")); err == nil {
		t.Fatal("計画の段階で置いた")
	}
	// 解決する前の結果では、受け入れが済んでいないと言って置かない。
	if code, _ := Yes(&o, &e, conf, root, tree, 1, threads, choices, digest, YesOptions{PlanDone: plan}); code == 0 ||
		!strings.Contains(e.String(), "がホストで解決済みになっていない") {
		t.Fatalf("解決の前: %q", e.String())
	}
	resolved := filepath.Join(root, "logs", "resolved.json")
	data, _ := os.ReadFile(threads)
	testkit.Put(t, resolved, strings.Replace(string(data), `"id": "d1", "url": "u1",`, `"id": "d1", "url": "u1", "resolved": true,`, 1))
	o.Reset()
	e.Reset()
	if code, err := Yes(&o, &e, conf, root, tree, 1, resolved, choices, digest, YesOptions{PlanDone: plan}); code != 0 || err != nil {
		t.Fatalf("置く段階: %q", e.String())
	}
	answer, _ := pyjson.Loads(o.String())
	if got := answer.(*pyjson.Object); got.String("op") != planned.Op || got.String("followup") != "i0001-01-02" {
		t.Errorf("答え: %s", o.String())
	}
	record, err := oplog.Load(conf.State, planned.Op)
	if err != nil || record == nil || record.Applied.Value("local") != true || record.Applied.Value("followup") != "i0001-01-02" {
		t.Errorf("操作記録: %+v %v", record, err)
	}
}

// ホストに書いたあとに見せた指摘が変わったら置かず、返信・解決を済ませた分だけを操作記録に残す。
func TestYesMismatchAfterTheHostKeepsTheReplies(t *testing.T) {
	root, tree, conf := requestedWorkspace(t)
	threads := filepath.Join(root, "logs", "threads.json")
	var o, e bytes.Buffer
	Preview(&o, &e, conf, root, tree, 1, threads)
	value, _ := pyjson.Loads(o.String())
	digest := value.(*pyjson.Object).String("digest")
	plan := filepath.Join(root, "logs", "plan.json")
	choices := `{"u1": "keep", "u2": "keep"}`
	if code, _ := Yes(&o, &e, conf, root, tree, 1, threads, choices, digest, YesOptions{PlanOut: plan}); code != 0 {
		t.Fatal(e.String())
	}
	planned := host.ReadAcceptPlan(plan)
	changed := filepath.Join(root, "logs", "changed.json")
	data, _ := os.ReadFile(threads)
	testkit.Put(t, changed, strings.Replace(string(data), `"body": "指摘 1"`, `"body": "書き換えた指摘"`, 1))
	o.Reset()
	if code, _ := Yes(&o, &e, conf, root, tree, 1, changed, choices, digest, YesOptions{PlanDone: plan}); code == 0 ||
		!strings.Contains(o.String(), `"mismatch": true`) || !strings.Contains(o.String(), `"changed": ["u1"]`) {
		t.Fatalf("%q %q", o.String(), e.String())
	}
	record, _ := oplog.Load(conf.State, planned.Op)
	if text, _ := pyjson.Dumps(record.Applied.Value(appliedOnHost), pyjson.Options{}); text != `["u1", "u2"]` {
		t.Errorf("操作記録: %s", text)
	}
}
