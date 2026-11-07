package closeout

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
	"ccnavi/internal/tickets/lifecycle/ops"
	"ccnavi/internal/tickets/testkit"
)

// 早めに閉じてから Draft を外すまでを、同じ形のワークスペースを 2 つ作って Python と Go に同じ順で打たせ、終了
// コード・標準出力・標準エラー・置いたファイル（承認済みの領域・wip/・state・退避）が 1 字も違わないことを見る。
// 順は ready --check と ready（子が開いている）→ close-early（理由なし・変更要求・n・計画・計画の照合 2 つ・
// 解決前・解決後）→ 親の finish → ready --check → ready → 退避をコミット → ready（打ち直し）→ ready（Draft が
// 外れた結果）→ ready --check（打ち直し）。

const fixedClock = "2026-01-06T03:04:05+0900"

const phasesCopy = "phases:\n  work:\n    kind: work\n    title: 作業\n    review: true\n    when: 作業の案内\n    scope:\n    - src/*\n    - wip/*\n" +
	"  impl:\n    kind: work\n    title: 実装\n    review: true\n    when: 実装の案内\n    scope:\n    - src/*\n"

func childText(id string, phase int, extra string) string {
	return "---\nversion: 1\nticket: " + id + "\nparent: i0001\nphase: " + string(rune('0'+phase)) +
		"\nhuman_review:\n  required: true\n  reason: t\ntitle: 子 " + id + "\nrationale: r\nallow:\n" +
		"  - match: Write|Edit\n    glob: \"src/*\"\n" + extra + "---\n\n本文\n"
}

// workspace はワークスペース・origin・親 i0001 のワークツリーを作る。計画は work・impl の 2 フェーズで、フェーズ 1
// の子はレビュー待ち、フェーズ 2 の子は承認したが未着手。フィードバック計画は無い。親のブランチは push 済み。
func workspace(t *testing.T) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(root, ".gitignore"), "/logs\n/remote.git\n/.ccnavi/messages\n")
	testkit.Git(t, root, "add", ".gitignore")
	testkit.Git(t, root, "commit", "-q", "-m", "init")
	testkit.CopyMessages(t, root)
	testkit.Git(t, root, "init", "-q", "--bare", filepath.Join(root, "remote.git"))
	testkit.Git(t, root, "remote", "add", "origin", filepath.Join(root, "remote.git"))
	testkit.Git(t, root, "worktree", "add", "-q", "-b", "i0001", filepath.Join(".claude", "worktrees", "i0001"), "main")
	tree := filepath.Join(root, ".claude", "worktrees", "i0001")
	testkit.Put(t, filepath.Join(tree, "src", "a.go"), "package a\n")
	testkit.Put(t, filepath.Join(tree, "wip", "design", "plan.md"), "d\n")
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "child work")
	merged := strings.TrimSpace(testkit.Git(t, tree, "rev-parse", "HEAD"))
	approved := filepath.Join(tree, ".ccnavi", "approved")
	testkit.Put(t, filepath.Join(approved, "doing", "i0001.md"), "---\nversion: 1\nticket: i0001\nmr: 7\nissue: 1\n"+
		phasesCopy+"plan:\n  - work\n  - impl\nhuman_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n"+
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\nstarted_at: \"2026-01-02T00:00:00+0900\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n")
	testkit.Put(t, filepath.Join(tree, "wip", "proposals", "review", "i0001-01-01.md"), childText("i0001-01-01", 1,
		"started_at: \"2026-01-02T00:00:00+0900\"\ncompleted_at: \"2026-01-03T00:00:00+0900\"\nfinished_to: \"review\"\nmerged_sha: \""+merged+"\"\n"))
	testkit.Put(t, filepath.Join(approved, "doing", "i0001-02-01.md"), childText("i0001-02-01", 2, "started_at: \"\"\ncompleted_at: \"\"\n"))
	testkit.Git(t, tree, "add", "-A")
	testkit.Git(t, tree, "commit", "-q", "-m", "tickets")
	testkit.Git(t, tree, "push", "-q", "-u", "origin", "i0001")
	mr := `"mr": {"number": 7, "url": "https://h/pr/7", "draft": true}`
	threads := `[{"id": "d1", "url": "u1", "path": "src/a.go", "line": 1, "body": "指摘 1", "last_id": "c1"},` +
		`{"id": "d2", "url": "u2", "path": "", "line": 0, "body": "指摘 2\n2 行目", "last_id": "c2"},` +
		`{"id": "d3", "url": "u3", "resolved": true, "body": "済み"}]`
	logs := filepath.Join(root, "logs")
	testkit.Put(t, filepath.Join(logs, "threads.json"), `{"host": "github", `+mr+`, "threads": `+threads+`, "reviews": []}`)
	testkit.Put(t, filepath.Join(logs, "fewer.json"), `{"host": "github", `+mr+`, "threads": [{"id": "d1", "url": "u1", "body": "指摘 1"}], "reviews": []}`)
	testkit.Put(t, filepath.Join(logs, "resolved.json"), `{"host": "github", `+mr+`, "threads": `+
		strings.ReplaceAll(threads, `"last_id"`, `"resolved": true, "last_id"`)+`, "reviews": []}`)
	testkit.Put(t, filepath.Join(logs, "changes.json"), `{"host": "github", `+mr+`, "threads": [], `+
		`"reviews": [{"state": "CHANGES_REQUESTED", "url": "r/1", "submitted_at": "2026-01-05T02:00:00Z", "author": "rev"}]}`)
	testkit.Put(t, filepath.Join(logs, "undrafted.json"), `{"host": "github", "mr": {"number": 7, "url": "https://h/pr/7", "draft": false}, "threads": [], "reviews": []}`)
	return root
}

type flowAnswers struct {
	Out   [][]any           `json:"out"`
	Files map[string]string `json:"files"`
}

func TestFlowMatchesPython(t *testing.T) {
	golang := workspace(t)
	want := golden.Load[flowAnswers](t, "flow")
	root := golang
	// ready の目印はツリーのパスを畳んで書く。
	rel := func(s string) string {
		return strings.ReplaceAll(strings.ReplaceAll(s, root, "<root>"), strings.ToLower(root), "<root>")
	}
	tree := filepath.Join(root, ".claude", "worktrees", "i0001")
	logs := filepath.Join(root, "logs")
	conf, _ := config.Load(root)
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	var out [][]any
	run := func(name string, f func(stdout, stderr *bytes.Buffer) (int, error)) {
		var o, e bytes.Buffer
		code, err := f(&o, &e)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		out = append(out, []any{name, float64(code), rel(o.String()), rel(e.String())})
	}
	plain := func(code int) (int, error) { return code, nil }
	early := func(name, answer, reason, result string, opt EarlyOptions) {
		run(name, func(o, e *bytes.Buffer) (int, error) {
			return CloseEarly(bufio.NewReader(strings.NewReader(answer)), o, e, conf, root, tree, reason, filepath.Join(logs, result), opt)
		})
	}
	commit := func(message string) {
		testkit.Git(t, tree, "add", "-A")
		testkit.Git(t, tree, "commit", "-q", "-m", message)
		testkit.Git(t, tree, "push", "-q", "origin", "i0001")
	}
	ready := func(name, result string) {
		run(name, func(o, e *bytes.Buffer) (int, error) {
			return Ready(o, e, conf, root, tree, filepath.Join(logs, result))
		})
	}
	checkReady := func(name string) {
		run(name, func(o, e *bytes.Buffer) (int, error) { return plain(CheckReady(o, e, conf, root, tree)) })
	}
	plan := filepath.Join(conf.State, "accept-plan.json")
	checkReady("check-ready")
	ready("ready-refused", "threads.json")
	early("early-noreason", "y\n", "  ", "threads.json", EarlyOptions{})
	early("early-changes", "y\n", "r", "changes.json", EarlyOptions{})
	early("early-declined", "n\n", "r", "threads.json", EarlyOptions{})
	early("early-plan", " Yes \n", " 今期はここまで ", "threads.json", EarlyOptions{PlanOut: plan})
	early("early-check", "", "今期はここまで", "threads.json", EarlyOptions{PlanCheck: plan})
	early("early-check-changed", "", "今期はここまで", "fewer.json", EarlyOptions{PlanCheck: plan})
	early("early-done-open", "", "今期はここまで", "threads.json", EarlyOptions{PlanDone: plan})
	early("early-done", "", "今期はここまで", "resolved.json", EarlyOptions{PlanDone: plan})
	commit("close early")
	run("finish", func(o, e *bytes.Buffer) (int, error) { return plain(ops.Finish(o, e, conf, root, "i0001")) })
	commit("finish")
	checkReady("check-ready-ok")
	ready("ready", "resolved.json")
	commit("ready")
	ready("ready-again", "resolved.json")
	ready("ready-undrafted", "undrafted.json")
	checkReady("check-ready-again")
	files := map[string]string{}
	for _, base := range []string{filepath.Join(tree, ".ccnavi", "approved"), filepath.Join(tree, "wip"),
		filepath.Join(logs, "state"), filepath.Join(logs, "archive")} {
		_ = filepath.WalkDir(base, func(path string, d fs.DirEntry, err error) error {
			if err != nil || d.IsDir() {
				return nil
			}
			data, _ := os.ReadFile(path)
			files[filepath.ToSlash(rel(path))] = rel(string(data))
			return nil
		})
	}
	for i := range max(len(out), len(want.Out)) {
		if i >= len(out) || i >= len(want.Out) || !reflect.DeepEqual(out[i], want.Out[i]) {
			var g, w any
			if i < len(out) {
				g = out[i]
			}
			if i < len(want.Out) {
				w = want.Out[i]
			}
			t.Errorf("%d 番目が違う\n go: %q\n py: %q", i, g, w)
		}
	}
	keys := map[string]bool{}
	for k := range files {
		keys[k] = true
	}
	for k := range want.Files {
		keys[k] = true
	}
	for path := range keys {
		if files[path] != want.Files[path] {
			t.Errorf("%s が違う\n go: %q\n py: %q", path, files[path], want.Files[path])
		}
	}
}
