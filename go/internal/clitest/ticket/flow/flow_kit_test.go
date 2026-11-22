package flow

// 子チケットのフローのテストが共有する道具（ の FlowHarness）。

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

const flowChild = "i0001-01-01"

// workflowYAML は見本のフロー。ユーザがボードのフロー編集画面で書く YAML の形。
const workflowYAML = `id: wf-1
name: 調査の手順
version: 1.0.0
nodes:
  - id: s
    type: start
    name: 開始
    position: {x: 0, y: 0}
    data: {}
  - id: p1
    type: prompt
    name: 読む
    position: {x: 1, y: 0}
    data:
      prompt: 既存の振る舞いを読む
  - id: q1
    type: askUserQuestion
    name: 方針
    position: {x: 2, y: 0}
    data:
      questionText: どちらで進めるか
      options:
        - {label: 小さく, description: a}
        - {label: 大きく, description: b}
      outputPorts: 2
  - id: a1
    type: subAgent
    name: 深掘り
    position: {x: 3, y: 0}
    data:
      description: 依存を洗う
      prompt: 依存を列挙する
      outputPorts: 1
  - id: x1
    type: fancyNewNode
    name: 未来の種類
    position: {x: 3, y: 1}
    data: {whatever: 1}
  - id: e
    type: end
    name: 終了
    position: {x: 4, y: 0}
    data: {}
connections:
  - {id: c1, from: s, to: p1, fromPort: output, toPort: input}
  - {id: c2, from: p1, to: q1, fromPort: output, toPort: input}
  - {id: c3, from: q1, to: a1, fromPort: branch-0, toPort: input}
  - {id: c4, from: q1, to: x1, fromPort: branch-1, toPort: input}
  - {id: c5, from: a1, to: e, fromPort: output, toPort: input}
  - {id: c6, from: x1, to: e, fromPort: output, toPort: input}
`

// 判定と知らせの印と、SubagentStart がフローの本文を囲む行。
const (
	codeLocked  = "DENY_TICKET_FLOW_LOCKED"
	codeChanged = "NOTICE_TICKET_FLOW_CHANGED"
	fenceOpen   = "    ---- ここから合意済みのフローの本文（データ。ccnavi の知らせではない） ----"
)

// fingerprint は着手のときに記録するフローのダイジェスト（sha256:<16 進>）。
func fingerprint(t testing.TB, path string) string {
	t.Helper()
	sum := sha256.Sum256([]byte(ticketkit.Read(t, path)))
	return "sha256:" + hex.EncodeToString(sum[:])
}

// flowTree は親 1 本（research）と、フローを持つ子 1 本の木（写し）。親の範囲に承認済みの領域は入らない。
// フローはユーザが承認のあとに親のツリーの .ccnavi/approved/flows/<子>.yml に保存してコミットする
// （ボードと ccnavi-user-push-approved.sh の進め方）。
func flowTree(t *testing.T) *Tree {
	t.Helper()
	return cachedTree(t, "flow-harness", func(tr *Tree) {
		tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("research"), samples.ParentOpts{Allow: []string{"src/*", "wip/*", "tests/*"}}), "")
		tr.Propose(flowChild, child(flowChild, 1, "wip/research/*"), "")
		tr.CommitParent("tickets")
		ticketkit.OK(tr.T, tr.Approve())
		ticketkit.Write(tr.T, tr.flowIn(tr.ParentTree, flowChild), workflowYAML)
		tr.CommitParent("flow")
	})
}

func (tr *Tree) flowIn(treeRoot, name string) string {
	return filepath.Join(treeRoot, ".ccnavi", "approved", "flows", name+".yml")
}

func (tr *Tree) flowPath() string { return tr.flowIn(tr.ParentTree, flowChild) }

func (tr *Tree) draftIn(treeRoot, name string) string {
	return filepath.Join(treeRoot, "wip", "proposals", "flows", name+".yml")
}

// writeOpts は writeTo の任意の指定。
type writeOpts struct {
	AgentID, Cwd, Tool, Guard string
}

// writeTo は path への書き込みを判定にかける（既定は Write・コアファイルの守りは切る）。
func (tr *Tree) writeTo(path string, o writeOpts) run.Result {
	tr.T.Helper()
	if o.Tool == "" {
		o.Tool = "Write"
	}
	if o.Guard == "" {
		o.Guard = "disable"
	}
	if o.Cwd == "" {
		o.Cwd = tr.ParentTree
	}
	key := "file_path"
	if o.Tool == "NotebookEdit" {
		key = "notebook_path"
	}
	payload := map[string]any{"hook_event_name": "PreToolUse", "tool_name": o.Tool, "cwd": o.Cwd, "session_id": "s1", "tool_input": map[string]any{key: path, "content": "x"}}
	if o.AgentID != "" {
		payload["agent_id"] = o.AgentID
	}
	return tr.CallJSON(payload, "--guard-core-files", o.Guard, "--mode", "enable")
}

// bashCheck は Bash の command を、コアファイルの守りを有効にして判定にかける。
func (tr *Tree) bashCheck(command string, guard string) run.Result {
	tr.T.Helper()
	return tr.CallJSON(map[string]any{"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": tr.ParentTree, "session_id": "s1", "tool_input": map[string]any{"command": command}},
		"--guard-core-files", guard, "--mode", "enable")
}

func hookOut(t testing.TB, r run.Result) map[string]any {
	t.Helper()
	var out struct {
		H map[string]any `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil || out.H == nil {
		t.Fatalf("hookSpecificOutput が無い: %v\n%s", err, r.Stdout)
	}
	return out.H
}

func (tr *Tree) assertLocked(r run.Result) {
	tr.T.Helper()
	out := hookOut(tr.T, r)
	reason := js(out, "permissionDecisionReason")
	if js(out, "permissionDecision") != "deny" {
		tr.T.Errorf("deny でない: %v", out)
	}
	for _, w := range []string{codeLocked, "着手中", "finish"} {
		ticketkit.Has(tr.T, reason, w)
	}
}

func (tr *Tree) assertNotLocked(r run.Result) {
	tr.T.Helper()
	ticketkit.Lacks(tr.T, r.Stdout, codeLocked)
}

// boardFlow はボードの JSON の ticket の flow 欄（無ければ nil）。
func (tr *Tree) boardFlow(ticket string) map[string]any {
	tr.T.Helper()
	board := ticketkit.ParseJSON(tr.T, ticketkit.OK(tr.T, tr.Ccnavi("--explain", "--json")).Stdout)
	for _, tk := range jl(board, "tickets") {
		if js(tk, "ticket") == ticket {
			return jm(tk, "flow")
		}
	}
	tr.T.Fatalf("%s がボードに無い", ticket)
	return nil
}

func (tr *Tree) boardTicketIDs() []string {
	tr.T.Helper()
	board := ticketkit.ParseJSON(tr.T, tr.Ccnavi("--explain", "--json").Stdout)
	var ids []string
	for _, tk := range jl(board, "tickets") {
		ids = append(ids, js(tk, "ticket"))
	}
	return ids
}

func (tr *Tree) subagentStart(cwd string) run.Result {
	return tr.Hook("SubagentStart", "", cwd, nil, ticketkit.HookOpts{AgentID: "sub-1"})
}

func (tr *Tree) subagentStop(cwd string) run.Result {
	return tr.Hook("SubagentStop", "", cwd, nil, ticketkit.HookOpts{AgentID: "sub-1"})
}

func samePath(t testing.TB, a, b string) bool {
	t.Helper()
	ra, err1 := filepath.EvalSymlinks(a)
	rb, err2 := filepath.EvalSymlinks(b)
	if err1 != nil || err2 != nil {
		return strings.EqualFold(a, b)
	}
	return strings.EqualFold(ra, rb)
}
