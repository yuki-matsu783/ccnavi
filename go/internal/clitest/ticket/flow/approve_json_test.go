package flow

// `--agree --preview --json` と `--agree --yes <識別子,…> --json`（承認の JSON）の受入テスト。元: tests/ticket/test_approve_json.py。
//
// VS Code のボード拡張がオーバーレイで承認するための経路。見るのは次のとおり。
//
//   - --yes は一覧と同じ識別子と本文のダイジェストを渡せば承認し、ターミナルの壁を通らない。素の --agree は壁で止まる
//   - ダイジェストは親の改版でも承認済みチケットの変化では動かさない（照合するのは提案の一覧と各提案ファイルのバイト列）
//   - 止まるまでに出た行も partial として渡す
//   - 拡張側のフィクスチャ（extensions/vscode/ccnavi-board/test/fixtures/approve-*.json）と同じ形・同じ値
//
// 形を変えたら `go test ./internal/clitest/ticket/flow -run ApproveJSON -update-board-fixtures` でフィクスチャを書き直す。
//
// プレビューの一覧・一覧と違えば承認しない（mismatch）・ターミナルの --agree が確認の入力のあとに同じ照合をする所は、
// 答えを返す前に提案を書き換える必要があるので go の core のテストが見る。
import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// parentAllowWip は計画（research, design）の範囲と重なる allow の親（重ならないと親にも超過が出る）。
func parentAllowWip(name string, plan []samples.Step, allow ...string) string {
	if allow == nil {
		allow = []string{"wip/*"}
	}
	return samples.ParentText(name, plan, samples.ParentOpts{Allow: allow})
}

func (tr *Tree) previewJSON(extra ...string) map[string]any {
	tr.T.Helper()
	return ticketkit.ParseJSON(tr.T, ticketkit.OK(tr.T, tr.Ccnavi(append([]string{"--agree", "--preview", "--json"}, extra...)...)).Stdout)
}

// yesBoard は拡張と同じく、直前に同じ extra でプレビューして見せたダイジェストを渡して承認する。
// digest を名指しすると、その値をそのまま渡す（見せたあとに提案が変わった形）。
func (tr *Tree) yesBoard(tickets []string, digest string, extra ...string) run.Result {
	tr.T.Helper()
	if digest == "" {
		digest = js(tr.previewJSON(extra...), "digest")
	}
	args := append([]string{"--entry", "board", "user", "agree", "--yes", strings.Join(tickets, ","), "--digest", digest, "--json"}, extra...)
	return tr.Ccnavi(args...)
}

func (tr *Tree) copyExists(name string) bool { return ticketkit.Exists(tr.ApprovedPath("doing", name)) }

// pendingParentAndChild は親 1 本と、フェーズ 1 の子 1 枚を提案したまま（未承認）にする。
func (tr *Tree) pendingParentAndChild() {
	tr.Propose("i0001", parentAllowWip("i0001", planOf("research", "design")), "")
	tr.Propose("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), "")
	tr.CommitParent("tickets")
}

func TestYesWithoutJSONPrintsTheHumanLines(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.pendingParentAndChild()
	digest := js(tr.previewJSON(), "digest")
	r := ticketkit.OK(t, tr.Ccnavi("user", "agree", "--yes", "i0001,i0001-01-01", "--digest", digest))
	ticketkit.Has(t, r.Stdout, "承認した")
	if !tr.copyExists("i0001") {
		t.Error("承認済みチケットが置かれない")
	}
}

// 親の改版を見せたあとで、今の承認済みチケットだけが変わっても、照合では止めない。
func TestAChangeOfTheApprovedCopyAloneIsNotCollated(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.FamilyPlan(planOf("research", "design"), nil)
	// 足すのは acceptance（implement は acceptance を要るので、単独で足すと改版は拒まれる）。
	tr.Propose("i0001", parentAllowWip("i0001", planOf("research", "design", "acceptance"), "src/*", "wip/*", "tests/*"), "")
	tr.CommitParent("revise")
	shown := tr.previewJSON()
	if b := jl(shown, "batch"); len(b) != 1 || js(b[0], "ticket") != "i0001" || !jb(b[0], "revision") {
		t.Fatalf("batch = %v", shown["batch"])
	}
	copyPath := tr.ApprovedPath("doing", "i0001")
	ticketkit.Write(t, copyPath, ticketkit.Read(t, copyPath)+"\n")
	if got := js(tr.previewJSON(), "digest"); got != js(shown, "digest") {
		t.Errorf("承認済みチケットだけの変化でダイジェストが動いた")
	}
	wantCode(t, tr.yesBoard([]string{"i0001"}, js(shown, "digest")), 0)
}

// --yes はターミナルの壁を通らない。素の --agree は今までどおり壁で止まる。
func TestYesDoesNotNeedATerminalButPlainApproveStillDoes(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.pendingParentAndChild()
	refused := ticketkit.Fails(t, tr.Call(ticketkit.Call{Stdin: "y\n"}, "--entry", "sh", "user", "agree", "--guard-ticket-approval", "enable"))
	ticketkit.Has(t, refused.Stderr, "ターミナル")
	if tr.copyExists("i0001") {
		t.Error("壁で止まらず承認した")
	}
	wantCode(t, tr.yesBoard([]string{"i0001", "i0001-01-01"}, "", "--guard-ticket-approval", "enable"), 0)
	if !tr.copyExists("i0001") {
		t.Error("--yes で承認できない")
	}
}

func TestPreviewDoesNotNeedATerminal(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.pendingParentAndChild()
	body := tr.previewJSON("--guard-ticket-approval", "enable")
	var got []string
	for _, b := range jl(body, "batch") {
		got = append(got, js(b, "ticket"))
	}
	if strings.Join(got, ",") != "i0001,i0001-01-01" {
		t.Errorf("batch = %v", got)
	}
}

// 拡張のフィクスチャ（approve-preview・approve-yes・approve-mismatch）と同じ形・同じ値。
func TestApproveJSONShapesMatchTheExtensionFixtures(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.pendingParentAndChild()
	// 定義の範囲を超える子は一覧に載り、overflow を持つ。計画に無い番号の子は承認の対象にしない側に載る。
	// 拡張は両方の形を読むので、同じ一覧に並べて書き出す。
	tr.Propose("i0001-01-02", child("i0001-01-02", 1, "wip/design/*"), "")
	tr.Propose("i0001-05-05", child("i0001-05-05", 5, "wip/research/*"), "")
	tr.CommitParent("tickets")
	preview := ticketkit.OK(t, tr.Ccnavi("--agree", "--preview", "--json"))
	checkApproveFixture(t, "approve-preview.json", preview.Stdout, tr.Root)

	// 承認の答えは親と子 1 枚の形で書き出す。超過のある子は提案を下げてから承認する。
	removeFile(t, tr.TodoPath("i0001-01-02"))
	tr.CommitParent("tickets")
	yes := tr.yesBoard([]string{"i0001", "i0001-01-01"}, "")
	wantCode(t, yes, 0)
	checkApproveFixture(t, "approve-yes.json", yes.Stdout, tr.Root)

	// 食い違いの形も拡張が読むので、同じく書き出す。
	tr.Propose("i0001-02-03", child("i0001-02-03", 2, "wip/design/*"), "")
	tr.CommitParent("tickets")
	mismatch := tr.yesBoard([]string{"i0001-01-09"}, "")
	wantCode(t, mismatch, 1)
	checkApproveFixture(t, "approve-mismatch.json", mismatch.Stdout, tr.Root)
}

// 止まるまでに出た行も渡す。ターミナルだけが知っていて拡張が知らない状態を作らない。
// レビュー済みのフェーズに子を 2 枚足し、2 枚目だけ書けなくする。1 枚目は置けるのでそのフェーズのマーカーが消え、その行が lines に入る。
func TestPartialCarriesTheProgressLines(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	ticketkit.OK(t, tr.ConfirmFor(tr.Phase1Requested(), "1"))
	gate, waiting, marks := tr.BoardPhase(1, "i0001")
	if gate || waiting || strings.Join(marks, ",") != "requested,reviewed" {
		t.Fatalf("board phase = %v %v %v", gate, waiting, marks)
	}
	tr.Propose("i0001-01-02", child("i0001-01-02", 1, "wip/design/*"), "")
	tr.Propose("i0001-01-03", child("i0001-01-03", 1, "wip/design/*"), "")
	tr.CommitParent("approval-request 02 03")
	if err := os.MkdirAll(filepath.Join(tr.Approved, "doing", "i0001-01-03.md"), 0o755); err != nil {
		t.Fatal(err)
	}
	r := tr.yesBoard([]string{"i0001-01-02", "i0001-01-03"}, "")
	wantCode(t, r, 1)
	partial := jm(ticketkit.ParseJSON(t, r.Stdout), "partial")
	if placed := strs(jl(partial, "placed")); len(placed) != 1 || placed[0] != "i0001-01-02" || js(partial, "ticket") != "i0001-01-03" {
		t.Errorf("partial = %v", partial)
	}
	found := false
	for _, l := range strs(jl(partial, "lines")) {
		found = found || strings.Contains(l, "マーカー")
	}
	if !found {
		t.Errorf("lines に「マーカー」が無い: %v", partial["lines"])
	}
}
