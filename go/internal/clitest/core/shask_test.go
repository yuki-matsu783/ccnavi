package core

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// `ccnavi tree parent` の約束。sh はチケットの YAML を自分で読まず、これに聞く。
// 答えは頭の行のあとに 1 行 1 項目（`<キー> <値>`）。読むだけで、何も書かない。
// 答えの行の並び・親の見分け方・push のゲートが聞く 5 行は、go/internal/entry/shask の見本（TestAnswersMatchGolden。
// ファイルの形ごとの場面を含む）が 1 字ずつ見る。ここは、実行ファイルの入口（`tree parent`）を通して答えが返ることだけを見る。
func TestShAskTheAnswerComesThroughTheEntry(t *testing.T) {
	t.Parallel()
	ws := emptyRoot(t)
	mkdir(t, filepath.Join(ws, ".claude"))
	tree := mkdir(t, filepath.Join(ws, ".claude", "worktrees", "p0001"))
	writeFile(t, tree, ".ccnavi/approved/doing/p0001.md", "---\nticket: p0001\nmr: 430\nstarted_at: 2026-10-18T00:00:00Z\n---\n本文\n")
	r := run.Ccnavi(t, []string{"--root", ws, "tree", "parent", tree, "p0001"})
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	out := strings.Split(strings.TrimRight(r.Stdout, "\n"), "\n")
	if out[0] != "tree 1" {
		t.Errorf("head = %q", out[0])
	}
	found := map[string]string{}
	for _, line := range out[1:] {
		k, v, _ := strings.Cut(line, " ")
		found[k] = v
	}
	for k, want := range map[string]string{"parent": "yes", "approved": "yes", "started": "yes", "mr": "430"} {
		if found[k] != want {
			t.Errorf("%s = %q, want %q", k, found[k], want)
		}
	}
}
