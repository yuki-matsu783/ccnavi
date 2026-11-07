package archive

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/model/place"

	"ccnavi/internal/tickets/testkit"
)

func closedText(name, parent string, phase int) string {
	lines := []string{"---", "version: 1", "ticket: " + name}
	if parent == "" {
		lines = append(lines, "mr: 1", "issue: 1")
	} else {
		lines = append(lines, "parent: "+parent, "phase: "+string(rune('0'+phase)))
	}
	lines = append(lines, "human_review:", "  required: false", "  reason: t", "title: "+name+" のタイトル",
		"rationale: r", "allow:", "  - match: Write|Edit", `    glob: "src/*"`, `started_at: ""`,
		"completed_at: '2026-01-02T00:00:00+09:00'", `base_sha: ""`, "---", "", "本文", "")
	return strings.Join(lines, "\n")
}

type fixture struct{ root, approved string }

// newFixture は閉じた親 2 つ（i0001・old）と、開いた親（open）の子がある承認済みの領域を作る。git は使わない。
func newFixture(t *testing.T) fixture {
	t.Helper()
	root := t.TempDir()
	approved := filepath.Join(root, "tree", ".ccnavi", "approved")
	for _, c := range [][2]string{{"i0001", ""}, {"i0001-01-01", "i0001"}, {"old", ""}, {"old-01-01", "old"}, {"open-01-01", "open"}} {
		phase := 0
		if c[1] != "" {
			phase = 1
		}
		testkit.Put(t, filepath.Join(approved, "done", c[0]+".md"), closedText(c[0], c[1], phase))
	}
	testkit.Put(t, filepath.Join(approved, "doing", "open.md"), closedText("open", "", 0))
	testkit.Put(t, filepath.Join(approved, "phases", "i0001", "1.reviewed.json"), "{}")
	testkit.Put(t, filepath.Join(approved, "phases", "i0001", "i0001-01-01.risk.json"), "{}")
	testkit.Put(t, filepath.Join(approved, "phases", "i0001", "sub", "a.json"), "{}")
	testkit.Put(t, filepath.Join(approved, "phases", "i0001", ".x.123.part"), "{}")
	testkit.Put(t, filepath.Join(approved, "phases", "open", "1.pending.json"), "{}")
	testkit.Put(t, filepath.Join(approved, "flows", "i0001-01-01.yml"), "steps: []\n")
	testkit.Put(t, filepath.Join(approved, "flows", "open-01-01.yml"), "steps: []\n")
	return fixture{root, approved}
}

func (f fixture) rel(rel string) string { return filepath.Join(f.approved, filepath.FromSlash(rel)) }

func TestPlanTakesOnlyClosedFamilies(t *testing.T) {
	f := newFixture(t)
	if got := ClosedParents(f.approved); !reflect.DeepEqual(got, []string{"i0001", "old"}) {
		t.Fatal(got)
	}
	todo := PlanMove(f.approved, ClosedParents(f.approved), "", "")
	if !reflect.DeepEqual(todo.parents, []string{"i0001", "old"}) {
		t.Error(todo.parents)
	}
	// 子のチケットが先、親のチケットが最後（止まったときに親が done/ に残る）。
	if !reflect.DeepEqual(todo.tickets, []string{"i0001-01-01", "old-01-01", "i0001", "old"}) {
		t.Error(todo.tickets)
	}
	// ディレクトリのファイルを先に、下のディレクトリを後に（os.walk の順）。書きかけの一時ファイルは移さない。
	want := []string{"phases/i0001/1.reviewed.json", "phases/i0001/i0001-01-01.risk.json", "phases/i0001/sub/a.json",
		"flows/i0001-01-01.yml", "done/i0001-01-01.md", "done/old-01-01.md", "done/i0001.md", "done/old.md"}
	if !reflect.DeepEqual(todo.files, want) {
		t.Errorf("%q", todo.files)
	}
}

func TestMoveCopiesThenRemoves(t *testing.T) {
	f := newFixture(t)
	todo := PlanMove(f.approved, []string{"i0001"}, "", "")
	moved, failed := Move(f.root, "", f.approved, todo, "", "")
	if failed != "" || !reflect.DeepEqual(moved, todo.files) {
		t.Fatal(moved, failed)
	}
	base := BaseDir(f.root, "")
	if base != filepath.Join(f.root, "logs", "archive", "self") {
		t.Error(base)
	}
	for _, rel := range todo.files {
		if ospath.DiskLexists(f.rel(rel)) || !ospath.IsFile(filepath.Join(base, filepath.FromSlash(rel))) {
			t.Error(rel)
		}
	}
	if ospath.DiskLexists(f.rel("phases/i0001/sub")) {
		t.Error("空になったディレクトリが残る")
	}
	if got := IDs(f.root, ""); !reflect.DeepEqual(got, map[string]bool{"i0001": true, "i0001-01-01": true}) {
		t.Error(got)
	}
	if got := IDsAny(f.root); !reflect.DeepEqual(got, IDs(f.root, "")) || len(IDs(f.root, "other")) != 0 {
		t.Error("全リポジトリの識別子", got)
	}
	if p := ArchivedParent(f.root, "", "i0001"); p == nil || p.ID != "i0001" || ArchivedParent(f.root, "", "i0001-01-01") != nil {
		t.Error("ArchivedParent")
	}
	if found := FindAny(f.root, "i0001-01-01"); len(found) != 1 || found[0].State != place.Done || found[0].Project != "" {
		t.Error(found)
	}
	// 大文字小文字だけが違う識別子も同じものとして引く。
	if c, ok := ArchivedFields(f.root, "", "I0001"); !ok || c.Ticket != "i0001" {
		t.Error(c, ok)
	}
	if got := ArchivedParents(f.root, ""); !reflect.DeepEqual(got, []string{"i0001"}) {
		t.Error(got)
	}
	if n := len(ClosedTickets(f.root)); n != 2 {
		t.Error(n)
	}
}

func TestMoveAgain(t *testing.T) {
	f := newFixture(t)
	base := BaseDir(f.root, "")
	// 前の回の残り（同じ中身。改行だけ違う）が在れば、コピーせずに元だけ消す。
	testkit.Put(t, filepath.Join(base, "done", "old.md"), strings.ReplaceAll(closedText("old", "", 0), "\n", "\r\n"))
	if moved, failed := Move(f.root, "", f.approved, PlanMove(f.approved, []string{"old"}, "", ""), "", ""); failed != "" || !slices.Contains(moved, "done/old.md") {
		t.Fatal(moved, failed)
	}

	f = newFixture(t)
	base = BaseDir(f.root, "")
	// 違う中身のチケットは上書きしない。
	testkit.Put(t, filepath.Join(base, "done", "old.md"), closedText("old", "", 0)+"違う\n")
	moved, failed := Move(f.root, "", f.approved, PlanMove(f.approved, []string{"old"}, "", ""), "", "")
	if !strings.Contains(failed, "違う中身") || slices.Contains(moved, "done/old.md") || !ospath.IsFile(f.rel("done/old.md")) {
		t.Fatal(moved, failed)
	}
	// マーカーは今の中身で置き換えて進む（push できずに戻した後の打ち直し）。
	testkit.Put(t, filepath.Join(base, "phases", "i0001", "1.reviewed.json"), `{"at": "前"}`)
	if _, failed := Move(f.root, "", f.approved, PlanMove(f.approved, []string{"i0001"}, "", ""), "", ""); failed != "" {
		t.Fatal(failed)
	}
	if data, _ := os.ReadFile(filepath.Join(base, "phases", "i0001", "1.reviewed.json")); string(data) != "{}" {
		t.Error(string(data))
	}
}

func TestLinksAreNotFollowed(t *testing.T) {
	f := newFixture(t)
	elsewhere := t.TempDir()
	if err := os.MkdirAll(filepath.Join(f.root, "logs"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(elsewhere, filepath.Join(f.root, "logs", "archive")); err != nil {
		t.Skip("シンボリックリンクを作れない")
	}
	if _, failed := Move(f.root, "", f.approved, PlanMove(f.approved, []string{"old"}, "", ""), "", ""); !strings.Contains(failed, "シンボリックリンク") {
		t.Error(failed)
	}
	if names, _ := os.ReadDir(elsewhere); len(names) != 0 || len(IDs(f.root, "")) != 0 {
		t.Error("リンクの先に書いた")
	}

	f = newFixture(t)
	elsewhere = t.TempDir()
	base := BaseDir(f.root, "")
	_ = os.MkdirAll(base, 0o755)
	_ = os.Symlink(elsewhere, filepath.Join(base, "phases"))
	if _, failed := Move(f.root, "", f.approved, PlanMove(f.approved, []string{"i0001"}, "", ""), "", ""); !strings.Contains(failed, "シンボリックリンク") {
		t.Error(failed)
	}
	if !ospath.IsFile(f.rel("done/i0001.md")) {
		t.Error("止まったのに親を消した")
	}
	// done/ のリンクは閉じた親として拾わない。
	_ = os.Symlink(f.rel("done/old.md"), f.rel("done/lnk.md"))
	if slices.Contains(ClosedParents(f.approved), "lnk") {
		t.Error("リンクを拾った")
	}
}

// 子は名前の形ではなく parent: 欄で結ぶ。チケットの無い子のフローだけを子の形で結ぶ。
func TestFamilyIsReadFromTheParentField(t *testing.T) {
	f := newFixture(t)
	testkit.Put(t, f.rel("done/rel.md"), closedText("rel", "", 0))
	testkit.Put(t, f.rel("done/rel-01-01.md"), closedText("rel-01-01", "", 0))
	if todo := PlanMove(f.approved, []string{"rel"}, "", ""); !reflect.DeepEqual(todo.tickets, []string{"rel"}) {
		t.Error(todo.tickets)
	}
	testkit.Put(t, f.rel("done/i0001-02-01.md"), closedText("i0001-02-01", "i0001", 2))
	testkit.Put(t, f.rel("flows/i0001-02-01.yml"), "steps: []\n")
	testkit.Put(t, f.rel("flows/i0001-03-02.yml"), "steps: []\n")
	testkit.Put(t, f.rel("doing/i0001-02.md"), closedText("i0001-02", "", 0))
	testkit.Put(t, f.rel("flows/i0001-02.yml"), "steps: []\n")
	todo := PlanMove(f.approved, []string{"i0001"}, "", "")
	if !reflect.DeepEqual(todo.tickets, []string{"i0001-01-01", "i0001-02-01", "i0001"}) {
		t.Error(todo.tickets)
	}
	for _, rel := range []string{"flows/i0001-03-02.yml", "flows/i0001-02-01.yml"} {
		if !slices.Contains(todo.files, rel) {
			t.Error(rel)
		}
	}
	for _, rel := range []string{"flows/i0001-02.yml", "flows/open-01-01.yml"} {
		if slices.Contains(todo.files, rel) {
			t.Error(rel)
		}
	}
}

func TestReadyMark(t *testing.T) {
	f := newFixture(t)
	key := treeKey(f.approved)
	if failed := noteReady(f.root, "", "i0001", f.approved, []string{"done/a.md", "../x", "src/y"}, "h1"); failed != "" {
		t.Fatal(failed)
	}
	if failed := noteReady(f.root, "", "i0001", f.approved, []string{"phases/b"}, "h1"); failed != "" {
		t.Fatal(failed)
	}
	if got := ReadyFiles(f.root, "", f.approved, "h1"); !reflect.DeepEqual(got, map[string]bool{"done/a.md": true, "phases/b": true}) {
		t.Error(got)
	}
	// ツリーが進めば、マーカーは以後の削除には効かない。
	if got := ReadyFiles(f.root, "", f.approved, "h2"); len(got) != 0 {
		t.Error(got)
	}
	if !ReadyStarted(f.root, "", "i0001", f.approved) || ReadyStarted(f.root, "", "i0001", f.root) {
		t.Error("ReadyStarted")
	}
	data, _ := ArchivedBytes(f.root, "", "ready/i0001.json")
	want := "{\n \"parent\": \"i0001\",\n \"tree\": " + quoteJSON(key) + ",\n \"head\": \"h1\",\n \"files\": [\n  \"done/a.md\",\n  \"phases/b\"\n ]\n}"
	if string(data) != want {
		t.Errorf("%s", data)
	}
	for rel, ok := range map[string]bool{"done/a": true, "flows/x.yml": true, "done/../x": false, "src/x": false, "phases//x": false} {
		if InReadyPlaces(rel) != ok {
			t.Error(rel)
		}
	}
}

func quoteJSON(s string) string {
	return `"` + strings.ReplaceAll(strings.ReplaceAll(s, `\`, `\\`), `"`, `\"`) + `"`
}

func TestHolds(t *testing.T) {
	if !Holds([]byte("a\r\nb"), true, []byte("a\nb")) || Holds(nil, false, []byte("")) || Holds([]byte("a"), true, []byte("b")) {
		t.Error("Holds")
	}
}

// 回帰: 名前の末尾が改行の書きかけを、一時ファイルと見ずに移していた。末尾の改行 1 つは許し、2 つは許さない。
func TestTempNameAllowsOneTrailingNewline(t *testing.T) {
	for name, want := range map[string]bool{
		".x.part": true, ".x.part\n": true, ".x.part\n\n": false, ".x.part.1": true, ".x.part.1\n": true,
		"x.part": false, ".x.partx": false, ".x.part/y": false,
	} {
		if got := tempName.MatchString(name); got != want {
			t.Errorf("tempName(%q) = %v, want %v", name, got, want)
		}
	}
}
