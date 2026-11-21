package templates

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/testkit"
)

const linkSample = "description: d\nplan:\n  - {type: design}\n"

// リンクは読まない。ファイルのシンボリックリンク・ハードリンク・置き場のシンボリックリンクは、苦情に理由を持ち、
// 中身を取り込まない。
func TestLinksAreNotRead(t *testing.T) {
	root := testkit.TempRoot(t)
	dir := filepath.Join(root, ".ccnavi", "approved", "templates")
	outside := testkit.Put(t, filepath.Join(root, "outside.yml"), linkSample)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	firstError := func(e *Entry) string {
		for _, p := range e.Notes {
			if p.Severity == rules.Error {
				return p.Detail
			}
		}
		return ""
	}

	t.Run("ファイルのシンボリックリンク", func(t *testing.T) {
		link := filepath.Join(dir, "research.yml")
		if err := os.Symlink(outside, link); err != nil {
			t.Skip("リンクを作れない")
		}
		defer os.Remove(link)
		e := ReadEntry("self", root, link, true)
		if e.Template != nil || !strings.Contains(firstError(e), "シンボリックリンク") {
			t.Errorf("%+v %q", e.Template, firstError(e))
		}
	})

	t.Run("ハードリンクのファイル", func(t *testing.T) {
		link := filepath.Join(dir, "hard.yml")
		if err := os.Link(outside, link); err != nil {
			t.Skip("ハードリンクを作れない")
		}
		defer os.Remove(link)
		e := ReadEntry("self", root, link, true)
		if e.Template != nil || !strings.Contains(firstError(e), "ハードリンク") {
			t.Errorf("%+v %q", e.Template, firstError(e))
		}
	})

	t.Run("置き場のシンボリックリンク", func(t *testing.T) {
		testkit.Put(t, filepath.Join(root, "elsewhere", "research.yml"), linkSample)
		place := filepath.Join(root, "linked-place")
		if err := os.Symlink(filepath.Join(root, "elsewhere"), place); err != nil {
			t.Skip("リンクを作れない")
		}
		l := ReadLayer("self", root, place, true)
		if len(l.Entries) != 0 || !strings.Contains(l.Unreadable, "シンボリックリンク") {
			t.Errorf("%d 本 %q", len(l.Entries), l.Unreadable)
		}
	})
}
