package ticketkit

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

func TestMain(m *testing.M) { base.Main(m) }

func TestTicketTextShapes(t *testing.T) {
	t.Parallel()
	parent := TicketText("i0001", TicketOpts{Allow: []string{"src/*"}})
	for _, want := range []string{"ticket: i0001\n", "mr: 1\n", "issue: 1\n", "plan:\n", "phases:\n", "required: true\n", `glob: "src/*"`} {
		if !strings.Contains(parent, want) {
			t.Errorf("親の本文に %q が無い:\n%s", want, parent)
		}
	}
	child := TicketText("i0001-01-01", TicketOpts{Parent: "i0001", Phase: 1, NoReview: true, Predecessors: []string{"i0001-01-00"}})
	for _, want := range []string{"parent: i0001\n", "phase: 1\n", "required: false\n", "predecessors: [i0001-01-00]\n"} {
		if !strings.Contains(child, want) {
			t.Errorf("子の本文に %q が無い:\n%s", want, child)
		}
	}
	for _, not := range []string{"mr:", "issue:", "plan:"} {
		if strings.Contains(child, not) {
			t.Errorf("子の本文に %q がある:\n%s", not, child)
		}
	}
}

func TestFamilyApprovesAndStartsTheChildren(t *testing.T) {
	t.Parallel()
	tr := New(t)
	tr.Family(FamilyOpts{})
	for _, name := range []string{"i0001", "i0001-01-01", "i0001-01-02"} {
		if !Exists(tr.ApprovedPath("doing", name)) {
			t.Errorf("%s が doing/ に無い", name)
		}
	}
	if !strings.Contains(Read(t, tr.ApprovedPath("doing", "i0001-01-01")), "started_at: \"20") {
		t.Error("子が着手済みでない")
	}
}

// Cached は同じ鍵なら、前のテストが木に残したものを写しへ戻して返す（同じパスで）。子の名前に依存しない。
func TestCachedRestoresTheTreeToTheSavedCopy(t *testing.T) {
	for _, names := range [][2]string{{"use", "use"}, {"first", "second"}} {
		key := "kit-test-restore-" + names[0] + names[1]
		var first string
		for i, name := range names {
			t.Run(name, func(t *testing.T) {
				tr := Cached(t, key, Options{}, func(tr *Tree) {
					Write(tr.T, filepath.Join(tr.Root, "built.txt"), "built\n")
				})
				if i == 0 {
					first = tr.Root
					Write(t, filepath.Join(tr.Root, "junk.txt"), "x\n")
					return
				}
				if tr.Root != first {
					t.Errorf("パスが変わった: %s → %s", first, tr.Root)
				}
				if Exists(filepath.Join(tr.Root, "junk.txt")) {
					t.Error("前のテストの残りが戻らない")
				}
				if !Exists(filepath.Join(tr.Root, "built.txt")) {
					t.Error("手順の結果が戻らない")
				}
			})
		}
	}
}

// 子の間に、別の（並行の）親テストが同じ鍵を使っても詰まらずに通る。
func TestCachedKeyIsSharedWithAnotherParallelTest(t *testing.T) {
	use := func(t *testing.T) *Tree {
		return Cached(t, "kit-test-shared", Options{}, func(tr *Tree) {
			Write(tr.T, filepath.Join(tr.Root, "built.txt"), "built\n")
		})
	}
	t.Run("group", func(t *testing.T) {
		t.Run("a", func(t *testing.T) { use(t) })
		t.Run("other", func(t *testing.T) {
			t.Parallel()
			use(t)
		})
		t.Run("b", func(t *testing.T) { use(t) })
	})
}

// 同じ鍵でも Options が違えば別の木になる（先着の木に引きずられない）。
func TestCachedKeyIncludesTheOptions(t *testing.T) {
	for _, tc := range []string{"a: 1\n", "", "b: 2\n"} {
		t.Run("phases", func(t *testing.T) {
			tr := Cached(t, "kit-test-options", Options{Phases: tc}, func(tr *Tree) {})
			if tr.Phases != tc {
				t.Errorf("Phases = %q, want %q", tr.Phases, tc)
			}
		})
	}
}

func TestFixtureUpdateKeepsTheOtherFields(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	f := Fixture{T: t, Path: filepath.Join(dir, "fx.json")}
	Write(t, f.Path, `{"host": "fixture", "mr": {"number": 7}}`)
	f.SetThreads(Thread{ID: "t1", URL: "u1", Body: "b"})
	data := f.Data()
	if data["host"] != "fixture" || len(data["threads"].([]any)) != 1 {
		t.Errorf("data = %v", data)
	}
	if _, err := os.Stat(f.Path); err != nil {
		t.Fatal(err)
	}
}

// PortableText は大文字小文字と区切り（\ と /）の違いを同じものとして <root> にそろえる。
func TestPortableTextMatchesTheRootIgnoringCaseAndSeparators(t *testing.T) {
	t.Parallel()
	got := PortableText("C:\\Ws\\x and c:/ws/y", "c:/WS")
	if want := "<root>/x and <root>/y"; got != want {
		t.Errorf("PortableText = %q, want %q", got, want)
	}
}

// 解いたパスと、張った symlink の両方が <root> になる（張れない機械では飛ばす）。
func TestPortableTextReplacesASymlinkAndItsTarget(t *testing.T) {
	t.Parallel()
	target := t.TempDir()
	link := filepath.Join(t.TempDir(), "link")
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("symlink を張れない: %v", err)
	}
	resolved, err := filepath.EvalSymlinks(link)
	if err != nil {
		t.Fatal(err)
	}
	got := PortableText(filepath.Join(link, "x")+" and "+filepath.Join(resolved, "y"), link)
	if want := "<root>/x and <root>/y"; got != want {
		t.Errorf("PortableText = %q, want %q", got, want)
	}
}

// WriteFixture は末尾の改行を 1 つにそろえて書く。
func TestWriteFixtureEndsWithOneNewline(t *testing.T) {
	t.Parallel()
	path := filepath.Join(base.TempDir(t), "fixture.json")
	WriteFixture(t, path, "a\n\n")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "a\n" {
		t.Errorf("WriteFixture の中身 = %q, want %q", data, "a\n")
	}
}
