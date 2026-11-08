package approval

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/testkit"
)

// 子の連番は、手元の退避（ready が片付けた閉じた親子のチケット）の番号も使ったものとして数える。同じフェーズの
// 番号だけを数え、親の識別子は大文字小文字を区別しない。
func TestNextChildIDSkipsTheArchivedNumbers(t *testing.T) {
	root := testkit.Workspace(t)
	conf := testkit.Settings(t, root)
	base := archive.BaseDir(root, "")
	testkit.Put(t, filepath.Join(base, "done", "i0001.md"), parentText("i0001", "  - research"))
	testkit.Put(t, filepath.Join(base, "done", "i0001-01-03.md"), childText("i0001-01-03", "i0001", "1", "src/*"))
	testkit.Put(t, filepath.Join(base, "done", "i0001-02-07.md"), childText("i0001-02-07", "i0001", "2", "src/*"))
	for _, c := range []struct {
		parent string
		phase  int
		want   string
	}{{"i0001", 1, "i0001-01-04"}, {"I0001", 1, "I0001-01-04"}, {"i0001", 2, "i0001-02-08"}, {"i0001", 3, "i0001-03-01"}} {
		if got, err := NextChildID(conf, root, c.parent, c.phase); err != nil || got != c.want {
			t.Errorf("%s %d: %q %v", c.parent, c.phase, got, err)
		}
	}
}
