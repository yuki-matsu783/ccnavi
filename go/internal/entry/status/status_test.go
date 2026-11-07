package status

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// Python の entry/status と同じワークスペースを読ませ、出力・標準エラー・終了コードが 1 字も違わないことを見る。
// 置き場のファイルの git での姿（push 済み・未 push・変更あり・未コミット・追跡参照なし）、作業中・レビュー待ち・閉じた
// （完了・取り消し）・承認待ち・改版・同じ識別子が 2 か所、先行の待ち、親子の引数の読み（子の識別子・無い親・2 件）を並べる。

func statusWorkspace(t *testing.T) string {
	root := testkit.Workspace(t, "i0001", "i0001-01-01")
	testkit.Remote(t, root)
	home := testkit.Worktree(root, "i0001")
	put := func(rel, text string) { testkit.Put(t, filepath.Join(home, filepath.FromSlash(rel)), text) }
	started := `started_at: "2026-01-01T00:00:00+0900"`
	parent := strings.Replace(testkit.ParentText("i0001", []string{"  - work", "  - {type: work, after: [1]}"}),
		`started_at: ""`, started, 1)
	put(".ccnavi/approved/doing/i0001.md", parent)
	put(".ccnavi/approved/doing/i0001-01-01.md", testkit.ChildText("i0001-01-01", "i0001", "1", started, `base_sha: "abc"`))
	testkit.Commit(t, home, "pushed")
	testkit.Git(t, home, "push", "-q", "origin", "i0001")
	put(".ccnavi/approved/doing/i0001-01-01.md", testkit.ChildText("i0001-01-01", "i0001", "1", started))
	put(".ccnavi/approved/doing/i0001-01-08.md", testkit.ChildText("i0001-01-08", "i0001", "1"))
	put(".ccnavi/approved/doing/i0001-02-01.md", testkit.ChildText("i0001-02-01", "i0001", "2"))
	testkit.Git(t, home, "add", ".ccnavi/approved/doing/i0001-01-08.md", ".ccnavi/approved/doing/i0001-02-01.md")
	testkit.Git(t, home, "commit", "-q", "-m", "unpushed")
	put("wip/proposals/review/i0001-01-03.md", testkit.ChildText("i0001-01-03", "i0001", "1", started,
		`completed_at: "2026-01-02T00:00:00+0900"`))
	put(".ccnavi/approved/done/i0001-01-04.md", testkit.ChildText("i0001-01-04", "i0001", "1", started,
		`completed_at: "2026-01-02T00:00:00+0900"`, `merged_sha: "0123456789abcdef0123"`))
	put(".ccnavi/approved/done/i0001-01-05.md", testkit.ChildText("i0001-01-05", "i0001", "1",
		`cancelled_at: "2026-01-02T00:00:00+0900"`, "cancel_reason: 不要になった"))
	put(".ccnavi/approved/doing/i0001-01-07.md", testkit.ChildText("i0001-01-07", "i0001", "1"))
	put("wip/proposals/review/i0001-01-07.md", testkit.ChildText("i0001-01-07", "i0001", "1", started,
		`completed_at: "2026-01-02T00:00:00+0900"`))
	put("wip/proposals/todo/i0001.md", parent)
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0002.md"), testkit.ParentText("i0002", nil))
	return root
}

func TestRunMatchesPython(t *testing.T) {
	root := statusWorkspace(t)
	args := [][]string{{}, {"i0001"}, {"i0001-02-01"}, {"i0002"}, {"i0009"}, {"i0001", "i0002"}}
	want := golden.Load[[][3]any](t, "run")
	conf := testkit.Settings(t, root)
	for i, a := range args {
		var out, errOut strings.Builder
		code := Run(&out, &errOut, root, conf, a)
		got := [3]any{float64(code), testkit.Rel(root, out.String()), testkit.Rel(root, errOut.String())}
		if i >= len(want) || got != want[i] {
			t.Errorf("%q:\n got code=%v\n%s\nstderr:%s\nwant code=%v\n%s\nstderr:%s", a, got[0], got[1], got[2], want[i][0], want[i][1], want[i][2])
		}
	}
}

// 承認済みチケットが 1 件も無いときは、無いと言って 0 で終わる。
func TestRunWithNothing(t *testing.T) {
	root := testkit.Workspace(t)
	var out, errOut strings.Builder
	if code := Run(&out, &errOut, root, testkit.Settings(t, root), nil); code != 0 || out.String() != msgNothing || errOut.Len() > 0 {
		t.Errorf("code=%d out=%q err=%q", code, out.String(), errOut.String())
	}
}
