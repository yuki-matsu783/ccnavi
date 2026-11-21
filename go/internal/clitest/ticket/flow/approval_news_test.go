package flow

// 承認したことの伝わり方。拡張が渡す文（`--agree --yes` の prompt）と status で伝わり、hook は承認を伝えない。
// 元: tests/ticket/test_approval_news.py。

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// event は hook の入口に payload を渡す（cwd は親のワークツリー）。
func (tr *Tree) event(event, session, agentID, tool string, input map[string]any) run.Result {
	tr.T.Helper()
	payload := map[string]any{"hook_event_name": event, "cwd": tr.ParentTree, "session_id": session}
	if tool != "" {
		payload["tool_name"] = tool
		payload["tool_input"] = input
	}
	if agentID != "" {
		payload["agent_id"] = agentID
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		tr.T.Fatal(err)
	}
	return ticketkit.OK(tr.T, tr.Call(ticketkit.Call{Stdin: string(raw)}, "--mode", "enable"))
}

// approveBody は見せたダイジェストを渡して `--agree --yes` で承認する（渡さない --yes は承認しない）。
func (tr *Tree) approveBody(tickets ...string) map[string]any {
	tr.T.Helper()
	shown := ticketkit.ParseJSON(tr.T, tr.Ccnavi("--agree", "--preview", "--json").Stdout)
	r := ticketkit.OK(tr.T, tr.Ccnavi("user", "agree", "--yes", strings.Join(tickets, ","), "--digest", js(shown, "digest"), "--json"))
	return ticketkit.ParseJSON(tr.T, r.Stdout)
}

func (tr *Tree) approveYes(tickets ...string) string { return js(tr.approveBody(tickets...), "prompt") }

func (tr *Tree) leaveChangeInParent() {
	tr.T.Helper()
	path := filepath.Join(tr.ParentTree, "src", "keep.py")
	ticketkit.Write(tr.T, path, ticketkit.Read(tr.T, path)+"print(2)\n")
}

func (tr *Tree) parentOnly() {
	tr.Propose("i0001", parentOf("i0001", "research", "design"), "")
	tr.CommitParent("tickets")
}

func (tr *Tree) nextChild(name string) {
	tr.Propose(name, childNoReview(name, 1, "wip/research/*"), "")
	tr.CommitParent("tickets")
}

func TestApprovalPromptNamesTheTicketsAndTheOrderOfStart(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.parentOnly()
	tr.nextChild("i0001-01-01")
	prompt := tr.approveYes("i0001", "i0001-01-01")
	ticketkit.Has(t, prompt, "i0001-01-01")
	ticketkit.Has(t, prompt, "start <親>") // 子より先に親を着手する順
}

// 子だけの回では親の start を勧めない（勧めたコマンドは「着手済み」で終わるので、案内が誤りの元になる）。
func TestApprovalPromptOfAChildOnlyBatchDoesNotAskForTheParentStart(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.parentOnly()
	tr.nextChild("i0001-01-01")
	tr.approveYes("i0001", "i0001-01-01")
	tr.nextChild("i0001-01-02")
	told := tr.approveYes("i0001-01-02")
	ticketkit.Lacks(t, told, "start <親>")
	ticketkit.Has(t, told, "親が未着手だと止まります）。")
}

func TestApprovalPromptTellsARevision(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.parentOnly()
	tr.approveYes("i0001")
	tr.Propose("i0001", parentOf("i0001", "research", "design", "acceptance"), "")
	tr.CommitParent("tickets")
	ticketkit.Has(t, tr.approveYes("i0001"), "改版")
}

// 承認の前に親のワークツリーへ入れた変更は、承認の時点で知らせる（承認は止めない）。戻し方まで案内に載せる。
func TestApprovalPromptTellsAboutAChangeLeftInTheParentWorktree(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.parentOnly()
	tr.leaveChangeInParent()
	body := tr.approveBody("i0001")
	if got := strs(jl(body, "approved")); len(got) != 1 || got[0] != "i0001" {
		t.Errorf("approved = %v", got)
	}
	var notices []string
	for _, l := range ticketkit.Lines(js(body, "prompt")) {
		if strings.HasPrefix(l, "案内:") {
			notices = append(notices, l)
		}
	}
	if len(notices) != 1 {
		t.Fatalf("案内: の行が %d 行: %s", len(notices), js(body, "prompt"))
	}
	for _, w := range []string{"src/keep.py", "ccnavi-git.sh", "restore --staged --worktree -- src/keep.py", "子のワークツリー", "ユーザへ確かめる"} {
		ticketkit.Has(t, notices[0], w)
	}
	checkDirtyParent(t, body)
}

func checkDirtyParent(t *testing.T, body map[string]any) {
	t.Helper()
	d := jl(body, "dirty_parent")
	if len(d) != 1 || js(d[0], "ticket") != "i0001" || len(jl(d[0], "paths")) != 1 || jl(d[0], "paths")[0] != "src/keep.py" {
		t.Errorf("dirty_parent = %v", body["dirty_parent"])
	}
}

func TestApprovalPromptIsSilentWhenTheParentWorktreeIsClean(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.parentOnly()
	body := tr.approveBody("i0001")
	ticketkit.Lacks(t, js(body, "prompt"), "案内:")
	if len(jl(body, "dirty_parent")) != 0 {
		t.Errorf("dirty_parent = %v", body["dirty_parent"])
	}
}

// 子の承認でも、変更が残っているのは親のワークツリー。親の識別子で名指しする。
func TestAChildApprovalNamesTheParentWorktree(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.parentOnly()
	tr.approveYes("i0001")
	tr.CommitParent("approve parent")
	tr.nextChild("i0001-01-01")
	tr.leaveChangeInParent()
	checkDirtyParent(t, tr.approveBody("i0001-01-01"))
}

// ターミナルの承認は、標準エラーと --placed-out の JSON の両方に出す。承認は通る。
func TestTerminalAgreeReportsAChangeLeftInTheParentWorktree(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.parentOnly()
	tr.leaveChangeInParent()
	report := filepath.Join(tr.Root, "placed.json")
	r := tr.Call(ticketkit.Call{Stdin: "y\n"}, "user", "agree", "--placed-out", report)
	wantCode(t, r, 0)
	ticketkit.Has(t, r.Stderr, "src/keep.py")
	ticketkit.Has(t, r.Stderr, "restore --staged --worktree -- src/keep.py")
	body := ticketkit.ParseJSON(t, ticketkit.Read(t, report))
	checkDirtyParent(t, body)
	if len(jl(body, "placed")) != 1 {
		t.Errorf("placed = %v", body["placed"])
	}
}

// 承認の後、親のワークツリーの変更を Edit で戻そうとして断られたとき、戻し方を案内する。
func TestTheParentDenialTellsHowToPutAChangeBack(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.parentOnly()
	tr.leaveChangeInParent()
	tr.approveYes("i0001")
	r := tr.event("PreToolUse", "s1", "", "Edit", map[string]any{
		"file_path": filepath.Join(tr.ParentTree, "src", "keep.py"), "old_string": "print(2)", "new_string": "",
	})
	reason := ticketkit.Reason(t, r)
	if decision(t, r) != "deny" {
		t.Errorf("decision = %q", decision(t, r))
	}
	for _, w := range []string{"DENY_TICKET_PARENT", "ccnavi-git.sh", "restore --staged --worktree -- <パス>", "迂回して書き直しません"} {
		ticketkit.Has(t, reason, w)
	}
}

// hook は承認を伝えない（セッション開始・プロンプト・ツール前のどれでも）。
func TestHooksDoNotTellAboutApprovals(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	prompt := func(session string) string {
		return contextOf(t, tr.event("UserPromptSubmit", session, "", "", nil))
	}
	before := func(session, agent string) string {
		target := filepath.Join(tr.ParentTree, "src", "x.py")
		return contextOf(t, tr.event("PreToolUse", session, agent, "Write", map[string]any{"file_path": target}))
	}
	tr.parentOnly()
	if got := prompt("s1"); got != "" {
		t.Errorf("承認前のプロンプトの文脈 = %q", got)
	}
	before("s1", "")
	tr.event("SessionStart", "s1", "", "", nil)
	tr.nextChild("i0001-01-01")
	tr.approveYes("i0001", "i0001-01-01")
	ticketkit.Lacks(t, prompt("s1"), "承認され")
	ticketkit.Lacks(t, before("s1", ""), "承認され")
	ticketkit.Lacks(t, before("s2", "sub-1"), "承認され")
	// セッション開始の一覧は次の子を名指しする。承認の知らせは言わない。
	ticketkit.Lacks(t, contextOf(t, tr.event("SessionStart", "s1", "", "", nil)), "承認され")
	ticketkit.Lacks(t, prompt("s2"), "承認され")
	// 承認の後も判定は同じ形で返り、親のワークツリーは置き場の外に書けない。
	r := tr.event("PreToolUse", "s1", "", "Write", map[string]any{"file_path": filepath.Join(tr.ParentTree, "src", "x.py")})
	if decision(t, r) != "deny" {
		t.Errorf("decision = %q", decision(t, r))
	}
	ticketkit.Has(t, ticketkit.Reason(t, r), "DENY_TICKET_PARENT")
	// 伝えた記録（approved-<セッション>-<エージェント>.json）は書かない。
	if found, _ := filepath.Glob(filepath.Join(tr.State, "approved-*")); len(found) != 0 {
		t.Errorf("承認を伝えた記録が書かれた: %v", found)
	}
}
