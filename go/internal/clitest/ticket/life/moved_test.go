package life

import (
	"encoding/json"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 依頼のあとに HEAD が動いて止めたときの説明（手元）。判定は変えない。
//   - 何が変わったかは断定しない固定の文: 依頼時のコミットといまの HEAD が違う。何が変わったかユーザに確かめてもらい、依頼し直す。
//     コミットの一覧は出さない（依頼時のコミットが祖先でなくても同じ文）
//   - 依頼のあとに変えて戻した（revert で差分が空）なら、判定は今どおり通る

const (
	resultText = `{"host": "fixture", "mr": {"number": 7, "url": "u/7"}, "threads": [], "reviews": []}`
	movedFixed = "依頼時のコミットといまの HEAD が違う。何が変わったかユーザに確かめてもらい、依頼し直す。"
)

// requestedWithResult はフェーズ 1 を依頼まで進め、取得した結果を置いた fixture を返す。
func requestedWithResult(t *testing.T) (*ticketkit.Tree, ticketkit.Fixture) {
	t.Helper()
	tr, f := requestedTree(t)
	ticketkit.Write(t, f.Path, resultText)
	return tr, f
}

func commitAs(tr *ticketkit.Tree, author, subject string, files map[string]string) {
	tr.T.Helper()
	for rel, text := range files {
		writeIn(tr.T, tr.ParentTree, rel, text)
	}
	tr.Parent("add", "-A")
	tr.Parent("-c", "user.name="+author, "-c", "user.email=a@example.invalid", "commit", "--quiet", "-m", subject)
}

func pushParent(tr *ticketkit.Tree) { tr.Parent("push", "--quiet", "origin", "i0001") }

func confirmRefused(tr *ticketkit.Tree, f ticketkit.Fixture) string {
	tr.T.Helper()
	r := tr.Confirm(f)
	if r.Code != 1 {
		tr.T.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	return r.Stderr
}

func TestTheMovedHeadGetsTheFixedExplanationWithoutACommitList(t *testing.T) {
	t.Parallel()
	tr, f := requestedWithResult(t)
	commitAs(tr, "alice", "最初の修正", map[string]string{"src/a.py": "1\n"})
	pushParent(tr)
	said := confirmRefused(tr, f)
	ticketkit.Has(t, said, "依頼の後に親の HEAD が動いている")
	ticketkit.Has(t, said, movedFixed)
	ticketkit.Lacks(t, said, "最初の修正")
	ticketkit.Lacks(t, said, "alice")
	ticketkit.Has(t, said, "依頼を出し直してください")
}

// 依頼時のコミットが祖先でない（履歴を書き換えた形）でも、同じ固定の文。
func TestARecordedCommitThatIsNotAnAncestorGetsTheSameExplanation(t *testing.T) {
	t.Parallel()
	tr, f := requestedWithResult(t)
	commitAs(tr, "alice", "修正", map[string]string{"src/a.py": "1\n"})
	pushParent(tr)
	orphan := strings.TrimSpace(tr.Parent("commit-tree", "-m", "別の履歴", "4b825dc642cb6eb9a060e54bf8d69288fbee4904"))
	mark := tr.RequestedMark()
	data := ticketkit.ReadJSON(t, mark)
	data["head"] = orphan
	raw, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	ticketkit.Write(t, mark, string(raw))
	said := confirmRefused(tr, f)
	ticketkit.Has(t, said, "依頼の後に親の HEAD が動いている")
	ticketkit.Has(t, said, movedFixed)
	ticketkit.Has(t, said, "依頼を出し直してください")
}

func TestAChangeThatWasRevertedLeavesNoDiffAndTheJudgementPasses(t *testing.T) {
	t.Parallel()
	tr, f := requestedWithResult(t)
	commitAs(tr, "alice", "足す", map[string]string{"src/tmp.py": "x\n"})
	tr.Parent("revert", "--no-edit", "HEAD")
	pushParent(tr)
	done := ticketkit.OK(t, tr.Confirm(f))
	ticketkit.Lacks(t, done.Stderr, "依頼の後に親の HEAD が動いている")
}

func TestDecideSaysTheSameWhenTheHeadMoved(t *testing.T) {
	t.Parallel()
	tr, f := requestedWithResult(t)
	commitAs(tr, "alice", "修正", map[string]string{"src/a.py": "1\n"})
	pushParent(tr)
	done := ticketkit.Fails(t, tr.Ccnavi("--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--preview", "--json", "--result", f.Path))
	ticketkit.Has(t, done.Stderr, movedFixed)
}

// ---- レビュー準備中・レビュー待ちの間の、新しい依頼についての案内。案内だけで、セッション単位で機械的に止める仕組みではない。止める
// （サブエージェントの起動とシェル実行）のは今までどおり、その親の cwd からだけ。案内は、止めたときの文と、request が成功したときの出力に添える。

var requestNoteParts = []string{
	"自分が準備・依頼したレビューの待ち中は、別の親・別の目的の新しい依頼を受けない。",
	"受けるときは、ワークスペースルートで起動した別のセッションで受けるようユーザに伝える。",
	"他の会話が待っている親は対象外。",
	"同じ親のレビュー対応・相談・止める指示・取り消し・次のフェーズの計画・動いている子の完了処理は、このセッションで続けてよい。",
}

func hasAll(t *testing.T, text string, parts []string) {
	t.Helper()
	for _, p := range parts {
		ticketkit.Has(t, text, p)
	}
}

func TestTheHoldTextHasTheNoteBeforeAndAfterTheRequest(t *testing.T) {
	t.Parallel()
	tr := closeFirstChild(t, samples.Plan("design"), designScope, "wip/design/plan.md")
	// レビュー準備中（依頼の前）。
	preparing := tr.Spawn(tr.ParentTree, "次")
	ticketkit.Has(t, preparing, "DENY_PHASE_REVIEW")
	hasAll(t, preparing, requestNoteParts)
	hasAll(t, tr.Bash(tr.ParentTree, "make"), requestNoteParts)
	// レビュー待ち（依頼のあと）。
	f := remoteDraft(tr)
	asked := ticketkit.OK(t, tr.Request(f))
	hasAll(t, tr.Spawn(tr.ParentTree, "次"), requestNoteParts)
	// request が成功したときの出力にも添える。
	hasAll(t, asked.Stdout, requestNoteParts)
}

// 案内だけ。親の cwd の外（ワークスペースルート）からの起動は止めない。
func TestTheNoteDoesNotStopAnythingByItself(t *testing.T) {
	t.Parallel()
	tr := closeFirstChild(t, samples.Plan("design"), designScope, "wip/design/plan.md")
	ticketkit.Lacks(t, tr.Spawn(tr.Root, "別の親の依頼"), "DENY_PHASE_REVIEW")
}
