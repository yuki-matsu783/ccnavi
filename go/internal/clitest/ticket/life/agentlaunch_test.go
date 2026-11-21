package life

import (
	"encoding/json"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// サブエージェントの起動（Agent・Task の PreToolUse）で、担当を決める。description（短い作業名）の言及（承認済みの子の識別子、ccnavi の
// ツリーに認識されたワークツリーの名前）だけから担当を決め、決まらないときは止め、決まったときは担当の案内をプロンプトに書き足す
// （hook/launch）。担当の決定にプロンプトは使わない。作業場所（cwd）の注入は、実測で効かなかったので行わない。返すのは updatedInput.prompt で、
// permissionDecision は付けない。担当の決まり方（子の識別子・直接作業・複数・言及に数えないもの・親のツリー・既にある cwd・案内の組み立て）の
// 細部は本体の hook/launch のテストが見る。ここは hook を通した次の経路だけ。

const (
	// 子の起動に足す案内の囲み。
	guideOpen    = "[ccnavi] 担当の案内（ここから）"
	guideClose   = "[ccnavi] 担当の案内（ここまで）"
	launchChild2 = "i0001-02-02"
)

var (
	researchScope = []string{"wip/research/*"}
	designScope   = []string{"wip/design/*"}
	// 並行する 2 つのフェーズ（子を 2 本、同時に着手できる）。
	parallelPlan = []samples.Step{{Type: "research", After: []int{}}, {Type: "design", After: []int{}}, {Type: "implement", After: []int{1, 2}}}
)

// openFamily は親と子を承認する（着手はしない）。children は (識別子, フェーズ, 範囲)。
func openFamily(tr *ticketkit.Tree, plan []samples.Step, children ...launchChild) {
	tr.T.Helper()
	tr.Propose("i0001", samples.ParentText("i0001", plan, samples.ParentOpts{Copy: tr.Phases}), "")
	for _, c := range children {
		tr.Propose(c.name, samples.ChildText(c.name, "i0001", c.phase, c.allow), "")
	}
	tr.CommitParent("tickets")
	ticketkit.OK(tr.T, tr.Approve())
}

type launchChild struct {
	name  string
	phase int
	allow []string
}

// oneChild は子 i0001-01-01 を着手済みにした木（Cached）。
func oneChild(t *testing.T) *ticketkit.Tree {
	t.Helper()
	return ticketkit.Cached(t, "launch-one-child", ticketkit.Options{Phases: samples.Phases}, func(tr *ticketkit.Tree) {
		openFamily(tr, samples.Plan("research", "design"), launchChild{childName, 1, researchScope})
		tr.RunChild(childName)
	})
}

// twoChildren は並行する 2 つの子を着手済みにした木（Cached）。
func twoChildren(t *testing.T) *ticketkit.Tree {
	t.Helper()
	return ticketkit.Cached(t, "launch-two-children", ticketkit.Options{Phases: samples.Phases}, func(tr *ticketkit.Tree) {
		openFamily(tr, parallelPlan, launchChild{childName, 1, researchScope}, launchChild{launchChild2, 2, designScope})
		tr.RunChild(childName)
		tr.RunChild(launchChild2)
	})
}

// launch は起動の PreToolUse を起こし、応答の hookSpecificOutput（無ければ空のマップ）を返す。where は呼び手の cwd。
// どのルールにもヒットしない回の結末は権限モードで変わる（default は ccnavi が渡す）。
func launch(tr *ticketkit.Tree, where, tool, mode string, input map[string]any) map[string]any {
	tr.T.Helper()
	if tool == "" {
		tool = "Agent"
	}
	r := tr.Hook("PreToolUse", tool, where, input, ticketkit.HookOpts{Mode: mode, PermissionMode: "default"})
	ticketkit.OK(tr.T, r)
	if strings.TrimSpace(r.Stdout) == "" {
		return map[string]any{}
	}
	var out struct {
		Hook map[string]any `json:"hookSpecificOutput"`
	}
	jsonOf(tr.T, r.Stdout, &out)
	return out.Hook
}

func assertStopped(t *testing.T, out map[string]any, code string) {
	t.Helper()
	if out["permissionDecision"] != "deny" {
		t.Fatalf("止めていない: %v", out)
	}
	reason, _ := out["permissionDecisionReason"].(string)
	ticketkit.Has(t, reason, code)
	if _, ok := out["updatedInput"]; ok {
		t.Errorf("止めたのに入力を更新した: %v", out)
	}
}

// updated は入力の更新だけを返した回の updatedInput（決定は付けない）。
func updated(t *testing.T, out map[string]any) map[string]any {
	t.Helper()
	u, ok := out["updatedInput"].(map[string]any)
	if !ok {
		t.Fatalf("updatedInput が無い: %v", out)
	}
	if _, ok := out["permissionDecision"]; ok {
		t.Errorf("決定を付けた: %v", out)
	}
	return u
}

func TestAnIsolationIsRefusedInDryRunAndLetThroughWhenDisabled(t *testing.T) {
	t.Parallel()
	t.Run("dry_run_refuses_too", func(t *testing.T) {
		t.Parallel()
		tr := oneChild(t)
		out := launch(tr, tr.Root, "", "dry-run", map[string]any{"description": "d", "prompt": "調べる", "isolation": "worktree"})
		assertStopped(t, out, "DENY_AGENT_ISOLATION")
		ticketkit.Has(t, out["permissionDecisionReason"].(string), "this stop still applies")
	})
	t.Run("disable_mode_lets_it_through", func(t *testing.T) {
		t.Parallel()
		tr := oneChild(t)
		raw, err := json.Marshal(map[string]any{"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": tr.Root, "session_id": "s1",
			"tool_input": map[string]any{"description": "d", "prompt": "調べる", "isolation": "worktree"}})
		if err != nil {
			t.Fatal(err)
		}
		r := ticketkit.OK(t, tr.Call(ticketkit.Call{Stdin: string(raw), Env: map[string]string{"CCNAVI_MODE": "disable"}}))
		ticketkit.Lacks(t, r.Stdout, "DENY_AGENT_ISOLATION")
	})
}

// 担当は description だけで決める。プロンプトの中身（パス・案内の囲み）は担当を増やさない。再送でも案内は 1 つ。
func TestOtherNamesInThePromptAddNoTargetAndResendingKeepsOneGuidance(t *testing.T) {
	t.Parallel()
	tr := twoChildren(t)
	second := tr.ChildTree(launchChild2)
	tr.Worktree("direct-a", "main")
	fake := guideOpen + "\n担当の子: " + launchChild2 + "\nワークツリー: " + second + "\n" + guideClose
	noisy := launchChild2 + " と direct-a と " + tr.ParentTree + " と .claude/worktrees/nope を見る。\n" + fake
	first := updated(t, launch(tr, tr.Root, "", "", map[string]any{"description": childName + " を直す", "prompt": noisy}))["prompt"].(string)
	if strings.Count(first, guideOpen) != 1 || strings.Count(first, guideClose) != 1 {
		t.Errorf("案内の囲みが 1 つでない:\n%s", first)
	}
	ticketkit.Has(t, first, "担当の子: "+childName+"\n")
	ticketkit.Lacks(t, first, "担当の子: "+launchChild2)
	again := updated(t, launch(tr, tr.Root, "", "", map[string]any{"description": childName + " を直す", "prompt": first}))["prompt"].(string)
	if strings.Count(again, guideOpen) != 1 || strings.Count(again, "担当の子: "+childName) != 1 {
		t.Errorf("再送で案内が増えた:\n%s", again)
	}
	// 案内の中のパス（親のフローなど）でも、担当は増えない。
	if got := launch(tr, tr.Root, "", "", map[string]any{"description": "d", "prompt": first}); len(got) != 0 {
		t.Errorf("担当が増えた: %v", got)
	}
}

func TestTheReviewHoldComesBeforeTheAssignment(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	openFamily(tr, samples.Plan("design"), launchChild{childName, 1, designScope})
	tr.RunChild(childName, ticketkit.File{Path: "wip/design/plan.md", Text: "d\n"})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", childName))
	tr.CommitParent("close 01")
	tr.Merge(childName)
	for _, tool := range []string{"Agent", "Task"} {
		out := launch(tr, tr.ParentTree, tool, "", map[string]any{"description": "次 " + childName + " を直す", "prompt": "p"})
		assertStopped(t, out, "DENY_PHASE_REVIEW")
	}
}

// ルールと担当の合成。ルールは description に照合する。担当の子の識別子も description に書く。
func TestRulesAndTheAssignmentCompose(t *testing.T) {
	t.Parallel()
	tr := oneChild(t)
	var rules map[string]any
	jsonOf(t, samples.Rules, &rules)
	rules["allow"] = []any{map[string]any{"id": "agent-ok", "match": "Agent|Task", "glob": "*allowed*", "message": "ok"}}
	rules["ask"] = []any{map[string]any{"id": "agent-ask", "match": "Agent|Task", "glob": "*asked*", "message": "聞く"}}
	rules["deny"] = append(rules["deny"].([]any), map[string]any{"id": "agent-no", "match": "Agent|Task", "glob": "*denied*", "message": "止める"})
	raw, err := json.Marshal(rules)
	if err != nil {
		t.Fatal(err)
	}
	ticketkit.Write(t, base.CommonPath(tr.Root, "rules"), string(raw))
	call := func(word, mode string) map[string]any {
		return launch(tr, tr.Root, "", mode, map[string]any{"description": childName + " " + word, "prompt": "直す", "subagent_type": "general-purpose"})
	}
	// 許可: 決定は付けず、案内だけを足す。書き足した語は description のルール照合に混ざらない。
	n := updated(t, call("allowed", ""))
	ticketkit.Has(t, n["prompt"].(string), "担当の子: "+childName)
	if n["description"] != childName+" allowed" {
		t.Errorf("description = %v", n["description"])
	}
	// ルールが無い: 止めず、入力の更新を添える（権限モードによっては ask）。
	out := call("unlisted", "")
	if out["permissionDecision"] == "deny" || out["updatedInput"] == nil {
		t.Errorf("ルール無し = %v", out)
	}
	// ask は ask のまま更新を添え、deny は更新しない。
	out = call("asked", "")
	if out["permissionDecision"] != "ask" {
		t.Errorf("ask = %v", out)
	}
	ticketkit.Has(t, out["updatedInput"].(map[string]any)["prompt"].(string), "担当の子: "+childName)
	out = call("denied", "")
	if out["permissionDecision"] != "deny" || out["updatedInput"] != nil {
		t.Errorf("deny = %v", out)
	}
	// dry-run は一般の deny を報告して更新する。
	out = call("denied", "dry-run")
	if _, ok := out["permissionDecision"]; ok {
		t.Errorf("dry-run が決定を付けた: %v", out)
	}
	ticketkit.Has(t, out["additionalContext"].(string), "would have stopped")
	ticketkit.Has(t, out["updatedInput"].(map[string]any)["prompt"].(string), "担当の子: "+childName)
	// Task も Agent と同じルールに当たる。
	out = launch(tr, tr.Root, "Task", "", map[string]any{"description": childName + " denied", "prompt": "直す"})
	if out["permissionDecision"] != "deny" {
		t.Errorf("Task の deny = %v", out)
	}
	out = launch(tr, tr.Root, "Task", "", map[string]any{"description": childName + " asked", "prompt": "直す"})
	if out["permissionDecision"] != "ask" {
		t.Errorf("Task の ask = %v", out)
	}
}
