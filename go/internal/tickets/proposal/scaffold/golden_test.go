package scaffold

import (
	"bytes"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// scaffold（ticket new）と revise（ticket revise）を同じ順に走らせ、標準出力・標準エラー・終了コードと、
// 書いたファイルの中身が、記録した答え（testdata/scaffold.json）と 1 字も違わないことを見る。断る場合（識別子・タイトル・issue・
// MR・組み合わせ・ワークツリーの外・定義に無い型・範囲）と、型の列・テンプレート（フィードバック計画とフローの
// 下書きを含む）から書く場合、既にあるものを上書きしない場合を並べる。

type scaffoldCase struct {
	Op       string   `json:"op"`
	Cwd      string   `json:"cwd"`
	ID       string   `json:"id"`
	Title    string   `json:"title"`
	Plan     string   `json:"plan"`
	Issue    *string  `json:"issue"`
	MR       *int     `json:"mr"`
	Template string   `json:"template"`
	Layer    string   `json:"layer"`
	Feedback bool     `json:"feedback"`
	Scope    []string `json:"scope"`
}

const scaffoldPhases = `version: 1
phases:
  research:
    kind: work
    title: 調査
    review: false
    scope: ["wip/research/*"]
  design:
    kind: work
    title: 設計
    review: true
    scope: ["wip/design/*"]
  implement:
    kind: work
    title: 実装とテスト
    review: true
    scope: ["src/*", "tests/*"]
  quick-work:
    kind: work
    title: 小さな作業
    review: true
    scope: inherit
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: true
    scope: inherit
`

const featureTemplate = `name: feature
description: 機能を設計から実装まで進める
plan:
  - research
  - {type: design, deferred: true, after: [1]}
  - {type: implement, after: [2]}
feedback:
  - implement-feedback
flows:
  3:
    id: wf-impl
    name: 実装の手順
    nodes:
      - {id: s, type: start, name: 開始, data: {}}
      - {id: p, type: prompt, name: 実装, data: {prompt: "先にテスト\n次に実装\n"}}
      - {id: e, type: end, name: 終了, data: {}}
    connections:
      - {id: c1, from: s, to: p}
      - {id: c2, from: p, to: e}
`

const kindTemplate = `name: wrongkind
description: kind が置き場と合わない
plan:
  - implement-feedback
`

const doingParent = `---
version: 1
ticket: feat-d
issue: 4
mr: 4
title: "改版する親"
plan:
  - design
  - {type: implement, after: [1]}
phases:
  design: {kind: work, title: 設計, review: true, scope: ["wip/design/*"]}
  implement: {kind: work, title: 実装とテスト, review: true, scope: ["src/*", "tests/*"]}
rationale: r
allow:
  - match: Write|Edit
    glob: "src/*"
started_at: "2026-01-05T00:00:00+0000"
base_sha: abc
---

## 目的

本文
`

var worktreeNames = []string{"feat-a", "feat-b", "feat-c", "feat-d", "feat-e"}

func scaffoldWorkspace(t *testing.T) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Git(t, root, "commit", "-q", "--allow-empty", "-m", "init")
	for _, name := range worktreeNames {
		testkit.Git(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name), "main")
	}
	testkit.CopyMessages(t, root)
	common := filepath.Join(root, ".ccnavi", "config", "common")
	testkit.Put(t, filepath.Join(common, "phases.yml"), scaffoldPhases)
	testkit.Put(t, filepath.Join(common, "templates", "feature.yml"), featureTemplate)
	testkit.Put(t, filepath.Join(common, "templates", "wrongkind.yml"), kindTemplate)
	conf, _ := config.Load(root)
	testkit.Put(t, filepath.Join(conf.ApprovedDir(filepath.Join(root, ".claude", "worktrees", "feat-d")), "doing", "feat-d.md"), doingParent)
	return root
}

func scaffoldCases() []scaffoldCase {
	issue, bad, mr, zero := "3", "x", 3, 0
	n := func(cwd, id, title, plan string) scaffoldCase {
		return scaffoldCase{Op: "new", Cwd: cwd, ID: id, Title: title, Plan: plan, Issue: &issue, MR: &mr}
	}
	with := func(c scaffoldCase, edit func(*scaffoldCase)) scaffoldCase { edit(&c); return c }
	return []scaffoldCase{
		n("feat-a", "bad id!", "t", "design"),
		n("feat-a", "feat-a-01-01", "t", "design"),
		n("feat-a", "feat-a", " ", "design"),
		n("feat-a", "feat-a", "a\nb", "design"),
		n("feat-a", "feat-a", "<タイトル>", "design"),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.Issue = nil }),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.MR = nil }),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.Issue = &bad }),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.MR = &zero }),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.Template = "feature" }),
		with(n("feat-a", "feat-a", "t", ""), func(c *scaffoldCase) { c.Layer = "self" }),
		with(n("feat-a", "feat-a", "t", ""), func(c *scaffoldCase) { c.Feedback = true }),
		n("feat-a", "feat-a", "t", " , "),
		n("", "feat-a", "t", "design"),
		n("feat-b", "feat-a", "t", "design"),
		n("feat-a", "feat-a", "t", "design,nope,other"),
		n("feat-a", "feat-a", "t", "design,implement-feedback"),
		n("feat-a", "feat-a", "t", "quick-work"),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.Scope = []string{"../x"} }),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.Scope = []string{"src/a.py src/b.py"} }),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.Scope = []string{"<触るファイルの glob>"} }),
		with(n("feat-a", "feat-a", "t", "design"), func(c *scaffoldCase) { c.Scope = []string{"C:/x"} }),
		with(n("feat-a", "feat-a", "設計と \"実装\"", "design, implement"),
			func(c *scaffoldCase) { c.Scope = []string{"docs/*,SRC/*", "docs/*"} }),
		n("feat-a", "feat-a", "t", "design"),
		with(n("feat-e", "feat-e", "t", "quick-work"), func(c *scaffoldCase) { c.Scope = []string{"src/ui/error.ts"} }),
		with(n("feat-b", "feat-b", "テンプレから", ""), func(c *scaffoldCase) { c.Template, c.Feedback = "feature", true }),
		with(n("feat-c", "feat-c", "t", ""), func(c *scaffoldCase) { c.Template = "nothing" }),
		with(n("feat-c", "feat-c", "t", ""), func(c *scaffoldCase) { c.Template = "wrongkind" }),
		with(n("feat-c", "feat-c", "t", ""), func(c *scaffoldCase) { c.Template, c.Layer = "feature", "nope" }),
		with(n("feat-c", "feat-c", "t", ""), func(c *scaffoldCase) { c.Template = "feature" }),
		{Op: "revise", Cwd: "feat-d", ID: "feat-d-01-01"},
		{Op: "revise", Cwd: "", ID: "feat-d"},
		{Op: "revise", Cwd: "feat-a", ID: "feat-a"},
		{Op: "revise", Cwd: "feat-d", ID: "feat-d", Feedback: true},
		{Op: "revise", Cwd: "feat-d", ID: "feat-d"},
		{Op: "revise", Cwd: "feat-d", ID: "feat-d"},
	}
}

func TestMatchesGolden(t *testing.T) {
	cases := scaffoldCases()
	want := golden.Load[[][3]any](t, "scaffold")

	root := scaffoldWorkspace(t)
	conf, _ := config.Load(root)
	for i, c := range cases {
		var stdout, stderr bytes.Buffer
		cwd := root
		if c.Cwd != "" {
			cwd = filepath.Join(root, ".claude", "worktrees", c.Cwd)
		}
		var code int
		if c.Op == "new" {
			code = New(&stdout, &stderr, conf, root, cwd, Request{ID: c.ID, Title: c.Title, Plan: c.Plan, Issue: c.Issue,
				MR: c.MR, Template: c.Template, Layer: c.Layer, WithFeedback: c.Feedback, Scope: c.Scope})
		} else {
			code = Revise(&stdout, &stderr, conf, root, cwd, c.ID, c.Feedback)
		}
		got := [3]any{float64(code), strings.ReplaceAll(stdout.String(), root, "<root>"),
			strings.ReplaceAll(stderr.String(), root, "<root>")}
		if got != want[i] {
			t.Errorf("%d %+v:\n%q\nwant:\n%q", i, c, got, want[i])
		}
	}
	// 書いた提案の中身は、見本の記録（testdata/scaffold-written.json）と比べる。
	golden.Check(t, "scaffold-written", written(t, root))
}

// written はワークツリーの提案の置き場（wip/）に在るファイルの中身。
func written(t *testing.T, root string) map[string]string {
	t.Helper()
	out := map[string]string{}
	base := filepath.Join(root, ".claude", "worktrees")
	err := filepath.WalkDir(base, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.Contains(filepath.ToSlash(path), "/wip/") {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(base, path)
		out[filepath.ToSlash(rel)] = string(data)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestWithFeedbackNone(t *testing.T) {
	// 閉じの --- が無い写しに、黙って feedback: [] を足して成功と言わない。
	if _, ok := withFeedbackNone("---\nticket: i0001\n"); ok {
		t.Error("閉じが無いのに足した")
	}
	if got, _ := withFeedbackNone("---\nticket: i0001\n---\n"); got != "---\nticket: i0001\nfeedback: []\n---\n" {
		t.Errorf("got %q", got)
	}
}

func TestParseScope(t *testing.T) {
	// 空白で区切った 1 項目は断り、カンマで区切ったものは 2 項目にする。
	if _, why := parseScope([]string{"src/a.py src/b.py"}); why == "" {
		t.Error("空白で区切った項目を通した")
	}
	got, why := parseScope([]string{"src/a.py,src/b.py"})
	if why != "" || len(got) != 2 || got[0] != "src/a.py" || got[1] != "src/b.py" {
		t.Errorf("got %v %q", got, why)
	}
}
