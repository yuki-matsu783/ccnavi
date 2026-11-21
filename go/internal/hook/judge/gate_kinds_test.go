package judge

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// 止める箇所の分類（承認済みの置き場と操作記録の置き場はどのモードでも止める）と、サブエージェントの役割の表。
// 受入テスト（tests/guard/test_gate_kinds.py）が hook を 1 件ずつ起こして見ていた組み合わせを、ここで全部見る。

// emptyRulesWorkspace はルールを 1 本も持たない共通レイヤーだけのワークスペース。
func emptyRulesWorkspace(t *testing.T) string {
	t.Helper()
	root := testkit.TempRoot(t)
	testkit.Put(t, filepath.Join(root, config.DefaultRules), `{"version": 1}`)
	if err := os.MkdirAll(filepath.Join(root, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.CopyMessages(t, root)
	return root
}

// hookSays は 1 件を判定して、決定と、理由（無ければ文脈）を返す。
func hookSays(t *testing.T, root, tool string, input map[string]any, env map[string]string, extra ...string) (string, string) {
	t.Helper()
	a := runGo(t, root, judgeCase{Payload: call(tool, root, input, extra...), Env: env})
	if a.Code != 0 || a.Error != "" {
		t.Fatalf("%s %v: %d %s %s", tool, input, a.Code, a.Error, a.Stderr)
	}
	if strings.TrimSpace(a.Stdout) == "" {
		return "", ""
	}
	var out struct {
		Hook struct {
			Decision string `json:"permissionDecision"`
			Reason   string `json:"permissionDecisionReason"`
			Context  string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(a.Stdout), &out); err != nil {
		t.Fatalf("%s: %v", a.Stdout, err)
	}
	text := out.Hook.Reason
	if text == "" {
		text = out.Hook.Context
	}
	return out.Hook.Decision, text
}

var gatePlaces = []string{
	".ccnavi/approved/phases/i0001/1.reviewed", ".ccnavi/approved/phases/i0001/accepted.json",
	".ccnavi/approved/done/i0001-01-01.md", ".ccnavi/approved/doing/i0001-01-01.md", ".ccnavi/approved/flows/i0001.md",
	"logs/state/ops/a1.json",
}

func TestThePlacesAreStoppedInAnyMode(t *testing.T) {
	root := emptyRulesWorkspace(t)
	for _, rel := range gatePlaces {
		path := filepath.Join(root, filepath.FromSlash(rel))
		for _, mode := range []string{"enable", "dry-run"} {
			for _, core := range []string{"enable", "disable"} {
				env := with("CCNAVI_MODE", mode, "CCNAVI_GUARD_CORE_FILES", core)
				for _, tool := range []string{config.ToolWrite, config.ToolEdit} {
					if d, text := hookSays(t, root, tool, map[string]any{"file_path": path}, env); d != "deny" {
						t.Errorf("%s %s (%s, core %s): %q %s", tool, rel, mode, core, d, text)
					}
				}
			}
			env := with("CCNAVI_MODE", mode, "CCNAVI_GUARD_CORE_FILES", "disable")
			for _, command := range []string{"echo x > " + rel, "rm " + rel, "cp /tmp/x " + rel, "tee " + rel} {
				if d, text := hookSays(t, root, config.ToolBash, map[string]any{"command": command}, env); d != "deny" {
					t.Errorf("%q (%s): %q %s", command, mode, d, text)
				}
			}
		}
		d, text := hookSays(t, root, config.ToolRead, map[string]any{"file_path": path}, with("CCNAVI_MODE", "dry-run"))
		if d == "deny" || strings.Contains(text, "builtin:ticket-approved") || strings.Contains(text, "builtin:ticket-ops") {
			t.Errorf("Read %s: %q %s", rel, d, text)
		}
	}
}

var subagentStopped = []string{
	"sh .ccnavi/scripts/ccnavi-ticket.sh start i0001", "sh .ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01",
	"sh .ccnavi/scripts/ccnavi-ticket.sh cancel i0001-01-01", "sh .ccnavi/scripts/ccnavi-ticket.sh approval-request i0001",
	"sh .ccnavi/scripts/ccnavi-ticket.sh record-risk i0001", "sh .ccnavi/scripts/ccnavi-ticket.sh judge i0001",
	"sh .ccnavi/scripts/ccnavi-ticket.sh unknown-sub", "sh .ccnavi/scripts/ccnavi-ticket.sh",
	"bash -x .ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01", "./.ccnavi/scripts/ccnavi-ticket.sh start i0001",
	"/work/.ccnavi/scripts/ccnavi-ticket.sh start i0001", "'/work/.ccnavi/scripts/ccnavi-ticket.sh' start i0001",
	"exec sh .ccnavi/scripts/ccnavi-ticket.sh start i0001", "command sh .ccnavi/scripts/ccnavi-ticket.sh start i0001",
	"source .ccnavi/scripts/ccnavi-ticket.sh start i0001", ". .ccnavi/scripts/ccnavi-ticket.sh start i0001",
	"cd x && sh .ccnavi/scripts/ccnavi-ticket.sh start i0001", "env X=1 sh .ccnavi/scripts/ccnavi-ticket.sh start i0001",
	"sh .ccnavi/scripts/ccnavi-review.sh review-request --phase 1", "sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 1",
	"sh .ccnavi/scripts/ccnavi-review.sh comment 3", "sh .ccnavi/scripts/ccnavi-review.sh ready",
	"sh .ccnavi/scripts/ccnavi-review.sh decide 3", "sh .ccnavi/scripts/ccnavi-review.sh unknown-sub",
	"sh .ccnavi/scripts/ccnavi-git.sh push origin x", "sh .ccnavi/scripts/ccnavi-git.sh worktree add x",
	"sh .ccnavi/scripts/ccnavi-git.sh integrate", "sh .ccnavi/scripts/ccnavi-git.sh integrate-done",
	"sh .ccnavi/scripts/ccnavi-start.sh", "sh .ccnavi/scripts/ccnavi-start.sh --issue 3",
	"sh .ccnavi/scripts/ccnavi-start.sh --issue 3 --list-open", "sh .ccnavi/scripts/ccnavi-start.sh --new-issue x --similar-checked",
	"sh .ccnavi/scripts/ccnavi-sync.sh", "sh .ccnavi/scripts/ccnavi-worktree.sh add x",
	"sh .ccnavi/scripts/ccnavi-clean.sh --worktree i0001-01-01", "sh .ccnavi/scripts/ccnavi-launcher.sh worktree add x",
	"sh .ccnavi/scripts/ccnavi-launcher.sh review comment 3", "sh .ccnavi/scripts/ccnavi-launcher.sh",
	"/bin/sh .ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01", "sh -c 'sh .ccnavi/scripts/ccnavi-review.sh review-request --phase 1'",
}

var subagentPassed = []string{
	"sh .ccnavi/scripts/ccnavi-ticket.sh status", "sh .ccnavi/scripts/ccnavi-ticket.sh next-id",
	"sh .ccnavi/scripts/ccnavi-ticket.sh --help", "sh .ccnavi/scripts/ccnavi-ticket.sh learn",
	"sh .ccnavi/scripts/ccnavi-review.sh fetch", "sh .ccnavi/scripts/ccnavi-review.sh origin",
	"sh .ccnavi/scripts/ccnavi-review.sh merged", "sh .ccnavi/scripts/ccnavi-review.sh decide 3 --preview",
	"sh .ccnavi/scripts/ccnavi-review.sh --help", "sh .ccnavi/scripts/ccnavi-git.sh status",
	"sh .ccnavi/scripts/ccnavi-git.sh add -- a.py", "sh .ccnavi/scripts/ccnavi-fetch.sh", "sh .ccnavi/scripts/ccnavi-branches.sh",
	"sh .ccnavi/scripts/ccnavi-launcher.sh --docs x", "sh .ccnavi/scripts/ccnavi-launcher.sh --lint",
	"sh .ccnavi/scripts/ccnavi-launcher.sh --explain x", "sh .ccnavi/scripts/ccnavi-start.sh --list-open",
	"sh .ccnavi/scripts/ccnavi-start.sh --check", "sh .ccnavi/scripts/ccnavi-start.sh --help",
}

const subagentOp = "DENY_SUBAGENT_TICKET_OP"

func TestTheSubagentRoles(t *testing.T) {
	root := emptyRulesWorkspace(t)
	bash := func(command string, env map[string]string, extra ...string) (string, string) {
		return hookSays(t, root, config.ToolBash, map[string]any{"command": command}, env, extra...)
	}
	for _, command := range subagentStopped {
		for _, mode := range []string{"enable", "dry-run"} {
			if d, text := bash(command, with("CCNAVI_MODE", mode), "agent_id", "a1"); d != "deny" {
				t.Errorf("サブエージェントの %q (%s): %q %s", command, mode, d, text)
			}
		}
	}
	for _, command := range subagentStopped[:8] {
		if _, text := bash(command, baseEnv); strings.Contains(text, subagentOp) {
			t.Errorf("メインの %q が役割で止まった: %s", command, text)
		}
	}
	for _, command := range subagentPassed {
		if d, text := bash(command, baseEnv, "agent_id", "a1"); d == "deny" || strings.Contains(text, subagentOp) {
			t.Errorf("サブエージェントの %q: %q %s", command, d, text)
		}
	}
	// 状態を動かさない git の副命令は、役割の表で止めない。push の仲間は止める。
	for _, sub := range []string{"rm", "mv", "restore", "stash", "reset"} {
		for _, mode := range []string{"enable", "dry-run"} {
			command := "sh .ccnavi/scripts/ccnavi-git.sh " + sub + " -- a.py"
			if d, text := bash(command, with("CCNAVI_MODE", mode), "agent_id", "a1"); d == "deny" || strings.Contains(text, subagentOp) {
				t.Errorf("サブエージェントの %q (%s): %q %s", command, mode, d, text)
			}
		}
	}
	for _, sub := range []string{"push", "worktree add x", "integrate"} {
		if d, text := bash("sh .ccnavi/scripts/ccnavi-git.sh "+sub, baseEnv, "agent_id", "a1"); d != "deny" {
			t.Errorf("サブエージェントの git %s: %q %s", sub, d, text)
		}
	}
}
