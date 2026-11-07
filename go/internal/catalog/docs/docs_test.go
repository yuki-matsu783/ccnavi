package docs

import (
	"bytes"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"sort"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/catalog/docindex"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/testkit"
)

type workspace struct {
	t    *testing.T
	root string
}

func newWorkspace(t *testing.T) *workspace {
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q")
	w := &workspace{t, root}
	w.write(".gitignore", docindex.IgnoreLine+"\n")
	return w
}

func (w *workspace) path(rel string) string {
	return filepath.Join(append([]string{w.root}, strings.Split(rel, "/")...)...)
}

func (w *workspace) write(rel, text string) string { return testkit.Put(w.t, w.path(rel), text) }

func (w *workspace) read(rel string) string {
	data, _ := os.ReadFile(w.path(rel))
	return string(data)
}

// put は md を書き、更新時刻を mtime（ローカル時刻の YYYY-MM-DDTHH:MM:SS。空なら 2026-08-05T12:00:00）にする。
func (w *workspace) put(rel, text, mtime string) string {
	if mtime == "" {
		mtime = "2026-08-05T12:00:00"
	}
	when, err := time.ParseInLocation("2006-01-02T15:04:05", mtime, time.Local)
	if err != nil {
		w.t.Fatal(err)
	}
	path := w.write(rel, text)
	if err := os.Chtimes(path, when, when); err != nil {
		w.t.Fatal(err)
	}
	return path
}

// project はプロジェクトの置き場の下に git を作り、.gitignore を ignore にする（空なら書かない）。
func (w *workspace) project(name, ignore string) string {
	home := w.path("projects/" + name)
	os.MkdirAll(home, 0o755)
	testkit.Git(w.t, home, "init", "-q")
	if ignore != "" {
		testkit.Put(w.t, filepath.Join(home, ".gitignore"), ignore)
	}
	return home
}

func (w *workspace) conf() *config.Settings { return testkit.Settings(w.t, w.root) }

func (w *workspace) run(q Query) (int, string, string) {
	var out, errOut bytes.Buffer
	code := Run(&out, &errOut, w.conf(), w.root, q, true)
	return code, out.String(), errOut.String()
}

func doc(front ...string) string {
	return "---\n" + strings.Join(front, "\n") + "\n---\n\n# 本文\n\nbody text\n"
}

func eq(t *testing.T, what string, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s = %#v, want %#v", what, got, want)
	}
}

// --- 引く ---------------------------------------------------------------------------

// sample は引き方の見本の文書（Python の SearchTest の ROWS と同じもの）。
var sample = []struct{ rel, text, mtime string }{
	{"docs/adr/0001-git.md", doc("type: adr", "title: B タイトル", "tags: [git, Worktree]"), "2026-08-01T09:00:00"},
	{"docs/adr/0002-ticket.md", doc("type: ADR", "title: A タイトル", "keywords: [承認]"), "2026-08-05T23:30:00"},
	{"docs/claude/worktree.md", doc("type: guide", "tags: worktree"), "2026-08-06T00:00:00"},
	{"README.md", doc("type: guide", "description: 使い方 description"), "2026-07-01T00:00:00"},
	{"notes/plain.md", "# 見出しだけ\n", ""},
}

func sampleRows(t *testing.T) []docindex.Row {
	w := newWorkspace(t)
	for _, s := range sample {
		w.put(s.rel, s.text, s.mtime)
	}
	return collect(w.conf(), w.root, collectOptions{refresh: true, parse: true}).rows
}

func TestSearch(t *testing.T) {
	rows := sampleRows(t)
	cases := []struct {
		name  string
		query Query
		want  []string
	}{
		{"type は値の完全一致で大文字小文字を区別しない", Query{Types: []string{"adr"}}, []string{"docs/adr/0001-git", "docs/adr/0002-ticket"}},
		{"type の一部では当たらない", Query{Types: []string{"ad"}}, nil},
		{"スカラーの tags も 1 要素のリスト", Query{Tags: []string{"WORKTREE"}}, []string{"docs/adr/0001-git", "docs/claude/worktree"}},
		{"keyword", Query{Keywords: []string{"承認"}}, []string{"docs/adr/0002-ticket"}},
		{"同じ欄は OR", Query{Types: []string{"guide", "adr"}}, []string{"README", "docs/adr/0001-git", "docs/adr/0002-ticket", "docs/claude/worktree"}},
		{"違う欄は AND", Query{Types: []string{"guide"}, Tags: []string{"worktree"}}, []string{"docs/claude/worktree"}},
		{"path は concept_id の一部", Query{Paths: []string{"CLAUDE/"}}, []string{"docs/claude/worktree"}},
		// キー名にはヒットしない（ヒットすると全件になる）。
		{"text はキー名を見ない", Query{Texts: []string{"title"}}, nil},
		{"text は値を見る", Query{Texts: []string{"description"}}, []string{"README"}},
		{"text は mtime を見る", Query{Texts: []string{"2026-07"}}, []string{"README"}},
		{"text は concept_id を見る", Query{Texts: []string{"plain"}}, []string{"notes/plain"}},
		{"日付だけの until はその日の終わりまで", Query{Since: "2026-08-05", Until: "2026-08-05"}, []string{"docs/adr/0002-ticket", "notes/plain"}},
		{"分までの until", Query{Until: "2026-08-05T12:00"}, []string{"README", "docs/adr/0001-git", "notes/plain"}},
		{"mtime の逆順と件数", Query{Sort: SortMtime, Reverse: true, Limit: 2}, []string{"docs/claude/worktree", "docs/adr/0002-ticket"}},
		// 同じ値は concept_id で並び、frontmatter の無いものは空として先に来る。
		{"type の順", Query{Sort: SortType}, []string{"notes/plain", "docs/adr/0001-git", "docs/adr/0002-ticket", "README", "docs/claude/worktree"}},
	}
	for _, c := range cases {
		hits, _ := search(rows, c.query)
		var got []string
		for _, h := range hits {
			got = append(got, h.ConceptID())
		}
		eq(t, c.name, got, c.want)
	}
	hits, matched := search(rows, Query{Limit: 2})
	eq(t, "limit", [2]int{len(hits), matched}, [2]int{2, 5})
}

func TestLearningWikiOnlyWhenTyped(t *testing.T) {
	w := newWorkspace(t)
	w.put("docs/wiki/inbox/a.md", doc("type: learning-wiki"), "")
	w.put("docs/b.md", doc("type: guide"), "")
	rows := collect(w.conf(), w.root, collectOptions{refresh: true, parse: true}).rows
	eq(t, "既定", len(slices.DeleteFunc(slices.Clone(rows), func(r docindex.Row) bool { return !matches(r, Query{}) })), 1)
	eq(t, "明示", matches(rows[1], Query{Types: []string{"Learning-Wiki"}}), true)
}

func TestProblemsNameBadValues(t *testing.T) {
	eq(t, "4 つ", len(usageProblems(Query{Sort: "size", Format: "xml", Since: "yesterday", Until: "2026-8-5"})), 4)
	for _, bad := range []string{"2026-13-45", "2026-02-30", "2026-08-05T25", "2026-08-05T10:61", "2026-08-05T10:00:60", "0000-01-01", "2026-08-05\n", "2024-02-１9", "2026-0８-05", "2026-08-05T2３"} {
		if len(usageProblems(Query{Since: bad})) != 1 {
			t.Errorf("%q を受けた", bad)
		}
	}
	for _, good := range []string{"2024-02-29", "2026-08-05T10:59", "2026-08-05T23:59:59", "２０２６-08-05", "2024-02-2９", "2026-08-05T1３:5９:0９"} {
		if p := usageProblems(Query{Until: good}); len(p) != 0 {
			t.Errorf("%q を断った: %v", good, p)
		}
	}
}

func TestRender(t *testing.T) {
	rows := sampleRows(t)
	out := func(q Query, rows []docindex.Row) string {
		var b bytes.Buffer
		hits, matched := search(rows, q)
		render(&b, hits, matched, len(rows), q.format())
		return b.String()
	}
	eq(t, "count", out(Query{Format: FormatCount}, rows), "matched=5 total=5\n")
	eq(t, "count で切ったとき", out(Query{Format: FormatCount, Limit: 1}, rows), "matched=5 shown=1 total=5\n")
	eq(t, "path", out(Query{Format: FormatPath, Types: []string{"guide"}}, rows), "README\ndocs/claude/worktree\n")
	var list []any
	if v, err := pyjson.Loads(out(Query{Format: FormatJSON}, rows)); err == nil {
		list, _ = v.([]any)
	}
	eq(t, "json", len(list), 5)

	w := newWorkspace(t)
	w.put("a.md", doc("type: guide", `description: "1 行目\n2 行目"`, "tags: [x, y]"), "")
	w.put("日本語/文書.md", doc("type: ガイド", "title: タイトル"), "")
	w.put("ab.md", doc("type: adr", "title: t"), "")
	more := collect(w.conf(), w.root, collectOptions{refresh: true, parse: true}).rows
	detail := out(Query{Format: FormatDetail, Paths: []string{"a"}}, more[:1])
	for _, want := range []string{"  description: 1 行目 2 行目\n", "  tags       : x, y\n"} {
		if !strings.Contains(detail, want) {
			t.Errorf("detail に %q が無い:\n%s", want, detail)
		}
	}
	lines := strings.Split(strings.TrimSuffix(out(Query{Paths: []string{"b", "文"}}, more), "\n"), "\n")
	// タイトルの列が始まる見た目の位置が揃う（全角は 2 桁）。
	eq(t, "table", lines[0], "adr     ab           t")
	eq(t, "幅", displayWidth(lines[0][:strings.LastIndex(lines[0], " ")+1]), displayWidth(lines[1][:strings.LastIndex(lines[1], " ")+1]))
}

// NFC にそろえて比べ、結合文字は幅 0。
func TestNormalization(t *testing.T) {
	nfd := pyfmt.NFD("が")
	w := newWorkspace(t)
	w.put("docs/"+nfd+".md", doc("tags: ["+nfd+"]", "title: "+nfd), "")
	rows := collect(w.conf(), w.root, collectOptions{refresh: true, parse: true}).rows
	for _, q := range []Query{{Paths: []string{"が"}}, {Tags: []string{"が"}}, {Texts: []string{"が"}}} {
		if hits, _ := search(rows, q); len(hits) != 1 {
			t.Errorf("%+v が当たらない", q)
		}
	}
	eq(t, "結合文字", displayWidth(nfd), 2)
	eq(t, "é", displayWidth("é"), 1)
}

// --- 合わせて集める --------------------------------------------------------------------

func projects(t *testing.T) (*workspace, string, string) {
	w := newWorkspace(t)
	w.write(".gitignore", docindex.IgnoreLine+"\n/projects/\n")
	lib := w.project("lib", docindex.IgnoreLine+"\n")
	// 利用者が索引を追跡している（否定の行がある）プロジェクトは、足さず対象外のまま。
	bare := w.project("bare", "!"+docindex.IgnoreLine+"\n")
	w.put("docs/w.md", doc("type: guide", "title: ワークスペース"), "")
	w.put("projects/lib/docs/x.md", doc("type: adr", "title: lib の決定"), "")
	w.put("projects/bare/y.md", doc("type: guide"), "")
	return w, lib, bare
}

func TestProjectsAreSearchedWithPathsFromTheWorkspaceRoot(t *testing.T) {
	w, lib, bare := projects(t)
	code, out, errOut := w.run(Query{Format: FormatJSONL})
	eq(t, "code", code, 0)
	var got [][2]string
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		v, _ := pyjson.Loads(line)
		o := v.(*pyjson.Object)
		got = append(got, [2]string{o.String("concept_id"), o.String("directory")})
	}
	eq(t, "rows", got, [][2]string{{"docs/w", "docs"}, {"projects/lib/docs/x", "projects/lib/docs"}})
	// 書く行はそのプロジェクトのルートからの相対。
	if data, _ := os.ReadFile(filepath.Join(lib, "docs", docindex.IndexName)); !strings.Contains(string(data), `"concept_id":"docs/x"`) {
		t.Errorf("プロジェクトの索引 = %s", data)
	}
	if !strings.Contains(errOut, "bare "+unignoredSaid) {
		t.Errorf("stderr = %s", errOut)
	}
	if _, err := os.Stat(filepath.Join(bare, docindex.IndexName)); err == nil {
		t.Error("対象外のプロジェクトに書いた")
	}
	_, out, _ = w.run(Query{Paths: []string{"projects/lib"}, Format: FormatPath})
	eq(t, "path", out, "projects/lib/docs/x\n")
	w.put("projects/lib/.ccnavi/approved/doing/i0001.md", "---\nversion: 1\n---\n", "")
	_, out, _ = w.run(Query{Paths: []string{".ccnavi"}, Format: FormatCount})
	eq(t, "ccnavi ディレクトリ", out, "matched=0 total=2\n")
}

func TestAProjectWithoutIgnoreGetsTheLine(t *testing.T) {
	w, _, _ := projects(t)
	plain := w.project("plain", "node_modules/\n")
	w.put("projects/plain/z.md", doc("type: guide"), "")
	_, out, errOut := w.run(Query{Format: FormatPath})
	if !slices.Contains(strings.Split(out, "\n"), "projects/plain/z") || !strings.Contains(errOut, "projects/plain/.gitignore に") {
		t.Errorf("out = %s\nerr = %s", out, errOut)
	}
	want := "node_modules/\n" + docindex.IgnoreLine + "\n"
	eq(t, ".gitignore", w.read("projects/plain/.gitignore"), want)
	_, _, errOut = w.run(Query{Format: FormatPath})
	if strings.Contains(errOut, "を追加しました") {
		t.Error("2 回目も足した")
	}
	eq(t, ".gitignore", w.read("projects/plain/.gitignore"), want)
	_ = plain
}

func TestAPastDeadlineStopsBeforeAnyTree(t *testing.T) {
	w, _, _ := projects(t)
	found := collect(w.conf(), w.root, collectOptions{refresh: true, parse: true, deadline: time.Now().Add(-time.Second)})
	eq(t, "timed out", [2]any{found.timedOut, len(found.rows)}, [2]any{true, 0})
}

func TestNFCDuplicatesAreFolded(t *testing.T) {
	w := newWorkspace(t)
	w.put("が.md", doc("type: a"), "")
	w.put(pyfmt.NFD("が")+".md", doc("type: b"), "")
	names, _ := os.ReadDir(w.root)
	if len(names) < 4 {
		t.Skip("このファイルシステムは名前を正規化する")
	}
	eq(t, "rows", len(collect(w.conf(), w.root, collectOptions{refresh: true, parse: true}).rows), 1)
}

func TestAProjectWithoutGitIsNamed(t *testing.T) {
	w := newWorkspace(t)
	w.put("a.md", doc("type: guide"), "")
	w.put("projects/plain/x.md", doc("type: guide"), "")
	_, _, errOut := w.run(Query{})
	if !strings.Contains(errOut, "プロジェクト plain は .git を持たないので引かない") {
		t.Errorf("stderr = %s", errOut)
	}
	if strings.Contains(AtStart(w.conf(), w.root, time.Time{}), "plain") {
		t.Error("SessionStart で名指しした")
	}
}

// git に聞けなかったことを、git の外・無視されていないと取り違えない。
func TestGitFailureIsNeitherOutsideGitNorUnignored(t *testing.T) {
	w := newWorkspace(t)
	w.put("a.md", doc("type: guide"), "")
	w.write(".git/HEAD", "broken\n")
	found := collect(w.conf(), w.root, collectOptions{refresh: true, parse: true})
	eq(t, "failed", [2]int{len(found.failed), len(found.unignored)}, [2]int{1, 0})
	if !strings.HasPrefix(found.failed[0], workspaceName+": ") {
		t.Errorf("failed = %v", found.failed)
	}
	_, _, errOut := w.run(Query{})
	if !strings.Contains(errOut, "git への問い合わせに失敗したので引かない") {
		t.Errorf("stderr = %s", errOut)
	}
	if strings.Contains(AtStart(w.conf(), w.root, time.Time{}), unignoredSaid) {
		t.Error("対象外と言った")
	}
}

// --- SessionStart ----------------------------------------------------------------------

func TestAtStart(t *testing.T) {
	w := newWorkspace(t)
	w.put("docs/a.md", doc("type: guide"), "")
	testkit.CopyMessages(t, w.root)
	said := AtStart(w.conf(), w.root, time.Time{})
	if _, err := os.Stat(w.path("docs/" + docindex.IndexName)); err != nil {
		t.Error("索引を新しくしない")
	}
	// 常駐の文脈には引き方の要点の 1 行だけ。オプションの一覧と frontmatter の書き方は渡さない。
	eq(t, "1 行", len(strings.Split(said, "\n")), 1)
	if !strings.HasPrefix(said, GuideHead) || !strings.Contains(said, "grep・Glob より先に") || !strings.Contains(said, "--docs --keyword <語>") || !strings.Contains(said, "--help") {
		t.Errorf("said = %s", said)
	}
	for _, listed := range []string{"--format", "--sort", "--since", "type は必須", "kebab-case", "--keyword frontmatter"} {
		if strings.Contains(said, listed) {
			t.Errorf("%s を渡した", listed)
		}
	}
	w.put(conventionDoc, doc("type: guide"), "")
	if !strings.Contains(AtStart(w.conf(), w.root, time.Time{}), "--docs --keyword frontmatter") {
		t.Error("決まりの文書を指さない")
	}
}

func TestAtStartNamesTreesThatGotTheLineOrWereLeftOut(t *testing.T) {
	w, _, _ := projects(t)
	testkit.CopyMessages(t, w.root)
	w.project("plain", "")
	w.put("projects/plain/y.md", doc("type: guide"), "")
	said := AtStart(w.conf(), w.root, time.Time{})
	for _, want := range []string{"--docs", "bare " + unignoredSaid, "projects/plain/.gitignore " + ignoreAddedSaid} {
		if !strings.Contains(said, want) {
			t.Errorf("%q が無い: %s", want, said)
		}
	}

	only := newWorkspace(t)
	only.write(".gitignore", "!"+docindex.IgnoreLine+"\n")
	only.put("docs/a.md", doc("type: guide"), "")
	said = AtStart(only.conf(), only.root, time.Time{})
	if !strings.Contains(said, workspaceName+" "+unignoredSaid) || strings.Contains(said, "grep・Glob より先に") {
		t.Errorf("said = %s", said)
	}

	foreign := newWorkspace(t)
	foreign.put("a.md", doc("type: guide"), "")
	foreign.write(docindex.IndexName, "x\n")
	if !strings.Contains(AtStart(foreign.conf(), foreign.root, time.Time{}), "書き換えなかった: "+docindex.IndexName) {
		t.Error("よその索引を名指ししない")
	}
}

func TestAtStartIsSilentWithoutMarkdownOrOutsideGit(t *testing.T) {
	w := newWorkspace(t)
	w.put("docs/x.txt", "x", "")
	eq(t, "md が無い", AtStart(w.conf(), w.root, time.Time{}), "")
	eq(t, "期限切れで md が無い", AtStart(w.conf(), w.root, time.Now().Add(-time.Second)), "")
	bare := testkit.TempRoot(t)
	testkit.Put(t, filepath.Join(bare, "a.md"), doc("type: guide"))
	eq(t, "git の外", AtStart(testkit.Settings(t, bare), bare, time.Time{}), "")
}

// 期限が切れていれば新しくしないが、既存の索引と md があれば案内は出す。
func TestAtStartWithoutTimeDoesNotRefreshButStillGuides(t *testing.T) {
	w := newWorkspace(t)
	testkit.CopyMessages(t, w.root)
	w.put("a.md", doc("type: guide"), "")
	if !strings.Contains(AtStart(w.conf(), w.root, time.Now().Add(-time.Second)), "--docs") {
		t.Error("案内しない")
	}
	if _, err := os.Stat(w.path(docindex.IndexName)); err == nil {
		t.Error("期限切れで書いた")
	}
	collect(w.conf(), w.root, collectOptions{refresh: true, parse: true})
	before := w.read(docindex.IndexName)
	w.put("a.md", doc("type: adr"), "2026-08-05T13:00:00")
	if !strings.Contains(AtStart(w.conf(), w.root, time.Now().Add(-time.Second)), "--docs") {
		t.Error("案内しない")
	}
	eq(t, "索引", w.read(docindex.IndexName), before)
}

// --- 書いた時点の案内 --------------------------------------------------------------------

func (w *workspace) hint(rel, text, tool, kind, base string) string {
	if base == "" {
		base = w.root
	}
	path := filepath.Join(append([]string{base}, strings.Split(rel, "/")...)...)
	if text != "" {
		testkit.Put(w.t, path, text)
	}
	in := hookio.Input{Event: hookio.PostToolUse, ToolName: tool, Cwd: base,
		ToolInput: pyjson.NewObject("file_path", path), ToolResponse: pyjson.NewObject("type", kind, "filePath", path)}
	return MissingFrontmatter(w.conf(), w.root, in)
}

func TestMissingFrontmatter(t *testing.T) {
	w := newWorkspace(t)
	testkit.CopyMessages(t, w.root)
	said := w.hint("docs/new.md", "# 見出し\n", "Write", "create", "")
	for _, want := range []string{"[ccnavi] " + CodeMissingFrontmatter + " ", "(docs/new.md)", "type（必須）"} {
		if !strings.Contains(said, want) {
			t.Errorf("%q が無い: %s", want, said)
		}
	}
	if strings.Contains(said, "--help") {
		t.Error("--help を渡した")
	}
	if !strings.Contains(w.hint("docs/t.md", doc("title: t"), "Write", "create", ""), "frontmatter に type が無い") {
		t.Error("type が無いと言わない")
	}
	eq(t, "type がある", w.hint("docs/ok.md", doc("type: guide"), "Write", "create", ""), "")
	w.put("docs/old.md", "# 見出し\n", "")
	eq(t, "Edit", w.hint("docs/old.md", "", "Edit", "", ""), "")
	eq(t, "上書き", w.hint("docs/old.md", "# 上書き\n", "Write", "update", ""), "")
	if !strings.Contains(w.hint(".claude/skills/x/SKILL.md", "---\nname: x\ndescription: y\n---\n# x\n", "Write", "create", ""), "type: skill を足してください") {
		t.Error("スキルに type: skill を促さない")
	}
	for _, rel := range []string{"README.md", "extensions/x/README.md", "CLAUDE.md", "AGENTS.md", "CHANGELOG.md", "readme.MD",
		"wip/handoff/draft.md", "wip/proposals/todo/i0001.md", ".ccnavi/notes.md", "wip/design/scripts/i12.md",
		".claude/skills/yomiyasu/a.md", ".claude/skills/yomiyasu/references/b.md", "docs/a.txt"} {
		eq(t, rel, w.hint(rel, "# 見出し\n", "Write", "create", ""), "")
	}
	// 表に無い置き場には出す。
	for _, rel := range []string{"wip/design/notes.md", ".claude/skills/x/references/a.md"} {
		if w.hint(rel, "# 見出し\n", "Write", "create", "") == "" {
			t.Errorf("%s に出さない", rel)
		}
	}
	w.write(".gitignore", docindex.IgnoreLine+"\n/out/\n")
	eq(t, "無視されたもの", w.hint("out/a.md", "# 見出し\n", "Write", "create", ""), "")
	w.put(conventionDoc, doc("type: guide"), "")
	said = w.hint("docs/new2.md", "# 見出し\n", "Write", "create", "")
	if !strings.Contains(said, conventionDoc) || !strings.Contains(said, "--docs --keyword frontmatter") {
		t.Errorf("決まりの文書を指さない: %s", said)
	}
}

// ワークスペースが無視するワークツリーの md も統合後は索引に入るので、ワークツリー自身の git で決める。
func TestAWorktreeIsJudgedByItsOwnGit(t *testing.T) {
	w := newWorkspace(t)
	testkit.CopyMessages(t, w.root)
	w.write(".gitignore", docindex.IgnoreLine+"\n/.claude/worktrees/\n")
	testkit.Git(t, w.root, "add", ".gitignore")
	testkit.Git(t, w.root, "commit", "-q", "-m", "seed")
	treeRoot := w.path(".claude/worktrees/w")
	testkit.Git(t, w.root, "worktree", "add", "-q", treeRoot, "-b", "w")
	if said := w.hint("docs/new.md", "# 見出し\n", "Write", "create", treeRoot); !strings.Contains(said, "(.claude/worktrees/w/docs/new.md)") {
		t.Errorf("said = %s", said)
	}
	eq(t, "下書き", w.hint("wip/handoff/a.md", "# 見出し\n", "Write", "create", treeRoot), "")
}

// 案内を出さない置き場は、決まりの文書の「対象外」の表で付けない・付けなくてよいとするもの。
func TestExemptPlacesMatchTheConventionTable(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), filepath.FromSlash(conventionDoc)))
	if err != nil {
		t.Fatal(err)
	}
	_, after, _ := strings.Cut(string(data), "## 対象外")
	table := strings.SplitN(after, "\n\n", 3)[1]
	code := regexp.MustCompile("`([^`]+)`")
	var places []string
	for _, row := range strings.Split(table, "\n")[2:] {
		cells := strings.Split(strings.Trim(row, "|"), "|")
		for i := range cells {
			cells[i] = strings.TrimSpace(cells[i])
		}
		if cells[1] != "付けない" && cells[1] != "付けなくてよい" {
			continue
		}
		for _, m := range code.FindAllStringSubmatch(cells[0], -1) {
			if strings.Contains(m[1], "/") {
				if strings.HasSuffix(m[1], "/") {
					places = append(places, m[1]+"**")
				} else {
					places = append(places, m[1])
				}
			}
		}
	}
	sort.Strings(places)
	want := slices.Clone(noFrontmatterPlaces)
	sort.Strings(want)
	eq(t, "places", slices.Compact(places), want)
}
