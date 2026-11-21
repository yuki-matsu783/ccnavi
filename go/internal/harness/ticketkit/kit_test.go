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

// Cached は同じ鍵なら、前のテストが木に残したものを写しへ戻して返す（同じパスで）。
func TestCachedRestoresTheTreeToTheSavedCopy(t *testing.T) {
	var first string
	for i := range 2 {
		t.Run("use", func(t *testing.T) {
			tr := Cached(t, "kit-test-restore", Options{}, func(tr *Tree) {
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
