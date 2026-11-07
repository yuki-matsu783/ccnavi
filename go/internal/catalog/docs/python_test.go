package docs

import (
	"bytes"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/testkit"
)

// Python の docsearch.run と突き合わせる。リポジトリの実際の文書（.ccnavi/docs・.agents/docs・docs/）を 2 つの
// git に写し、片方を Python、片方を Go で引いて、出力と書いた索引が 1 字も違わないことを見る。

const runOracle = `
import io, json, sys
from ccnavi.catalog import docsearch, docsearch_query
from ccnavi.infra import settings
c = json.load(sys.stdin)
conf = settings.load(c["root"])[0]
out = []
for q in c["queries"]:
    o, e = io.StringIO(), io.StringIO()
    query = docsearch_query.Query(**q["query"])
    code = docsearch.run(o, e, conf, c["root"], query, refresh=q["refresh"])
    out.append({"code": code, "out": o.getvalue(), "err": e.getvalue()})
json.dump(out, sys.stdout, ensure_ascii=False)
`

type pyQuery struct {
	Types    []string `json:"types,omitempty"`
	Tags     []string `json:"tags,omitempty"`
	Keywords []string `json:"keywords,omitempty"`
	Paths    []string `json:"paths,omitempty"`
	Texts    []string `json:"texts,omitempty"`
	Since    string   `json:"since,omitempty"`
	Until    string   `json:"until,omitempty"`
	Sort     string   `json:"sort,omitempty"`
	Reverse  bool     `json:"reverse,omitempty"`
	Limit    int      `json:"limit,omitempty"`
	Format   string   `json:"format,omitempty"`
}

func (p pyQuery) query() Query {
	return Query{Types: p.Types, Tags: p.Tags, Keywords: p.Keywords, Paths: p.Paths, Texts: p.Texts,
		Since: p.Since, Until: p.Until, Sort: Sort(p.Sort), Reverse: p.Reverse, Limit: p.Limit, Format: Format(p.Format)}
}

type runCase struct {
	Query   pyQuery `json:"query"`
	Refresh bool    `json:"refresh"`
}

type runAnswer struct {
	Code int    `json:"code"`
	Out  string `json:"out"`
	Err  string `json:"err"`
}

// repoDocs はリポジトリの文書の置き場（ワークスペースルートからの相対）。
var repoDocs = []string{".ccnavi/docs", ".agents/docs", "docs"}

// copyDocs はリポジトリの文書を root に写す。更新時刻は写した順に 1 分ずつずらして固定する（mtime の並べ方と
// --since を確かめられるように）。
func copyDocs(t *testing.T, repo, root string) {
	t.Helper()
	var files []string
	for _, dir := range repoDocs {
		err := filepath.WalkDir(filepath.Join(repo, dir), func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.Type().IsRegular() && strings.HasSuffix(path, ".md") {
				files = append(files, path)
			}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	sort.Strings(files)
	start := time.Date(2026, 8, 1, 9, 0, 0, 0, time.Local)
	for i, path := range files {
		rel, _ := filepath.Rel(repo, path)
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		target := testkit.Put(t, filepath.Join(root, rel), string(data))
		when := start.Add(time.Duration(i) * time.Minute)
		if err := os.Chtimes(target, when, when); err != nil {
			t.Fatal(err)
		}
	}
}

func docsWorkspace(t *testing.T, repo string) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q")
	copyDocs(t, repo, root)
	// 1 つは .gitignore に行を足す経路を通す（ワークスペースの .gitignore は無い）。
	return root
}

// indexFiles は root の下の ccnavi-index.jsonl と .gitignore（root からの相対 → 中身）。
func indexFiles(t *testing.T, root string) map[string]string {
	t.Helper()
	found := map[string]string{}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() && d.Name() == ".git" {
			return filepath.SkipDir
		}
		if d.Name() == "ccnavi-index.jsonl" || d.Name() == ".gitignore" {
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			rel, _ := filepath.Rel(root, path)
			found[filepath.ToSlash(rel)] = string(data)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return found
}

var repoQueries = []runCase{
	{pyQuery{}, true},
	{pyQuery{Format: "path"}, true},
	{pyQuery{Format: "jsonl"}, false},
	{pyQuery{Format: "json"}, true},
	{pyQuery{Format: "detail", Sort: "title"}, true},
	{pyQuery{Format: "count", Types: []string{"guide", "ADR"}}, true},
	{pyQuery{Keywords: []string{"frontmatter", "Go"}, Format: "detail"}, true},
	{pyQuery{Tags: []string{"process-implement"}, Sort: "type", Reverse: true}, true},
	{pyQuery{Paths: []string{"ccnavi/DOCS"}, Sort: "mtime", Limit: 5}, true},
	{pyQuery{Texts: []string{"ワークツリー", "worktree"}, Format: "path"}, true},
	{pyQuery{Since: "2026-08-01T10", Until: "2026-08-01T12:30", Format: "path"}, true},
	{pyQuery{Until: "2026-08-01", Format: "count", Limit: 3}, true},
	{pyQuery{Types: []string{"learning-wiki"}}, true},
	{pyQuery{Sort: "size", Format: "xml", Since: "2026-02-30", Until: "2026-8-5"}, true},
}

func TestRunMatchesPythonOnTheRepositoryDocs(t *testing.T) {
	repo := testkit.RepoRoot(t)
	pyRoot, goRoot := docsWorkspace(t, repo), docsWorkspace(t, repo)
	want := testkit.Answers[[]runAnswer](t, "", runOracle, map[string]any{"root": pyRoot, "queries": repoQueries})
	if len(want) != len(repoQueries) || strings.Count(want[1].Out, "\n") < 100 {
		t.Fatalf("Python が索引を引けていない: %+v", want[:min(len(want), 2)])
	}
	conf := testkit.Settings(t, goRoot)
	for i, c := range repoQueries {
		var out, errOut bytes.Buffer
		code := Run(&out, &errOut, conf, goRoot, c.Query.query(), c.Refresh)
		got := runAnswer{code, out.String(), strings.ReplaceAll(errOut.String(), goRoot, pyRoot)}
		if got != want[i] {
			t.Errorf("%+v:\nGo  (%d): %.2000s\n%s\nPython (%d): %.2000s\n%s", c, got.Code, got.Out, got.Err, want[i].Code, want[i].Out, want[i].Err)
		}
	}
	pyFiles, goFiles := indexFiles(t, pyRoot), indexFiles(t, goRoot)
	if len(pyFiles) < 5 {
		t.Fatalf("索引が書かれていない: %v", pyFiles)
	}
	for rel, text := range pyFiles {
		if goFiles[rel] != text {
			t.Errorf("%s が Python と違う\nGo:\n%.1500s\nPython:\n%.1500s", rel, goFiles[rel], text)
		}
	}
	for rel := range goFiles {
		if _, ok := pyFiles[rel]; !ok {
			t.Errorf("%s は Go だけが書いた", rel)
		}
	}
}

// edgeDocs は frontmatter の値の型・幅・正規化・改行の違いを集めた見本。
var edgeDocs = []struct{ rel, text, mtime string }{
	{"a.md", doc("type: guide", `description: "1 行目\n2 行目"`, "tags: [x, y]"), "2026-08-01T00:00:00"},
	{"日本語/文書.md", doc("type: ガイド", "title: タイトル"), "2026-08-02T00:00:00"},
	{"ab.md", doc("type: adr", "title: t"), "2026-08-03T00:00:00"},
	{"docs/" + "\u304b\u3099" + ".md", doc("tags: [\u304b\u3099]", "title: \u304b\u3099"), "2026-08-03T00:00:01"},
	{"nested.md", doc("tags: {a: 1}", "type: [odd]", "keywords: [1, true, null, 1.5]", "title: 1.0"), "2026-08-04T00:00:00"},
	{"zw.md", doc("type: \"a\u200bb\"", "title: \"e\u0301t\u00e9\""), "2026-08-04T00:00:00"},
	{"wiki/l.md", doc("type: learning-wiki", "title: 材料"), "2026-08-05T00:00:00"},
	{"deep.md", doc("type: guide", "n: "+strings.Repeat("[", 40)+strings.Repeat("]", 40)), "2026-08-05T01:00:00"},
	{"dates.md", doc("type: note", "date: 2026-08-05", "when: 2026-08-05 10:00:00+09:00"), "2026-08-05T02:00:00"},
	{"crlf.md", "---\r\ntype: crlf\r\ntitle: 改行\r\n---\r\nbody\r\n", "2026-08-05T03:00:00"},
	{"bom.md", "\ufeff---\ntype: bom\n...\nbody\n", "2026-08-05T04:00:00"},
	{"Sub/UPPER.md", doc("type: GUIDE", "title: Ab", "keywords: [Go, YAML]"), "2026-08-05T05:00:00"},
	{"spaces.md", doc("type: \"guide  \"", "title: \"  前後に空白  \"", "description: \"a\\tb\\r\\nc\""), "2026-08-05T06:00:00"},
	{"x.MD", doc("type: upper-ext"), "2026-08-05T07:00:00"},
	{"none.md", "本文だけ\n", "2026-08-05T23:59:59"},
	{"projects/lib/docs/p.md", doc("type: adr", "title: lib"), "2026-08-06T00:00:00"},
	{"projects/nogit/q.md", doc("type: guide"), "2026-08-06T00:00:00"},
	{".ccnavi/docs/usage.md", doc("type: guide", "title: 使い方"), "2026-08-06T01:00:00"},
	{".ccnavi/approved/done/i0001.md", "---\nversion: 1\n---\n", "2026-08-06T02:00:00"},
}

func edgeWorkspace(t *testing.T) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q")
	lib := filepath.Join(root, "projects", "lib")
	if err := os.MkdirAll(lib, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, lib, "init", "-q")
	testkit.Put(t, filepath.Join(root, ".gitignore"), "/projects/\n")
	for _, d := range edgeDocs {
		path := testkit.Put(t, filepath.Join(root, filepath.FromSlash(d.rel)), d.text)
		when, _ := time.ParseInLocation("2006-01-02T15:04:05", d.mtime, time.Local)
		os.Chtimes(path, when, when)
	}
	return root
}

var edgeQueries = []runCase{
	{pyQuery{}, true},
	{pyQuery{}, true},
	{pyQuery{Format: "detail"}, true},
	{pyQuery{Format: "json", Sort: "title"}, true},
	{pyQuery{Format: "jsonl", Sort: "type", Reverse: true}, true},
	{pyQuery{Format: "path", Sort: "mtime"}, false},
	{pyQuery{Types: []string{"learning-wiki"}, Format: "detail"}, true},
	{pyQuery{Types: []string{"GUIDE"}, Format: "count", Limit: 2}, true},
	{pyQuery{Tags: []string{"\u304c"}, Format: "path"}, true},
	{pyQuery{Keywords: []string{"1", "True"}, Format: "path"}, true},
	{pyQuery{Keywords: []string{"go"}, Format: "path"}, true},
	{pyQuery{Texts: []string{"1.5"}, Format: "path"}, true},
	{pyQuery{Texts: []string{"true", ""}, Format: "path"}, true},
	{pyQuery{Texts: []string{"\"a\":1"}, Format: "path"}, true},
	{pyQuery{Texts: []string{"2026-08-05T10:00:00+09:00"}, Format: "path"}, true},
	{pyQuery{Paths: []string{"SUB", "projects/"}, Format: "path"}, true},
	{pyQuery{Since: "２０２６-08-05", Format: "count"}, true},
	{pyQuery{Since: "2026-08-05T03", Until: "2026-08-05T05", Format: "path"}, true},
	{pyQuery{Until: "2026-08-05T23:59:58", Format: "count"}, true},
	{pyQuery{Sort: "Path"}, true},
	{pyQuery{Since: "2026-08-05T", Until: "2026-02-29"}, true},
}

func TestRunMatchesPythonOnEdgeCases(t *testing.T) {
	pyRoot, goRoot := edgeWorkspace(t), edgeWorkspace(t)
	want := testkit.Answers[[]runAnswer](t, "", runOracle, map[string]any{"root": pyRoot, "queries": edgeQueries})
	if !strings.Contains(want[0].Out, "projects/lib/docs/p") || !strings.Contains(want[0].Err, "nogit") {
		t.Fatalf("見本が Python に届いていない: %+v", want[0])
	}
	conf := testkit.Settings(t, goRoot)
	for i, c := range edgeQueries {
		var out, errOut bytes.Buffer
		code := Run(&out, &errOut, conf, goRoot, c.Query.query(), c.Refresh)
		got := runAnswer{code, out.String(), strings.ReplaceAll(errOut.String(), goRoot, pyRoot)}
		if got != want[i] {
			t.Errorf("%d %+v:\nGo  (%d):\n%s\n%s\nPython (%d):\n%s\n%s", i, c, got.Code, got.Out, got.Err, want[i].Code, want[i].Out, want[i].Err)
		}
	}
	pyFiles, goFiles := indexFiles(t, pyRoot), indexFiles(t, goRoot)
	if len(pyFiles) < 5 {
		t.Fatalf("索引が書かれていない: %v", pyFiles)
	}
	for rel, text := range pyFiles {
		if goFiles[rel] != text {
			t.Errorf("%s が Python と違う\nGo:\n%s\nPython:\n%s", rel, goFiles[rel], text)
		}
	}
}

const noticeOracle = `
import json, sys
from ccnavi.catalog import docsearch
from ccnavi.infra import hookio, settings
c = json.load(sys.stdin)
root = c["root"]
conf = settings.load(root)[0]
out = {
    "start": docsearch.at_start(conf, root),
    "first": docsearch.fail_streak_note(conf, root, 1, True),
    "streak": docsearch.fail_streak_note(conf, root, 5),
    "hints": [],
}
for h in c["hints"]:
    payload = hookio.Input(event=hookio.POST_TOOL_USE, tool_name=h["tool"], cwd=h["cwd"],
        tool_input={"file_path": root + "/" + h["rel"]}, tool_response={"type": h["kind"]})
    out["hints"].append(docsearch.missing_frontmatter(conf, root, payload))
json.dump(out, sys.stdout, ensure_ascii=False)
`

type hintCase struct {
	Rel  string `json:"rel"`
	Text string `json:"-"`
	Tool string `json:"tool"`
	Kind string `json:"kind"`
	Cwd  string `json:"cwd"`
}

type noticeAnswer struct {
	Start  string   `json:"start"`
	First  string   `json:"first"`
	Streak string   `json:"streak"`
	Hints  []string `json:"hints"`
}

var hintCases = []hintCase{
	{Rel: "docs/new.md", Text: "# 見出し\n", Tool: "Write", Kind: "create"},
	{Rel: "docs/notype.md", Text: doc("title: t"), Tool: "Write", Kind: "create"},
	{Rel: "docs/typed.md", Text: doc("type: guide"), Tool: "Write", Kind: "create"},
	{Rel: "docs/blank.md", Text: doc(`type: "  "`), Tool: "Write", Kind: "create"},
	{Rel: ".claude/skills/x/SKILL.md", Text: "---\nname: x\n---\n", Tool: "Write", Kind: "create"},
	{Rel: "docs/edit.md", Text: "# x\n", Tool: "Edit", Kind: "create"},
	{Rel: "docs/update.md", Text: "# x\n", Tool: "Write", Kind: "update"},
	{Rel: "Readme.md", Text: "# x\n", Tool: "Write", Kind: "create"},
	{Rel: "WIP/Handoff/a.md", Text: "# x\n", Tool: "Write", Kind: "create"},
	{Rel: "wip/design/scripts/I3.md", Text: "# x\n", Tool: "Write", Kind: "create"},
	{Rel: ".ccnavi/docs/new.md", Text: "# x\n", Tool: "Write", Kind: "create"},
	{Rel: ".ccnavi/other.md", Text: "# x\n", Tool: "Write", Kind: "create"},
	{Rel: "projects/lib/docs/n.md", Text: "# x\n", Tool: "Write", Kind: "create"},
	{Rel: "ignored/n.md", Text: "# x\n", Tool: "Write", Kind: "create"},
}

func noticeWorkspace(t *testing.T, repo string, convention bool) string {
	t.Helper()
	root := edgeWorkspace(t)
	testkit.CopyMessages(t, root)
	testkit.Put(t, filepath.Join(root, ".gitignore"), "/projects/\n/ignored/\n")
	testkit.Put(t, filepath.Join(root, "ccnavi-index.jsonl"), "よその\n")
	if convention {
		data, _ := os.ReadFile(filepath.Join(repo, filepath.FromSlash(conventionDoc)))
		testkit.Put(t, filepath.Join(root, filepath.FromSlash(conventionDoc)), string(data))
	}
	for _, h := range hintCases {
		testkit.Put(t, filepath.Join(root, filepath.FromSlash(h.Rel)), h.Text)
	}
	return root
}

func TestNoticesMatchPython(t *testing.T) {
	repo := testkit.RepoRoot(t)
	t.Setenv(env.EntryPath, ".ccnavi/scripts/ccnavi-launcher.sh")
	for _, convention := range []bool{false, true} {
		pyRoot, goRoot := noticeWorkspace(t, repo, convention), noticeWorkspace(t, repo, convention)
		want := testkit.Answers[noticeAnswer](t, "", noticeOracle, map[string]any{"root": pyRoot, "hints": hintCases})
		said := 0
		for _, h := range want.Hints {
			if h != "" {
				said++
			}
		}
		if said < 4 || !strings.Contains(want.Start, "書き換えなかった") || !strings.Contains(want.Start, ".gitignore") {
			t.Fatalf("見本が Python に届いていない: %+v", want)
		}
		if !strings.Contains(want.Start, GuideHead) {
			t.Fatalf("Python の案内 = %q", want.Start)
		}
		conf := testkit.Settings(t, goRoot)
		got := noticeAnswer{Start: AtStart(conf, goRoot, time.Time{}), First: FailStreakNote(conf, goRoot, 1, true), Streak: FailStreakNote(conf, goRoot, 5, false)}
		for _, h := range hintCases {
			in := hookio.Input{Event: hookio.PostToolUse, ToolName: h.Tool, Cwd: h.Cwd,
				ToolInput: pyjson.NewObject("file_path", goRoot+"/"+h.Rel), ToolResponse: pyjson.NewObject("type", h.Kind)}
			got.Hints = append(got.Hints, MissingFrontmatter(conf, goRoot, in))
		}
		fix := func(s string) string { return strings.ReplaceAll(s, goRoot, pyRoot) }
		got.Start, got.First, got.Streak = fix(got.Start), fix(got.First), fix(got.Streak)
		for i := range got.Hints {
			got.Hints[i] = fix(got.Hints[i])
		}
		for _, f := range [][3]string{{"start", got.Start, want.Start}, {"first", got.First, want.First}, {"streak", got.Streak, want.Streak}} {
			if f[1] != f[2] {
				t.Errorf("%s (convention=%v):\nGo:     %s\nPython: %s", f[0], convention, f[1], f[2])
			}
		}
		for i, h := range hintCases {
			if got.Hints[i] != want.Hints[i] {
				t.Errorf("hint %s (convention=%v):\nGo:     %s\nPython: %s", h.Rel, convention, got.Hints[i], want.Hints[i])
			}
		}
	}
}
