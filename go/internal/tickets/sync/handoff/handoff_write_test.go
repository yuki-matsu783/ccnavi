package handoff

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// 書いたあとで下書きが書き換わっていたら、下書きを消さずに残す（取り込んでいない編集を失わない）。
func TestADraftRewrittenAfterTheWriteIsKept(t *testing.T) {
	root, conf := workspace(t, map[string]string{draftRoot + docsRel: "new\n"})
	plan := Prepare(conf, root, nil)
	draft := filepath.Join(root, filepath.FromSlash(draftRoot+docsRel))
	if failure, _ := writeOne(plan.Tree, entryOf(t, plan, docsRel)); failure != "" {
		t.Fatal(failure)
	}
	if err := os.WriteFile(draft, []byte("agent kept editing\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	done := &Applied{OK: true}
	cleanDraft(plan.Tree, entryOf(t, plan, docsRel), done)
	if len(done.RemovedDrafts) != 0 || len(done.KeptDrafts) != 1 || done.KeptDrafts[0].Path != draftRoot+docsRel {
		t.Fatalf("%+v", done)
	}
	if raw, _ := os.ReadFile(draft); string(raw) != "agent kept editing\n" {
		t.Errorf("下書き = %q", raw)
	}
}

// 書いたあとの読み直しが合わなかった 1 本も、書く前のバイト列へ戻す。
func TestAFileThatFailsTheReadBackIsPutBack(t *testing.T) {
	root, conf := workspace(t, map[string]string{
		draftRoot + ".ccnavi/CCNAVI.md": "new top\n", ".ccnavi/CCNAVI.md": "old top\n",
	})
	plan := Prepare(conf, root, nil)
	saved := readBack
	t.Cleanup(func() { readBack = saved })
	readBack = func(string, string) ([]byte, string) { return []byte("garbled\n"), "" }
	done := Apply(plan, plan.Expect(), false)
	if done.OK || !strings.Contains(done.Error, "読み直すと中身が違う") || !strings.Contains(done.Error, "書いたものは元に戻しました") {
		t.Fatalf("%+v", done)
	}
	if raw, _ := os.ReadFile(filepath.Join(root, ".ccnavi", "CCNAVI.md")); string(raw) != "old top\n" {
		t.Errorf("戻していない: %q", raw)
	}
}
