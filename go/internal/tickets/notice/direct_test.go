package notice

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/model/pyoracle"
)

// directWorkspace は直接作業のワークツリーを並べたワークスペース。記録の更新時刻は i 番目が i 分前。
//
//	new-1-b   コミットが 1 つ
//	old-1-a   未コミットの変更がある（一番古い）
//	tick-1-c  同じ名前の提案があり、チケットに結び付く（載せない）
//	none-1-d  記録が無い（載せない）
//	rec-1-a   取り込みの途中の記録がある
//	done-1-e  片付けた記録のコピーが今の HEAD と同じ
//	nosnap-1  記録に起点が無い
//	w-NN-x    数合わせ（上限を超えた分は本数だけ言う）
func directWorkspace(t *testing.T, fill int) string {
	root := workspace(t)
	common := filepath.Join(root, ".git")
	snap := headOf(t, root)
	base := time.Date(2026, 1, 5, 12, 0, 0, 0, time.UTC)
	age := 0
	record := func(name string, lines ...string) {
		if lines == nil {
			lines = []string{"version 4", "base_branch main", "snap_sha " + snap}
		}
		stamp := put(t, filepath.Join(common, tree.DirectDir, name), strings.Join(lines, "\n")+"\n")
		age++
		touch(t, stamp, base.Add(-time.Duration(age)*time.Minute))
	}
	newer := addWorktree(t, root, "new-1-b", "main")
	put(t, filepath.Join(newer, "work.txt"), "y\n")
	commitAll(t, newer, "work")
	record("new-1-b")
	addWorktree(t, root, "rec-1-a", "main")
	record("rec-1-a")
	put(t, filepath.Join(common, integrate.RecordDir, "rec-1-a", "marker"), "version 4\n")
	done := addWorktree(t, root, "done-1-e", "main")
	record("done-1-e")
	put(t, filepath.Join(common, integrate.DoneDir, "done-1-e"), "version 4\nbranch_sha "+headOf(t, done)+"\n")
	addWorktree(t, root, "nosnap-1", "main")
	record("nosnap-1", "version 4", "base_branch main")
	addWorktree(t, root, "tick-1-c", "main")
	record("tick-1-c")
	put(t, filepath.Join(root, "wip", "proposals", "todo", "tick-1-c.md"), "---\n---\n")
	addWorktree(t, root, "none-1-d", "main")
	for i := range fill {
		name := fmt.Sprintf("w-%02d-x", i)
		addWorktree(t, root, name, "main")
		record(name)
	}
	old := addWorktree(t, root, "old-1-a", "main")
	put(t, filepath.Join(old, "draft.txt"), "d\n")
	record("old-1-a")
	return root
}

const directOracle = `
import json, sys, time
from ccnavi.infra import settings
from ccnavi.tickets.notice import direct_worktrees
c = json.load(sys.stdin)
out = {}
for key, root in c["roots"].items():
    conf, _ = settings.load(root)
    out[key] = direct_worktrees.session_text(root, conf, time.monotonic() + 60).replace(root, "<root>")
json.dump(out, sys.stdout, ensure_ascii=False)
`

func TestDirectWorktreesMatchesPython(t *testing.T) {
	empty := workspace(t)
	addWorktree(t, empty, "no-record", "main")
	roots := map[string]string{"empty": empty, "few": directWorkspace(t, 0), "many": directWorkspace(t, 8)}
	want := pyoracle.Answers[map[string]any](t, "direct", directOracle, map[string]any{"roots": roots})
	got := map[string]any{}
	for key, root := range roots {
		got[key] = rel(root, DirectWorktrees(settingsOf(t, root), root, time.Now().Add(time.Minute)))
	}
	sameAsPython(t, got, want)
	few := got["few"].(string)
	for _, s := range []string{"new-1-b（取り込み先 main）: 未コミット なし、起点から 1 コミット、integrate の記録 なし",
		"rec-1-a（取り込み先 main）: 未コミット なし、起点から 0 コミット、integrate の記録 あり（取り込みの途中）",
		"done-1-e（取り込み先 main）: 未コミット なし、起点から 0 コミット、integrate の記録 あり（片付け済み）",
		"nosnap-1（取り込み先 main）: 未コミット なし、起点から 不明",
		"old-1-a（取り込み先 main）: 未コミット あり"} {
		if !strings.Contains(few, s) {
			t.Errorf("%q が無い:\n%s", s, few)
		}
	}
	if strings.Contains(few, "tick-1-c") || strings.Contains(few, "none-1-d") {
		t.Errorf("チケット作業と記録の無いものは載せない:\n%s", few)
	}
	if got["empty"] != "" {
		t.Errorf("直接作業が無ければ空: %q", got["empty"])
	}
	if lines := strings.Count(got["many"].(string), "\n  - "); lines != directLimit {
		t.Errorf("載せるのは %d 本まで: %d", directLimit, lines)
	}
}
