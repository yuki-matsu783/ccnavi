package flow

// `--agree --preview --verify`（承認を頼む前の確認）と、書いた回にそれを伝える案内。
// 元: tests/ticket/test_approve_verify.py。

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// answerNo は確かめの「いいえ」の終了コード。1 は使い方と設定の誤りで、答えではない。
const answerNo = 3

func (tr *Tree) verify(extra ...string) run.Result {
	return tr.Ccnavi(append([]string{"--agree", "--preview", "--verify"}, extra...)...)
}

// soundProposal は承認できる親と子（調査）。
func soundProposal(tr *Tree) {
	tr.Propose("i0001", parentOf("i0001", "research", "design"), "")
	tr.Propose("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), "")
	tr.CommitParent("tickets")
}

func TestVerifyPassesAPendingBatchAndPlacesNothing(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	soundProposal(tr)
	r := tr.verify()
	wantCode(t, r, 0)
	for _, w := range []string{"i0001", "通る", "承認を依頼してよい", "ユーザに承認を依頼してよい"} {
		ticketkit.Has(t, r.Stdout, w)
	}
	ticketkit.Lacks(t, r.Stdout, "外れる")
	// 確かめただけ。承認済みチケットは置かれていない。
	for _, n := range []string{"i0001", "i0001-01-01"} {
		if ticketkit.Exists(tr.ApprovedPath("doing", n)) {
			t.Errorf("%s の承認済みチケットが置かれた", n)
		}
	}
}

func TestVerifyNarrowedToOneID(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	r := tr.verify("i0001")
	wantCode(t, r, 0)
	ticketkit.Has(t, r.Stdout, "指定の 1 件")
}

func TestVerifyFailsOnARejectedProposalWithItsReason(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research", "design"), "")
	// 計画に無い番号の子。承認の対象にしない側に載る。
	tr.Propose("i0001-05-05", child("i0001-05-05", 5, "wip/research/*"), "")
	tr.CommitParent("tickets")
	r := tr.verify()
	wantCode(t, r, answerNo)
	for _, w := range []string{"i0001-05-05", "外れる", "計画にありません", "直してから", "通る"} {
		ticketkit.Has(t, r.Stdout, w)
	}
}

func TestVerifyParentWithoutAChildPassesButPointsAtTheChild(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research", "design"), "")
	tr.CommitParent("tickets")
	r := tr.verify()
	wantCode(t, r, 0)
	ticketkit.Has(t, r.Stdout, "子の提案がありません")
	ticketkit.Has(t, r.Stdout, "i0001-01-01")
	var nexts []string
	for _, l := range ticketkit.Lines(r.Stdout) {
		if strings.HasPrefix(l, "次: ") {
			nexts = append(nexts, l)
		}
	}
	if len(nexts) != 1 {
		t.Fatalf("次: の行が %d 行: %s", len(nexts), r.Stdout)
	}
	ticketkit.Has(t, nexts[0], "子の提案を")
	ticketkit.Has(t, nexts[0], "承認が済むまで実装はしない")
	ticketkit.Lacks(t, r.Stdout, "承認を依頼してよい")
}

func TestVerifyOnlyTheStepsThatStartAtOnceNeedAChild(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	soundProposal(tr) // design（先行あり）の子は無くてよい
	r := tr.verify()
	wantCode(t, r, 0)
	ticketkit.Lacks(t, r.Stdout, "子の提案がありません")
	ticketkit.Has(t, r.Stdout, "承認を依頼してよい")
}

func TestVerifyCountsAChildOutsideTheNarrowedBatch(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.Propose("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), "")
	tr.CommitParent("tickets")
	r := tr.verify("i0001")
	wantCode(t, r, 0)
	ticketkit.Lacks(t, r.Stdout, "子の提案がありません")
}

func TestVerifyFailsWhenNothingIsPending(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	r := tr.verify()
	wantCode(t, r, answerNo)
	ticketkit.Has(t, r.Stdout, "承認待ちのチケットはありません。")
	ticketkit.Has(t, r.Stdout, "todo/")
}

func TestVerifyFailsOnAnIDThatIsNotPending(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	r := tr.verify("i0002")
	wantCode(t, r, answerNo)
	ticketkit.Has(t, r.Stdout, "承認待ちに無い")
}

func TestVerifyScopeOverflowPassesButIsShown(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research", "design"), "")
	// 定義（調査）の範囲を超える子。承認は止まらず、判定が切り詰める。
	tr.Propose("i0001-01-01", child("i0001-01-01", 1, "wip/design/*"), "")
	tr.CommitParent("tickets")
	r := tr.verify()
	wantCode(t, r, 0)
	ticketkit.Has(t, r.Stdout, "承認しても書けない")
	ticketkit.Has(t, r.Stdout, "承認を依頼してよい")
}

func TestVerifyJSONIsThePreviewBodyWithTheAnswer(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	r := tr.verify("--json")
	wantCode(t, r, 0)
	body := ticketkit.ParseJSON(t, r.Stdout)
	if jf(body, "version") != 1 {
		t.Errorf("version = %v", body["version"])
	}
	if b := jl(body, "batch"); len(b) != 1 || js(b[0], "ticket") != "i0001" {
		t.Errorf("batch = %v", body["batch"])
	}
	if js(body, "digest") == "" {
		t.Error("digest が空")
	}
	if !jb(body, "verify", "ok") || js(body, "verify", "reason") != "ok" {
		t.Errorf("verify = %v", body["verify"])
	}
}

func TestVerifyJSONSaysWhyItFailed(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	// 読めない提案しか無ければ、承認待ちが 1 件も無いのと同じ（そこで失敗する）。
	ticketkit.Write(t, tr.TodoPath("broken"), "---\n: :\n")
	tr.CommitParent("tickets")
	r := tr.verify("--json")
	wantCode(t, r, answerNo)
	body := ticketkit.ParseJSON(t, r.Stdout)
	if jb(body, "verify", "ok") || js(body, "verify", "reason") != "nothing-pending" {
		t.Errorf("verify = %v", body["verify"])
	}
	found := false
	for _, p := range jl(body, "problems") {
		found = found || strings.Contains(p.(string), "broken.md")
	}
	if !found {
		t.Errorf("problems に broken.md が無い: %v", body["problems"])
	}
}

// 親のワークツリーの状態（approval-request が止まる条件と同じ）が確かめを落とす。
func TestVerifyFailsOnTheStateOfTheParentWorktree(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, reason, path string
		setup              func(tr *Tree)
	}{
		{"uncommitted", "dirty_parent", "src/keep.py", func(tr *Tree) {
			ticketkit.Write(t, filepath.Join(tr.ParentTree, "src", "keep.py"), "print(2)\n")
		}},
		{"unpushed", "unpushed_code", "src/new.py", func(tr *Tree) {
			withOrigin(tr)
			ticketkit.Write(t, filepath.Join(tr.ParentTree, "src", "new.py"), "x = 1\n")
			tr.CommitParent("code")
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := newTree(t)
			soundProposal(tr)
			c.setup(tr)
			r := tr.verify("--json")
			wantCode(t, r, answerNo)
			body := ticketkit.ParseJSON(t, r.Stdout)
			if jb(body, "verify", "ok") || js(body, "verify", "reason") != c.reason {
				t.Errorf("verify = %v", body["verify"])
			}
			text := tr.verify()
			wantCode(t, text, answerNo)
			ticketkit.Has(t, text.Stdout, c.path)
			ticketkit.Lacks(t, text.Stdout, "承認を依頼してよい")
		})
	}
}

func withOrigin(tr *Tree) {
	bare := filepath.Join(tr.Root, "origin.git")
	tr.Git(tr.Root, "init", "--quiet", "--bare", bare)
	tr.Git(tr.Root, "remote", "add", "origin", bare)
	tr.Parent("push", "--quiet", "origin", "i0001")
}

func TestVerifyAPushedCleanParentStillPasses(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	soundProposal(tr)
	withOrigin(tr)
	r := tr.verify("--json")
	wantCode(t, r, 0)
	if !jb(ticketkit.ParseJSON(t, r.Stdout), "verify", "ok") {
		t.Errorf("stdout = %s", r.Stdout)
	}
}

// 読めない提案は終了コードを動かさない。`--agree` もそこでは失敗しないので、確かめと承認の答えを分けない。
func TestVerifyABrokenProposalElsewhereDoesNotFailASoundOne(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.Propose("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), "")
	ticketkit.Write(t, tr.TodoPath("broken"), "---\n: :\n")
	tr.CommitParent("tickets")
	r := tr.verify()
	wantCode(t, r, 0)
	for _, w := range []string{"読めなかったファイル", "broken.md", "承認を依頼してよい"} {
		ticketkit.Has(t, r.Stdout, w)
	}
	wantCode(t, tr.Agree("y"), 0)
	if !ticketkit.Exists(tr.ApprovedPath("doing", "i0001")) {
		t.Error("承認で承認済みチケットが置かれない")
	}
}

// `--verify` は `--preview` につけて使う。使い方の誤りは 1（答えの「いいえ」3 と分ける）。
func TestVerifyNeedsPreview(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	r := tr.Ccnavi("user", "agree", "--verify")
	wantCode(t, r, 1)
	ticketkit.Has(t, r.Stderr, "--preview")
}

// 承認で外れるものを数える経路は 1 本。--lint も同じことを言う。
func TestLintSaysWhatTheVerifySays(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", parentOf("i0001", "research", "design"), "")
	tr.Propose("i0001-05-05", child("i0001-05-05", 5, "wip/research/*"), "")
	tr.CommitParent("tickets")
	v := tr.verify()
	wantCode(t, v, answerNo)
	ticketkit.Has(t, v.Stdout, "計画にありません")
	lint := tr.Ccnavi("--lint")
	if lint.Code == 0 {
		t.Errorf("--lint が 0: %s%s", lint.Stdout, lint.Stderr)
	}
	ticketkit.Has(t, ticketkit.Out(lint), "i0001-05-05: ")
	ticketkit.Has(t, ticketkit.Out(lint), "計画にありません")
}

// 順序で外れる子は --lint では warn（前が閉じれば通るので、書いた側に直すものが無い）。
func TestLintSaysTheOrderProblemAsAWarn(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	soundProposal(tr)
	tr.Approve()
	// フェーズ 1 が閉じていないのに、フェーズ 2 の子を先回りして書く。
	tr.Propose("i0001-02-02", child("i0001-02-02", 2, "wip/design/*"), "")
	tr.CommitParent("tickets")
	v := tr.verify()
	wantCode(t, v, answerNo)
	ticketkit.Has(t, v.Stdout, "が閉じるまで承認しない")
	lint := tr.Ccnavi("--lint")
	said := ticketkit.Out(lint)
	ticketkit.Has(t, said, "i0001-02-02: ")
	ticketkit.Has(t, said, "が閉じるまで承認しない")
	ticketkit.Has(t, said, "warn: (ticket): i0001-02-02: ")
	ticketkit.Lacks(t, said, "error: (ticket): i0001-02-02: ")
	wantCode(t, lint, 0)
}

const verifyNotice = "approval-request <親の識別子> --check"

func TestWritingAProposalTellsTheAgentToCheckFirst(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	target := tr.TodoPath("i0002")
	first := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": target})
	ticketkit.Has(t, contextOf(t, first), verifyNotice)
	ticketkit.Has(t, contextOf(t, first), "承認を依頼する前に")
	// 案内は 1 つの文脈で 1 度だけ。
	again := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": tr.TodoPath("i0003")})
	ticketkit.Lacks(t, contextOf(t, again), verifyNotice)
}

// 案内は判定の表に足さない。todo/ とどのルールも言及しない場所が、どの権限モードでも同じ判定になる。
func TestTheNoticeChangesNoVerdict(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	todo := tr.TodoPath("i0002")
	other := filepath.Join(tr.ParentTree, "src", "keep.py")
	for _, c := range []struct{ mode, verdict string }{{"bypassPermissions", "deny"}, {"dontAsk", "deny"}, {"unknownMode", "ask"}} {
		inside := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": todo}, ticketkit.HookOpts{PermissionMode: c.mode})
		beyond := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": other}, ticketkit.HookOpts{PermissionMode: c.mode})
		if got := decision(t, inside); got != c.verdict {
			t.Errorf("%s: todo/ の判定 = %q, want %q", c.mode, got, c.verdict)
		}
		if got := decision(t, beyond); got != c.verdict {
			t.Errorf("%s: 範囲外の判定 = %q, want %q", c.mode, got, c.verdict)
		}
		// 止めた回にも文は届く（判定とは別の欄）。1 つの文脈で 1 度だけなので最初の 1 回を見る。
		if c.mode == "bypassPermissions" {
			ticketkit.Has(t, contextOf(t, inside), verifyNotice)
		}
	}
}

func TestTheNoticeStaysInsideTheWorkspaceAndPlaces(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	// ワークスペースの外に同じ構成を掘っても、提案を書いたことにはしない。
	elsewhere := filepath.Join(base.TempDir(t), "wip", "proposals", "todo", "evil.sh")
	r := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": elsewhere})
	ticketkit.Lacks(t, contextOf(t, r), verifyNotice)
	// ほかの場所にも届かない。
	other := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": filepath.Join(tr.ParentTree, "src", "keep.py")})
	ticketkit.Lacks(t, contextOf(t, other), verifyNotice)
}
