package docs

import (
	"bytes"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/testkit"
)

// 文書の索引を引く Run と、文書の知らせを見る。決めた見本の文書で引いた答えは、Go の答えの見本（golden.Check）と
// 比べる（見本は Python の docsearch.run と答えが揃っていた Go の答えから取った）。リポジトリの実際の文書で引く
// ほうは、文書が変わるたびに答えが変わるので、索引を引けて書けたことだけを見る。

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

func TestRunOnTheRepositoryDocs(t *testing.T) {
	repo := testkit.RepoRoot(t)
	root := docsWorkspace(t, repo)
	conf := testkit.Settings(t, root)
	for i, c := range repoQueries {
		var out, errOut bytes.Buffer
		if code := Run(&out, &errOut, conf, root, c.Query.query(), c.Refresh); code != 0 && code != 1 {
			t.Errorf("%+v: 終了コード %d\n%s", c, code, errOut.String())
		}
		if i == 1 && strings.Count(out.String(), "\n") < 100 {
			t.Fatalf("索引を引けていない: %.2000s", out.String())
		}
	}
	if files := indexFiles(t, root); len(files) < 5 {
		t.Fatalf("索引が書かれていない: %v", files)
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
	root := edgeWorkspace(t)
	conf := testkit.Settings(t, root)
	var answers []runAnswer
	for _, c := range edgeQueries {
		var out, errOut bytes.Buffer
		code := Run(&out, &errOut, conf, root, c.Query.query(), c.Refresh)
		answers = append(answers, runAnswer{code, out.String(), strings.ReplaceAll(errOut.String(), root, "<root>")})
	}
	if !strings.Contains(answers[0].Out, "projects/lib/docs/p") || !strings.Contains(answers[0].Err, "nogit") {
		t.Fatalf("見本の文書に届いていない: %+v", answers[0])
	}
	files := indexFiles(t, root)
	if len(files) < 5 {
		t.Fatalf("索引が書かれていない: %v", files)
	}
	for rel, text := range files {
		files[rel] = strings.ReplaceAll(text, root, "<root>")
	}
	golden.Check(t, "edge", map[string]any{"runs": answers, "files": files})
}

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
	all := map[string]noticeAnswer{}
	for _, convention := range []bool{false, true} {
		root := noticeWorkspace(t, repo, convention)
		conf := testkit.Settings(t, root)
		got := noticeAnswer{Start: AtStart(conf, root, time.Time{}), First: FailStreakNote(conf, root, 1, true), Streak: FailStreakNote(conf, root, 5, false)}
		said := 0
		for _, h := range hintCases {
			in := hookio.Input{Event: hookio.PostToolUse, ToolName: h.Tool, Cwd: h.Cwd,
				ToolInput: pyjson.NewObject("file_path", root+"/"+h.Rel), ToolResponse: pyjson.NewObject("type", h.Kind)}
			hint := MissingFrontmatter(conf, root, in)
			if hint != "" {
				said++
			}
			got.Hints = append(got.Hints, strings.ReplaceAll(hint, root, "<root>"))
		}
		if said < 4 || !strings.Contains(got.Start, "書き換えなかった") || !strings.Contains(got.Start, ".gitignore") || !strings.Contains(got.Start, GuideHead) {
			t.Fatalf("見本の文書に届いていない: %+v", got)
		}
		fix := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
		got.Start, got.First, got.Streak = fix(got.Start), fix(got.First), fix(got.Streak)
		all[strconv.FormatBool(convention)] = got
	}
	golden.Check(t, "notices", all)
}
