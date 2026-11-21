package flow

// Chrome の承認の入口（go/cmd/ccnavi-chrome）の受入テスト。元: tests/ticket/test_chrome_write.py。
//
//  1. 親のワークツリー相当: ホストに無い親子のチケットは仮のワークツリーが組まれず、その親子のチケットは
//     「親のワークツリーがありません」で決まらない。判定の入力に読めない（バイナリの）ファイルがあれば止める
//  2. 版ずれ: 統合先の互換のマーカーが違えば、書く操作（見せたものつきの plan）を受けない
//  3. 見せた一覧とダイジェスト: 違えば書くものを出さない
//  4. 手元の ccnavi が、Chrome の書いた承認済みチケットを同じに読む（判定し直しで error が出ない）
//
// 書く先の名前の断り・ホストから来たパスの断り・パスの書き換えは go の entry/chrome のテストが見る。
import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/ticketkit"
)

var chromeActor = map[string]any{"account": "alice", "version": "9.9.9"}

// chromeTree は互換のマーカーを置いた木（親 i0001 のワークツリーまで）。
func chromeTree(t *testing.T) *Tree {
	t.Helper()
	tr := newTree(t)
	tr.putCompat(exe.Compat(t))
	return tr
}

func (tr *Tree) previewChrome(family string) map[string]any {
	tr.T.Helper()
	return tr.askChrome(tr.chromeRequest("plan", family, nil, nil))
}

// planChrome は見せた一覧とダイジェストをつけて plan を頼む。
func (tr *Tree) planChrome(family string, extra map[string]any) map[string]any {
	tr.T.Helper()
	first := tr.previewChrome(family)
	req := map[string]any{"only": first["identifiers"], "shown": map[string]any{"ids": first["identifiers"], "digest": first["digest"]}, "actor": chromeActor}
	for k, v := range extra {
		req[k] = v
	}
	return tr.answer(tr.chromeRequest("plan", family, nil, req))
}

func TestAnAbsentFamilyStopsAndItsStaleCopyIsUndecided(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	tr.FamilyPlan(planOf("research"), nil)
	// P の上に古いチケット（閉じた i0009-01-01）があっても、i0009 がホストに無ければ決まらない。
	text := strings.Replace(childNoReview("i0009-01-01", 1, "wip/research/*"), "parent: i0001", "parent: i0009", 1)
	text = strings.Replace(text, `completed_at: ""`, `completed_at: "2026-01-01T00:00:00+0000"`, 1)
	ticketkit.Write(t, tr.ApprovedPath("done", "i0009-01-01"), text)
	kid := strings.Replace(childNoReview("i0001-01-01", 1, "wip/research/*"), "human_review:", "predecessors: [\"i0009-01-01\"]\nhuman_review:", 1)
	tr.Propose("i0001-01-01", kid, "")
	tr.CommitParent("tickets")
	request := tr.chromeRequest("plan", "i0001", nil, nil)
	jm(request, "snapshot")["absent"] = []any{"i0009"}
	body := tr.askChrome(request)
	if len(jl(body, "identifiers")) != 0 {
		t.Errorf("identifiers = %v", body["identifiers"])
	}
	found := false
	for _, r := range jl(body, "rejected") {
		if js(r, "ticket") == "i0001-01-01" {
			for _, p := range strs(jl(r, "problems")) {
				found = found || (strings.Contains(p, "i0009") && strings.Contains(p, "親のワークツリー（.claude/worktrees/i0009）がありません"))
			}
		}
	}
	if !found {
		t.Errorf("rejected = %v", body["rejected"])
	}
}

// 要求した親子のブランチ自体が snapshot の branches に無ければ、偽のツリーを組まずに断る（ホストに無いと分かっていても同じ）。
func TestARequestedFamilyMissingFromTheSnapshotIsRefused(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	for _, absent := range [][]any{{}, {"i0003"}} {
		request := tr.chromeRequest("plan", "i0003", nil, nil)
		jm(request, "snapshot")["absent"] = absent
		for _, op := range []string{"board", "plan"} {
			request["op"] = op
			body := tr.answer(request)
			ticketkit.Has(t, js(body, "error"), "family のブランチが snapshot にありません")
			if _, ok := body["identifiers"]; ok {
				t.Errorf("%s %v: identifiers がある", op, absent)
			}
		}
	}
}

func TestAnUnreadableInputStopsTheBoardAndThePlan(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	request := tr.chromeRequest("plan", "i0001", nil, nil)
	jm(request, "snapshot", "branches", "main")["binary"] = []any{".ccnavi/approved/done/x.md"}
	ticketkit.Has(t, js(tr.answer(request), "error"), "判定できません")
	request["op"] = "board"
	board := tr.answer(request)
	ticketkit.Has(t, js(board, "undecided"), "バイナリ")
	if _, ok := board["batch"]; ok {
		t.Error("batch がある")
	}
}

func TestADifferentCompatRefusesWritingButStillShows(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	first := tr.previewChrome("i0001")
	tr.putCompat(compatBump(t))
	board := tr.answer(tr.chromeRequest("board", "i0001", nil, nil))
	if jb(board, "write", "allowed") {
		t.Error("互換が違うのに書ける")
	}
	ticketkit.Has(t, js(board, "write", "reason"), "拡張を更新してください。")
	if b := jl(board, "batch"); len(b) != 1 || js(b[0], "ticket") != "i0001" {
		t.Errorf("batch = %v", board["batch"])
	}
	refused := tr.answer(tr.chromeRequest("plan", "i0001", nil, map[string]any{"shown": map[string]any{"ids": first["identifiers"], "digest": first["digest"]}}))
	ticketkit.Has(t, js(refused, "error"), "互換")
}

func TestADifferentDigestWritesNothing(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	first := tr.previewChrome("i0001")
	plan := func(ids any, digest any) map[string]any {
		return tr.answer(tr.chromeRequest("plan", "i0001", nil, map[string]any{"shown": map[string]any{"ids": ids, "digest": digest}}))
	}
	expect := func(what string, body map[string]any, code string) {
		t.Helper()
		if body["mismatch"] == nil && code == "CORE_APPROVE_DIGEST_CHANGED" {
			t.Errorf("%s: mismatch が無い", what)
		}
		if !isNull(body, "changes") || js(body, "code") != code {
			t.Errorf("%s: changes=%v code=%v", what, body["changes"], body["code"])
		}
	}
	expect("違うダイジェスト", plan(first["identifiers"], strings.Repeat("0", 64)), "CORE_APPROVE_DIGEST_CHANGED")
	// 見せた識別子が今の一覧と違えば、中身より先にそちらの種別。
	expect("違う一覧", plan([]any{"i0001", "i0002"}, first["digest"]), "CORE_APPROVE_LIST_CHANGED")
	// 見せた後に提案が変われば、同じダイジェストでも書かない。
	tr.Propose("i0001", parentOf("i0001", "research", "design"), "")
	tr.CommitParent("tickets")
	expect("変わった提案", plan(first["identifiers"], first["digest"]), "CORE_APPROVE_DIGEST_CHANGED")
}

func TestNothingToApproveCarriesItsReasonCode(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	body := tr.answer(tr.chromeRequest("plan", "i0001", nil, nil))
	if !isNull(body, "changes") || !isNull(body, "mismatch") || js(body, "code") != "CORE_APPROVE_NOTHING" {
		t.Errorf("body = %v", body)
	}
}

// 手元の判定し直し（C3）で error が出ない。
func TestTheLocalCcnaviReadsWhatChromeWroteTheSame(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.Propose("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), "")
	tr.CommitParent("tickets")
	body := tr.planChrome("i0001", nil)
	if ids := strs(jl(body, "identifiers")); !slices.Equal(ids, []string{"i0001", "i0001-01-01"}) {
		t.Fatalf("identifiers = %v (%v)", ids, body)
	}
	tr.applyChanges(jl(body, "changes", "i0001"), tr.ParentTree)
	preview := ticketkit.ParseJSON(t, tr.Ccnavi("--agree", "--preview", "--json").Stdout)
	if len(jl(preview, "batch")) != 0 {
		t.Errorf("batch = %v", preview["batch"])
	}
	lint := ticketkit.ParseJSON(t, tr.Ccnavi("--lint", "--json").Stdout)
	for _, p := range jl(lint, "problems") {
		if js(p, "severity") == "error" {
			t.Errorf("error: %v", p)
		}
	}
	entries, err := os.ReadDir(filepath.Dir(tr.ApprovedPath("doing", "x")))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	slices.Sort(names)
	if !slices.Equal(names, []string{"i0001-01-01.md", "i0001.md"}) {
		t.Errorf("doing/ = %v", names)
	}
}

func TestEveryWritingOpChecksCompatAndTheBranch(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	tr.Propose("i0001", parentOf("i0001", "research"), "")
	tr.CommitParent("tickets")
	tr.putCompat(compatBump(t))
	for op, extra := range map[string]map[string]any{
		"plan":    nil,
		"confirm": {"phase": 1, "result": map[string]any{"host": "h", "mr": map[string]any{"number": 1, "url": "u"}}},
	} {
		ticketkit.Has(t, js(tr.answer(tr.chromeRequest(op, "i0001", nil, extra)), "error"), "互換")
	}
}

// Chrome の承認はワークスペースのリポジトリだけ。プロジェクトの提案は承認しない（プロジェクトのリポジトリが
// 拡張に登録されていなければ、プロジェクトが絡む提案は承認しない）。
func TestAProposalNamingAProjectIsNotApprovedFromTheWorkspace(t *testing.T) {
	t.Parallel()
	tr := chromeTree(t)
	text := strings.Replace(parentOf("i0001", "research"), "ticket: i0001\n", "ticket: i0001\nproject: web\n", 1)
	tr.Propose("i0001", text, "")
	tr.CommitParent("tickets")
	body := tr.previewChrome("i0001")
	rejected := jl(body, "rejected")
	if len(jl(body, "identifiers")) != 0 || len(rejected) != 1 || js(rejected[0], "ticket") != "i0001" {
		t.Errorf("body = %v", body)
	}
}
