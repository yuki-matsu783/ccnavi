package closeout

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/ticket"
)

func closedText(name string) string {
	return "---\nversion: 1\nticket: " + name + "\nmr: 1\nissue: 1\nhuman_review:\n  required: false\n  reason: t\n" +
		"title: " + name + " のタイトル\nrationale: r\nallow:\n  - match: Write|Edit\n    glob: \"src/*\"\n" +
		"started_at: \"\"\ncompleted_at: '2026-01-02T00:00:00+09:00'\nbase_sha: \"\"\n---\n\n本文\n"
}

// 回帰: ワークスペースルートの承認済みの領域から退避しない（done/ には他の親子のチケットも在り、まとめて消す）。
func TestArchiveRefusesTheWorkspaceRoot(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("git", "init", "-q", "-b", "main", root).CombinedOutput(); err != nil {
		t.Fatalf("%v %s", err, out)
	}
	approved := filepath.Join(root, ".ccnavi", "approved")
	for _, name := range []string{"i0001", "other"} {
		put(t, filepath.Join(approved, "done", name+".md"), closedText(name))
	}
	conf, _ := config.Load(root)
	parent, _ := ticket.Parse(closedText("i0001"))
	if parent == nil {
		t.Fatal("見本を読めない")
	}
	var o, e bytes.Buffer
	if code := archiveClosed(&o, &e, conf, root, parent, approved, "note.md", nil); code == 0 {
		t.Fatal("ワークスペースルートから退避した")
	}
	if !strings.Contains(e.String(), "親のワークツリー") || o.Len() != 0 {
		t.Errorf("%q %q", o.String(), e.String())
	}
	if _, err := os.Stat(filepath.Join(approved, "done", "other.md")); err != nil {
		t.Error("他の親のチケットが消えた")
	}
	if archive.IDs(root, "")["other"] {
		t.Error("他の親のチケットを退避した")
	}
}
