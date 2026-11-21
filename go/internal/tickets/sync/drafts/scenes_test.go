package drafts

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/proposal/templates"
	"ccnavi/internal/tickets/testkit"
)

// 作成の断り・取り込み先の決まり方・保存される欄・一覧のまとめ方のうち、TestMatchesGolden の 1 つのワークスペースに
// 収まらない場面。場面ごとにワークスペースを作り直し、答えと、場面が書いたファイル（下書きの本文）を見本と比べる。

type scene struct {
	Name    string            `json:"name"`
	Out     string            `json:"out"`
	Written map[string]string `json:"written"`
}

// sceneRoot は git のワークスペース（ワークツリー i0001 つき）に、TestMatchesGolden と同じ定義を置く。
func sceneRoot(t *testing.T) (string, *config.Settings) {
	t.Helper()
	clearEnv(t)
	root := testkit.Workspace(t, "i0001")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "phases.yml"), phasesText)
	conf, _ := config.Load(root)
	return root, conf
}

// plain は dumped と同じ形で、日本語をそのまま書く（見本を読んで確かめやすい）。
func plain(t *testing.T, v any, root string) string {
	t.Helper()
	text, err := pyjson.Dumps(v, pyjson.Options{Indent: 1, RawUnicode: true})
	if err != nil {
		t.Fatal(err)
	}
	return strings.ReplaceAll(text, root, "ROOT")
}

// putDone は親 id（と、計画の n 番目ごとの子 1 本）を tree の done/ に置く。
func putDone(t *testing.T, tree, id, plan string) {
	t.Helper()
	text := strings.Replace(parent(id, ""), "plan:\n  - design\n  - {type: implement, after: [1]}\n", plan, 1)
	testkit.Put(t, filepath.Join(tree, doneDir, id+".md"), text)
	testkit.Put(t, filepath.Join(tree, doneDir, id+"-01-01.md"), child(id+"-01-01", "1"))
}

// written は root の下の wip/handoff/ に在る下書きの本文。
func written(t *testing.T, root string) map[string]string {
	t.Helper()
	out := map[string]string{}
	for _, base := range []string{root, testkit.Worktree(root, "i0001")} {
		_ = filepath.WalkDir(filepath.Join(base, "wip", "handoff"), func(path string, d os.DirEntry, err error) error {
			if err == nil && !d.IsDir() {
				raw, _ := os.ReadFile(path)
				rel, _ := filepath.Rel(root, path)
				out[filepath.ToSlash(rel)] = string(raw)
			}
			return nil
		})
	}
	return out
}

// 置き場（wip/handoff/）がリンクのとき、リンクの先へ書かない。リンクを作れない機械（Windows の開発者モード無し）ではこの 1 本だけ飛ばす。
func TestLinkedHandoffNotFollowed(t *testing.T) {
	root, conf := sceneRoot(t)
	outside := filepath.Join(root, "outside")
	if err := os.MkdirAll(outside, 0o755); err != nil {
		t.Fatal(err)
	}
	putDone(t, root, "i0011", "plan:\n  - design\n")
	if err := os.MkdirAll(filepath.Join(root, "wip"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "wip", "handoff")); err != nil {
		t.Skip("リンクを作れない:", err)
	}
	m, why := Make(conf, root, Request{Parent: "i0011"})
	golden.Check(t, "scenes-link", plain(t, AnswerJSON(m, why), root))
	if entries, _ := os.ReadDir(outside); len(entries) != 0 {
		t.Errorf("リンクの先に書いた: %v", entries)
	}
}

func TestScenesMatchGolden(t *testing.T) {
	const plan = "plan:\n  - {type: design, deferred: true}\n  - {type: implement, after: [1], review: true}\n"
	var got []scene
	add := func(name string, root string, out string) {
		got = append(got, scene{Name: name, Out: out, Written: written(t, root)})
	}
	make1 := func(t *testing.T, conf *config.Settings, root string, req Request) string {
		m, why := Make(conf, root, req)
		tmpl := ""
		if m != nil && m.Template != nil {
			tmpl = templates.Dump(m.Template)
		}
		return plain(t, AnswerJSON(m, why).With("template", tmpl), root)
	}

	// ready で閉じて退避済みの親は読まない。
	root, conf := sceneRoot(t)
	testkit.Put(t, filepath.Join(root, "logs", "archive", "self", "done", "i0010.md"), parent("i0010", ""))
	add("退避済みの親", root, make1(t, conf, root, Request{Parent: "i0010"}))

	// deferred と review は下書きに残る。
	root, conf = sceneRoot(t)
	putDone(t, root, "i0012", plan)
	add("deferred と review の保存", root, make1(t, conf, root, Request{Parent: "i0012"}))

	// --layer は親のツリーの中のプロジェクトだけ。外のプロジェクト（親がワークツリーにあるとき）は断る。
	root, conf = sceneRoot(t)
	if err := os.MkdirAll(filepath.Join(root, "projects", "lib", ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	putDone(t, root, "i0013", "plan:\n  - design\n")
	add("--layer の成功", root, make1(t, conf, root, Request{Parent: "i0013", Layer: "lib"}))
	root, conf = sceneRoot(t)
	if err := os.MkdirAll(filepath.Join(root, "projects", "lib", ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	putDone(t, testkit.Worktree(root, "i0001"), "i0014", "plan:\n  - design\n")
	add("--layer が親のツリーの外", root, make1(t, conf, root, Request{Parent: "i0014", Layer: "lib"}))

	// 同じ下書きが 2 つのツリーにあれば 1 件にまとめ、ほかのツリーを also_in に言う。
	root, conf = sceneRoot(t)
	putDone(t, root, "i0015", "plan:\n  - design\n")
	make1(t, conf, root, Request{Parent: "i0015"})
	rel := filepath.Join("wip", "handoff", ".ccnavi", "config", "project", "templates", "i0015.yml")
	raw, err := os.ReadFile(filepath.Join(root, rel))
	if err != nil {
		t.Fatal(err)
	}
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0001"), rel), string(raw))
	scanned := templates.Load(conf, root)
	found := Scan(conf, root, &scanned.Catalog)
	rows := []any{}
	for _, d := range found.Drafts {
		rows = append(rows, DraftJSON(d, scanned))
	}
	add("同じ下書きが 2 つのツリーにある", root, plain(t, pyjson.NewObject("drafts", rows), root))

	golden.Check(t, "drafts-scenes", got)
}
