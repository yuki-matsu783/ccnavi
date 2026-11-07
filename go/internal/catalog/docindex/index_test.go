package docindex

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"syscall"
	"testing"
	"time"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/testkit"
)

// noon は 2026-08-05 12:00:00 のローカル時刻（行の mtime はローカル時刻で書く）。
var noon = time.Date(2026, 8, 5, 12, 0, 0, 0, time.Local)

const noonText = "2026-08-05T12:00:00"

type repo struct {
	t    *testing.T
	root string
}

func newRepo(t *testing.T) *repo {
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q")
	r := &repo{t, root}
	r.write(gitignoreName, IgnoreLine+"\n")
	return r
}

func (r *repo) path(rel string) string {
	return filepath.Join(append([]string{r.root}, strings.Split(rel, "/")...)...)
}

func (r *repo) write(rel, text string) string { return testkit.Put(r.t, r.path(rel), text) }

// put は md を書き、更新時刻を when にする（ゼロ値なら noon）。
func (r *repo) put(rel, text string, when ...time.Time) string {
	path := r.write(rel, text)
	at := noon
	if len(when) > 0 {
		at = when[0]
	}
	if err := os.Chtimes(path, at, at); err != nil {
		r.t.Fatal(err)
	}
	return path
}

func (r *repo) read(rel string) string {
	data, err := os.ReadFile(r.path(rel))
	if err != nil {
		r.t.Fatal(err)
	}
	return string(data)
}

func (r *repo) exists(rel string) bool {
	_, err := os.Lstat(r.path(rel))
	return err == nil
}

func (r *repo) build(o ...Options) *Built {
	r.t.Helper()
	opts := Options{Refresh: true, Parse: true}
	if len(o) > 0 {
		opts = o[0]
	}
	built, err := Build(r.root, opts)
	if err != nil {
		r.t.Fatal(err)
	}
	return built
}

func index(dir string) string {
	if dir == "" {
		return IndexName
	}
	return dir + "/" + IndexName
}

func doc(front ...string) string {
	return "---\n" + strings.Join(front, "\n") + "\n---\n\n# 本文\n\nbody text\n"
}

func ids(rows []Row) []string {
	out := []string{}
	for _, r := range rows {
		out = append(out, r.ConceptID())
	}
	return out
}

func eq(t *testing.T, what string, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s = %#v, want %#v", what, got, want)
	}
}

func sorted(s []string) []string {
	s = append([]string{}, s...)
	sort.Strings(s)
	return s
}

// --- 索引 ---------------------------------------------------------------------------

func TestWritesOneIndexPerDirectoryWithTheRowShape(t *testing.T) {
	r := newRepo(t)
	r.put("README.md", doc("type: guide", "title: はじめに"))
	r.put("docs/adr/0001-a.md", doc("type: adr", "tags: [git, worktree]"))
	built := r.build()
	eq(t, "written", sorted(built.Written), []string{IndexName, "docs/adr/" + IndexName})
	eq(t, "row", r.read(index("docs/adr")),
		`{"concept_id":"docs/adr/0001-a","directory":"docs/adr","frontmatter":{"type":"adr","tags":["git","worktree"]},"mtime":"`+noonText+`"}`+"\n")
	if !strings.Contains(r.read(IndexName), `"directory":"."`) {
		t.Error("ルート直下の directory が . でない")
	}
}

func TestGitignoreLine(t *testing.T) {
	cases := []struct {
		name, before, after string
		added               bool
	}{
		{"足りなければ 1 行足す", "build/\n", "build/\n" + IgnoreLine + "\n", true},
		{"末尾の改行を補い、ほかの行は変えない", "build/\r\n*.log", "build/\r\n*.log\n" + IgnoreLine + "\n", true},
		// git は先頭の空白を無視しないので、同じ行ではない。
		{"先頭に空白のある行は同じ行ではない", "build/\n  " + IgnoreLine + "\n", "build/\n  " + IgnoreLine + "\n" + IgnoreLine + "\n", true},
		{"無ければ作る", "", IgnoreLine + "\n", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := newRepo(t)
			if c.before == "" {
				os.Remove(r.path(gitignoreName))
			} else {
				r.write(gitignoreName, c.before)
			}
			r.put("a.md", doc("type: guide"))
			built := r.build()
			eq(t, "ignore", [3]any{built.Unignored, built.IgnoreAdded, built.Written}, [3]any{false, gitignoreName, []string{IndexName}})
			eq(t, ".gitignore", r.read(gitignoreName), c.after)
			again := r.build()
			eq(t, "2 回目", [2]any{again.IgnoreAdded, again.Written}, [2]any{"", []string(nil)})
			eq(t, ".gitignore", r.read(gitignoreName), c.after)
		})
	}
}

func TestALineAlreadyThereIsNotAppendedAgain(t *testing.T) {
	r := newRepo(t)
	r.write(gitignoreName, "build/\n"+IgnoreLine+"  \n")
	eq(t, "addIgnore", addIgnore(r.root), present)
	eq(t, ".gitignore", r.read(gitignoreName), "build/\n"+IgnoreLine+"  \n")
}

// 索引が追跡されていて無視されないとき、行は既にあっても書いていないので、足したとは言わない。
func TestAPresentLineThatDoesNotIgnoreTheIndexIsNotReportedAsAdded(t *testing.T) {
	r := newRepo(t)
	r.put("docs/a.md", doc("type: guide"))
	r.write(index("docs"), "")
	testkit.Git(t, r.root, "add", "-f", index("docs"))
	built := r.build()
	eq(t, "unignored", [2]any{built.Unignored, built.IgnoreAdded}, [2]any{true, ""})
	eq(t, ".gitignore", r.read(gitignoreName), IgnoreLine+"\n")
}

func TestTheTreeIsLeftOut(t *testing.T) {
	cases := []struct {
		name    string
		prepare func(r *repo)
		refresh bool
	}{
		{"否定 **/", func(r *repo) { r.write(gitignoreName, "!**/"+IndexName+"\n") }, true},
		{"否定", func(r *repo) { r.write(gitignoreName, "!"+IndexName+"\n") }, true},
		{"広い否定", func(r *repo) { r.write(gitignoreName, "!docs/*.jsonl\n") }, true},
		{".gitignore がふつうのファイルでない", func(r *repo) {
			os.Remove(r.path(gitignoreName))
			os.Mkdir(r.path(gitignoreName), 0o755)
		}, true},
		{"新しくしないときは足さない", func(r *repo) { r.write(gitignoreName, "") }, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			r := newRepo(t)
			c.prepare(r)
			r.put("a.md", doc("type: guide"))
			before, _ := os.ReadFile(r.path(gitignoreName))
			built := r.build(Options{Refresh: c.refresh, Parse: true})
			eq(t, "left out", [4]any{built.Unignored, len(built.Rows), built.Written, built.IgnoreAdded}, [4]any{true, 0, []string(nil), ""})
			after, _ := os.ReadFile(r.path(gitignoreName))
			eq(t, ".gitignore", string(after), string(before))
			if r.exists(IndexName) {
				t.Error("索引を書いた")
			}
		})
	}
}

func TestAPartlyIgnoredTreeGetsNoLine(t *testing.T) {
	r := newRepo(t)
	r.write(gitignoreName, "/docs/"+IndexName+"\n")
	r.put("a.md", doc("type: guide"))
	r.put("docs/b.md", doc("type: guide"))
	built := r.build()
	eq(t, "built", [2]any{built.IgnoreAdded, built.Written}, [2]any{"", []string{index("docs")}})
	eq(t, ".gitignore", r.read(gitignoreName), "/docs/"+IndexName+"\n")
}

func TestATrackedIndexIsNotRewrittenButItsRowsAreBuilt(t *testing.T) {
	r := newRepo(t)
	r.put("a.md", doc("type: guide"))
	r.put("docs/b.md", doc("type: adr"))
	r.write(IndexName, "tracked\n")
	testkit.Git(t, r.root, "add", "-f", IndexName)
	built := r.build()
	eq(t, "built", [3]any{built.Unignored, ids(built.Rows), built.Written}, [3]any{false, []string{"a", "docs/b"}, []string{index("docs")}})
	eq(t, "tracked", r.read(IndexName), "tracked\n")
}

func TestListsUntrackedButNotIgnoredFiles(t *testing.T) {
	r := newRepo(t)
	r.write(gitignoreName, IgnoreLine+"\nbuild/\n")
	r.put("new.md", doc("type: guide"))
	r.put("build/out.md", doc("type: guide"))
	r.put("notes.txt", "not md")
	eq(t, "rows", ids(r.build().Rows), []string{"new"})
}

func TestSkipsDeletedFilesAndRemovesTheIndexOfAnEmptiedDirectory(t *testing.T) {
	r := newRepo(t)
	r.put("keep/a.md", doc("type: guide"))
	r.put("gone/b.md", doc("type: guide"))
	testkit.Git(t, r.root, "add", "-A")
	r.build()
	if !r.exists(index("gone")) {
		t.Fatal("索引が無い")
	}
	os.Remove(r.path("gone/b.md"))
	built := r.build()
	eq(t, "rows", ids(built.Rows), []string{"keep/a"})
	eq(t, "removed", built.Removed, []string{index("gone")})
	if r.exists(index("gone")) {
		t.Error("消えたディレクトリの索引が残った")
	}
}

func TestTheCcnaviDirectoryIsLeftOutExceptItsDocs(t *testing.T) {
	r := newRepo(t)
	r.put(".ccnavi/approved/done/i0001.md", "---\nversion: 1\nticket: i0001\n---\n")
	r.put(".ccnavi/docs/usage.md", doc("type: guide"))
	r.put("a.md", doc("type: guide"))
	eq(t, "excluded", ids(r.build(Options{Excluded: []string{".ccnavi"}, Refresh: true, Parse: true}).Rows), []string{"a"})
	built := r.build(Options{Excluded: []string{".ccnavi"}, Kept: []string{".ccnavi/docs"}, Refresh: true, Parse: true})
	eq(t, "kept", ids(built.Rows), []string{".ccnavi/docs/usage", "a"})
	if r.exists(index(".ccnavi/approved/done")) {
		t.Error("ccnavi ディレクトリに索引を書いた")
	}
}

func TestNotARepository(t *testing.T) {
	if _, err := Build(t.TempDir(), Options{Refresh: true, Parse: true}); !errors.Is(err, ErrNotRepository) {
		t.Errorf("err = %v", err)
	}
}

// --- 差分 ---------------------------------------------------------------------------

func TestReusesRowsByConceptIDAndMtime(t *testing.T) {
	r := newRepo(t)
	r.put("a.md", doc("type: guide"))
	r.put("b.md", doc("type: adr"))
	first := r.build()
	eq(t, "first", [2]int{first.Parsed, first.Reused}, [2]int{2, 0})
	// 使い回すかは concept_id と mtime だけで決める（同じ mtime に戻した中身は読み直さない）。
	r.put("a.md", doc("type: changed"))
	second := r.build()
	eq(t, "second", [3]any{second.Parsed, second.Reused, second.Written}, [3]any{0, 2, []string(nil)})
	eq(t, "type", second.Rows[0].Front().Value("type"), "guide")
	r.put("a.md", doc("type: changed"), noon.Add(time.Minute))
	third := r.build()
	eq(t, "third", [3]any{third.Parsed, third.Written, third.Rows[0].Front().Value("type")}, [3]any{1, []string{IndexName}, "changed"})
}

func TestNoRefreshUsesExistingRowsAndWritesNothing(t *testing.T) {
	r := newRepo(t)
	r.put("a.md", doc("type: guide"))
	r.build()
	r.put("a.md", doc("type: changed"), noon.Add(time.Minute))
	r.put("b.md", doc("type: adr"))
	built := r.build(Options{Parse: true})
	types := map[string]any{}
	for _, row := range built.Rows {
		types[row.ConceptID()] = row.Front().Value("type")
	}
	eq(t, "types", types, map[string]any{"a": "guide", "b": "adr"})
	eq(t, "written", built.Written, []string(nil))
}

// --- frontmatter --------------------------------------------------------------------

func TestFrontMatter(t *testing.T) {
	cases := []struct {
		raw  string
		want string // 詰めた JSON。空なら nil
	}{
		{"# no front\n", ""},
		{"---\ntype: guide\n", ""},
		{"---\ntype: [unclosed\n---\n", ""},
		{"---\na: &x 1\nb: *x\n---\n", ""},
		{"---\n- a\n- b\n---\n", ""},
		{"---\n!!python/object:os.system x\n---\n", ""},
		{"---\nn: 1" + strings.Repeat("9", 5000) + "\n---\n", ""},
		{"---\nn: " + strings.Repeat("[", 40) + strings.Repeat("]", 40) + "\n---\n", ""},
		{"---\ndate: 2026-08-05\nn: .nan\nok: true\n---\n", `{"date":"2026-08-05","n":"nan","ok":true}`},
		{"\ufeff---\ntype: a\n...\n", `{"type":"a"}`},
	}
	for _, c := range cases {
		got := FrontMatter([]byte(c.raw))
		if c.want == "" {
			if got != nil {
				t.Errorf("FrontMatter(%.40q) = %v, want nil", c.raw, got)
			}
			continue
		}
		line, _ := compact(got)
		eq(t, "FrontMatter", line, c.want)
	}
}

func TestReadHeadStopsEarly(t *testing.T) {
	r := newRepo(t)
	eq(t, "no front", len(mustHead(t, r.put("big.md", strings.Repeat("x", 20000)))), firstRead)
	eq(t, "closed", len(mustHead(t, r.put("closed.md", "---\ntype: a\n---\n"+strings.Repeat("y", 20000)))), firstRead)
	long := mustHead(t, r.put("long.md", "---\nd: "+strings.Repeat("z", 10000)+"\n---\nbody"))
	eq(t, "long", FrontMatter(long).Value("d"), strings.Repeat("z", 10000))
}

func mustHead(t *testing.T, path string) []byte {
	t.Helper()
	data, ok := ReadHead(path)
	if !ok {
		t.Fatalf("%s を読めない", path)
	}
	return data
}

// --- ccnavi のものでない索引 ------------------------------------------------------------

func TestAForeignIndexIsLeftAlone(t *testing.T) {
	cases := map[string]string{
		"よその道具":      `{"name": "other tool"}` + "\n",
		"壊れた行":       "{not json\n",
		"深い入れ子":      strings.Repeat("[", 100000) + "\n",
		"NaN":        `{"concept_id":"a","directory":".","frontmatter":{"n":NaN},"mtime":"x"}` + "\n",
		"字下げで書けない深さ": deepRow(500),
	}
	for name, text := range cases {
		t.Run(name, func(t *testing.T) {
			r := newRepo(t)
			r.put("a.md", doc("type: guide"))
			r.write(IndexName, text)
			built := r.build()
			eq(t, "built", [4]any{built.Parsed, ids(built.Rows), built.Written, built.Foreign}, [4]any{1, []string{"a"}, []string(nil), []string{IndexName}})
			if !strings.Contains(built.Problems[0], "ccnavi の索引ではない") {
				t.Errorf("problems = %v", built.Problems)
			}
			eq(t, "索引", r.read(IndexName), text)
		})
	}
}

func deepRow(depth int) string {
	return `{"concept_id":"a","directory":".","frontmatter":{"n":` + strings.Repeat("[", depth) + `"deep"` + strings.Repeat("]", depth) + `},"mtime":"x"}` + "\n"
}

func TestAForeignIndexOfAnEmptiedDirectoryIsNotRemoved(t *testing.T) {
	r := newRepo(t)
	r.put("gone/b.md", doc("type: guide"))
	r.put("keep.md", doc("type: guide"))
	testkit.Git(t, r.root, "add", "gone/b.md")
	r.write(index("gone"), "not ours\n")
	os.Remove(r.path("gone/b.md"))
	built := r.build()
	eq(t, "removed", built.Removed, []string(nil))
	if !r.exists(index("gone")) {
		t.Error("よその索引を消した")
	}
}

func TestAnEmptyIndexIsOurs(t *testing.T) {
	r := newRepo(t)
	r.put("a.md", doc("type: guide"))
	r.write(IndexName, "\n")
	eq(t, "written", r.build().Written, []string{IndexName})
}

// 自分の索引を、値の中の U+2028・U+2029・U+0085 や CRLF で割って ccnavi のものでないとしない。
func TestOwnIndexSurvivesLineSeparatorsAndCRLF(t *testing.T) {
	for escape, char := range map[string]string{`\L`: " ", `\P`: " ", `\N`: "\u0085"} {
		r := newRepo(t)
		r.put("a.md", "---\ntype: guide\ntitle: \"x"+escape+"y\"\n---\n")
		r.build()
		if !strings.Contains(r.read(IndexName), char) {
			t.Errorf("%q が索引に生のまま入らない", char)
		}
		built := r.build()
		eq(t, "built", [3]any{built.Foreign, built.Reused, built.Rows[0].Front().Value("title")}, [3]any{[]string(nil), 1, "x" + char + "y"})
	}
	r := newRepo(t)
	r.put("a.md", doc("type: guide"))
	r.build()
	r.write(IndexName, strings.ReplaceAll(r.read(IndexName), "\n", "\r\n"))
	built := r.build()
	eq(t, "crlf", [2]any{built.Foreign, built.Reused}, [2]any{[]string(nil), 1})
}

// --- ツリーの外 --------------------------------------------------------------------------

func TestASymlinkedDirectoryIsNeitherReadNorWritten(t *testing.T) {
	r := newRepo(t)
	outside := testkit.TempRoot(t)
	testkit.Put(t, filepath.Join(outside, "x.md"), doc("type: guide"))
	if err := os.Symlink(outside, r.path("linked")); err != nil {
		t.Skip("ディレクトリのシンボリックリンクを作れない")
	}
	r.put("a.md", doc("type: guide"))
	built := r.build()
	eq(t, "rows", ids(built.Rows), []string{"a"})
	if _, err := os.Stat(filepath.Join(outside, IndexName)); err == nil {
		t.Error("ツリーの外に書いた")
	}
}

// --- 期限 ---------------------------------------------------------------------------

// stub は readHead と past を差し替え、1 回に読める md の数を budget にする。
func stub(t *testing.T, budget *int) {
	t.Helper()
	realRead, realPast := readHead, past
	t.Cleanup(func() { readHead, past = realRead, realPast })
	readHead = func(path string) ([]byte, bool) {
		*budget--
		return realRead(path)
	}
	past = func(deadline time.Time) bool { return !deadline.IsZero() && *budget <= 0 }
}

func TestADirectoryCutShortIsWrittenUpToWhereItGot(t *testing.T) {
	r := newRepo(t)
	r.put("a.md", doc("type: guide"))
	r.put("b.md", doc("type: guide"))
	oldB := `{"concept_id":"b","directory":".","frontmatter":null,"mtime":"x"}`
	r.write(IndexName, oldB+"\n")
	budget := 1
	stub(t, &budget)
	built := r.build(Options{Refresh: true, Parse: true, Deadline: time.Now().Add(time.Hour)})
	eq(t, "built", [2]any{built.TimedOut, built.Written}, [2]any{true, []string{IndexName}})
	eq(t, "索引", r.read(IndexName), `{"concept_id":"a","directory":".","frontmatter":{"type":"guide"},"mtime":"`+noonText+`"}`+"\n"+oldB+"\n")
	built = r.build()
	eq(t, "次の回", [2]any{built.TimedOut, built.Rows[1].Front().Value("type")}, [2]any{false, "guide"})
}

// 大きなディレクトリが毎回打ち切られても、回を重ねれば埋まり、後ろのディレクトリも読む。
func TestABigDirectoryFillsUpOverRuns(t *testing.T) {
	r := newRepo(t)
	for i := range 30 {
		r.put(fmt.Sprintf("docs/d%03d.md", i), doc("type: guide"))
	}
	r.put("zz/late.md", doc("type: adr"))
	var budget int
	stub(t, &budget)
	var seen []int
	for range 3 {
		budget = 12
		built := r.build(Options{Refresh: true, Parse: true, Deadline: time.Now().Add(time.Hour)})
		seen = append(seen, len(built.Rows))
		if !strings.Contains(strings.Join(ids(built.Rows), " "), "zz/late") {
			t.Error("後ろのディレクトリを読まない")
		}
	}
	// 1 回目は zz/late と docs の 11 本、2 回目は docs の続き 12 本、3 回目で残りの 7 本。
	eq(t, "seen", seen, []int{12, 24, 31})
	built := r.build()
	eq(t, "最後", [2]any{built.TimedOut, built.Parsed}, [2]any{false, 0})
}

// --- 一時ファイル --------------------------------------------------------------------

func TestNothingNewShowsInGitStatus(t *testing.T) {
	r := newRepo(t)
	r.put("a.md", doc("type: guide"))
	testkit.Git(t, r.root, "add", "-A")
	testkit.Git(t, r.root, "commit", "-q", "-m", "a")
	r.build()
	if !r.exists(IndexName) {
		t.Fatal("索引が無い")
	}
	eq(t, "status", testkit.Git(t, r.root, "status", "--porcelain", "-uall"), "")
}

func TestStaleLeftoversAreSwept(t *testing.T) {
	r := newRepo(t)
	r.put("a.md", doc("type: guide"))
	stale := r.put(".git/ccnavi-index-1-0.tmp", "x")
	fresh := r.write(".git/ccnavi-index-1-1.tmp", "x")
	r.build()
	if _, err := os.Stat(stale); err == nil {
		t.Error("古い残骸が残った")
	}
	if _, err := os.Stat(fresh); err != nil {
		t.Error("新しい一時ファイルを消した")
	}
}

// --- 別のファイルシステム -----------------------------------------------------------------

func crossDeviceGit(t *testing.T, r *repo) {
	t.Helper()
	real := rename
	t.Cleanup(func() { rename = real })
	gitDir := r.path(".git") + string(filepath.Separator)
	rename = func(src, dst string) error {
		if strings.HasPrefix(src, gitDir) {
			return &os.LinkError{Op: "rename", Old: src, New: dst, Err: syscall.EXDEV}
		}
		return real(src, dst)
	}
}

func leftovers(t *testing.T, dir string) []string {
	names, _ := os.ReadDir(dir)
	var found []string
	for _, n := range names {
		if strings.HasPrefix(n.Name(), localTmpPrefix) {
			found = append(found, n.Name())
		}
	}
	return found
}

func TestWritesThroughATemporaryBesideTheIndex(t *testing.T) {
	r := newRepo(t)
	r.put("docs/a.md", doc("type: guide"))
	r.put("b.md", doc("type: adr"))
	crossDeviceGit(t, r)
	built := r.build()
	eq(t, "built", [2]any{built.Problems, sorted(built.Written)}, [2]any{[]string(nil), []string{IndexName, index("docs")}})
	eq(t, "leftovers", [2]any{leftovers(t, r.path("docs")), leftovers(t, r.root)}, [2]any{[]string(nil), []string(nil)})
	status := testkit.Git(t, r.root, "status", "--porcelain", "--untracked-files=all")
	if strings.Contains(status, IndexName) || strings.Contains(status, localTmpPrefix) {
		t.Errorf("status = %s", status)
	}
}

func TestDoesNotWriteWhenTheTemporaryWouldNotBeIgnored(t *testing.T) {
	r := newRepo(t)
	r.write(gitignoreName, "/docs/"+IndexName+"\n")
	r.put("docs/a.md", doc("type: guide"))
	crossDeviceGit(t, r)
	built := r.build()
	eq(t, "built", [3]any{built.Written, ids(built.Rows), leftovers(t, r.path("docs"))}, [3]any{[]string(nil), []string{"docs/a"}, []string(nil)})
	if !strings.Contains(strings.Join(built.Problems, " "), "git に無視されない") {
		t.Errorf("problems = %v", built.Problems)
	}
}

func TestSweepsOnlyItsOwnStaleLeftovers(t *testing.T) {
	r := newRepo(t)
	r.put("docs/a.md", doc("type: guide"))
	old := time.Now().Add(-tmpStale - time.Minute)
	mine := r.write("docs/.ccnavi-tmp-1-1/"+IndexName, "x")
	theirs := r.write("docs/.ccnavi-tmp-2-2/notes.txt", "keep")
	fresh := r.write("docs/.ccnavi-tmp-3-3/"+IndexName, "x")
	for _, p := range []string{mine, theirs} {
		os.Chtimes(filepath.Dir(p), old, old)
	}
	crossDeviceGit(t, r)
	r.build()
	for p, want := range map[string]bool{filepath.Dir(mine): false, theirs: true, fresh: true} {
		if _, err := os.Stat(p); (err == nil) != want {
			t.Errorf("%s が残る = %v, want %v", p, err == nil, want)
		}
	}
}

// --- pathspec ---------------------------------------------------------------------------

func TestPathsAndEnvironmentAreNotReadAsPathspecMagic(t *testing.T) {
	r := newRepo(t)
	paths := []string{":(exclude)x/" + IndexName, ":!y/" + IndexName, "*/" + IndexName}
	got, err := Ignored(r.root, paths, gitTimeout)
	if err != nil {
		t.Fatal(err)
	}
	eq(t, "ignored", len(got), len(paths))
	if !ospath.Windows {
		r.put(":(exclude)x/a.md", doc("type: guide"))
		eq(t, "written", r.build().Written, []string{":(exclude)x/" + IndexName})
	}
	for _, name := range []string{"GIT_LITERAL_PATHSPECS", "GIT_ICASE_PATHSPECS", "GIT_GLOB_PATHSPECS"} {
		t.Setenv(name, "1")
		if rows := ids(r.build().Rows); len(rows) == 0 {
			t.Errorf("%s で索引が空", name)
		}
		os.Unsetenv(name)
	}
}
