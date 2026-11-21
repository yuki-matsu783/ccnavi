package flow

// 変更要求の案内は、ホストごとに出し分ける。判定は変えない。
// 同じ結果（変更要求のレビューが 1 件）でホストだけ変えて、confirm・decide・close-early・手で動かした子の確定の
// 拒否の文面が GitLab と GitHub で分かれることを見る。ホストは取得した結果の host。
// 元: tests/ticket/test_changes_requested_hosts.py。

import (
	"ccnavi/internal/harness/ticketkit"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

const (
	gitlabNote       = "変更を依頼（Request changes）が出ています。解除はレビュアーの承認（Approve）で行います。"
	gitlabUnverified = "作成者によるレビューの再依頼（Re-request review）で状態が戻るかは未検証です"
	githubNote       = "変更要求（Changes requested）が出ています。解除はレビュアーの Approve か、レビューの dismiss で行います"
)

var hosts = []string{"gitlab", "github"}

// requestedOn はフェーズ 1 のレビューを依頼したところまで組み、依頼の記録のホストを host に合わせる
// （結果のホストと照合されるため）。
func requestedOn(t *testing.T, host string) *Tree {
	t.Helper()
	return cachedTree(t, "changes-requested-"+host, func(tr *Tree) {
		tr.Phase1Requested()
		mark := tr.RequestedMark()
		body := ticketkit.ReadJSON(tr.T, mark)
		body["host"] = host
		raw, _ := json.Marshal(body)
		ticketkit.Write(tr.T, mark, string(raw))
		tr.CommitParent("request")
		tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	})
}

func changesRequestedResult(t *testing.T, tr *Tree, host string) string {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{
		"host": host, "mr": map[string]any{"number": 7, "url": "u/7", "draft": true}, "threads": []any{},
		"reviews": []any{map[string]any{"state": "CHANGES_REQUESTED", "url": "u/7#review", "submitted_at": "2026-01-01T00:00:00Z", "author": "rev"}},
	})
	return ticketkit.Write(t, filepath.Join(tr.Root, "result-"+host+".json"), string(raw))
}

func saidByHost(t *testing.T, host, text string) {
	t.Helper()
	want, other := gitlabNote, githubNote
	if host == "github" {
		want, other = githubNote, gitlabNote
	}
	ticketkit.Has(t, text, want)
	ticketkit.Lacks(t, text, other)
	if host == "gitlab" {
		ticketkit.Has(t, text, gitlabUnverified)
	} else {
		ticketkit.Lacks(t, text, "Re-request")
	}
}

func TestConfirmDecideAndCloseEarlySayItByHost(t *testing.T) {
	for _, host := range hosts {
		t.Run(host, func(t *testing.T) {
			t.Parallel()
			tr := requestedOn(t, host)
			result := changesRequestedResult(t, tr, host)
			f := ticketkit.Fixture{T: t, Path: result}
			confirm := ticketkit.Fails(t, tr.ConfirmFor(f, "1"))
			saidByHost(t, host, confirm.Stderr)
			decide := ticketkit.Fails(t, tr.Ccnavi("--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--preview", "--json", "--result", result))
			saidByHost(t, host, decide.Stderr)
			closed := ticketkit.Fails(t, tr.Call(ticketkit.Call{Stdin: "y\n"}, "--cwd", tr.ParentTree, "user", "close-early", "--reason", "ここまでで十分", "--result", result))
			saidByHost(t, host, closed.Stderr)
			// 判定は変えない: どのホストでもレビュー済みにならない。
			if ticketkit.Exists(tr.PhaseMark("1.reviewed")) {
				t.Error("変更要求があるのにレビュー済みになった")
			}
		})
	}
}

func TestAManuallyMovedChildIsHeldWithTheHostNote(t *testing.T) {
	for _, host := range hosts {
		t.Run(host, func(t *testing.T) {
			t.Parallel()
			tr := requestedOn(t, host)
			waiting := filepath.Join(tr.ParentTree, "wip", "proposals", "review", "i0001-01-01.md")
			done := tr.ApprovedPath("done", "i0001-01-01")
			if err := os.MkdirAll(filepath.Dir(done), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.Rename(waiting, done); err != nil {
				t.Fatal(err)
			}
			tr.CommitParent("move by hand")
			tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
			held := ticketkit.Fails(t, tr.ConfirmFor(ticketkit.Fixture{T: t, Path: changesRequestedResult(t, tr, host)}, "1"))
			saidByHost(t, host, held.Stderr)
			ticketkit.Has(t, held.Stderr, "保留")
		})
	}
}
