package handoff

import (
	"os"
	"path/filepath"
	"testing"
)

// 書いたあとで下書きが書き換わっていたら、下書きを消さずに残す（取り込んでいない編集を失わない）。
func TestADraftRewrittenAfterTheWriteIsKept(t *testing.T) {
	root, conf := workspace(t, map[string]string{draftRoot + docsRel: "new\n"})
	plan := Prepare(conf, root, nil)
	draft := filepath.Join(root, filepath.FromSlash(draftRoot+docsRel))
	if failure := writeOne(plan.Tree, entryOf(t, plan, docsRel)); failure != "" {
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
