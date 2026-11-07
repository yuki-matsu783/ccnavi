package reasons

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/testkit"
)

func TestShorten(t *testing.T) {
	limit := texts.ShownSubjectLimit
	if got := shorten("git\n  push   origin"); got != "git push origin" {
		t.Errorf("上限以内はまとめるだけ: %q", got)
	}
	// 残りの字数は、まとめた後の長さで数える（まとめる前で数えると見えていない字数を多く言う）。
	folded := strings.TrimSpace(strings.Repeat("a ", limit))
	want := folded[:limit] + fmt.Sprintf("…(+%d)", len(folded)-limit)
	if got := shorten(strings.Repeat("a\n\n\n   ", limit)); got != want {
		t.Errorf("%q", got)
	}
	if got := shorten(strings.Repeat("あ", limit+3)); got != strings.Repeat("あ", limit)+"…(+3)" {
		t.Errorf("符号位置で数える: %q", got)
	}
}

// 1 件の呼び出し。Python と同じ表で両方を呼ぶ。
type sayCase struct {
	Kind     string            `json:"kind"`
	Attended string            `json:"attended"`
	Args     map[string]any    `json:"args"`
	Rule     map[string]string `json:"rule,omitempty"`
}

func str(m map[string]any, k string) string {
	v, _ := m[k].(string)
	return v
}

func flag(m map[string]any, k string) bool {
	v, _ := m[k].(bool)
	return v
}

func strs(m map[string]any, k string) []string {
	var out []string
	for _, v := range m[k].([]any) {
		out = append(out, v.(string))
	}
	return out
}

func (c sayCase) rule(root string) *rules.Rule {
	return &rules.Rule{
		ID: c.Rule["id"], BareID: c.Rule["id"], Message: c.Rule["message"], Decision: rules.Decision(c.Rule["decision"]),
		Root: root, FromFile: c.Rule["file"] != "", File: c.Rule["file"],
	}
}

func (c sayCase) run(root string) string {
	a := c.Args
	switch c.Kind {
	case "fallen_back":
		return FallenBack(str(a, "path"), root)
	case "undeclared":
		return Undeclared(root, str(a, "tool"), str(a, "subject"), str(a, "source"), shell.Reason(str(a, "degraded")),
			rules.Decision(str(a, "verdict")), flag(a, "fell_back"))
	case "reason_for":
		return ReasonFor(root, Hit{Rule: c.rule(root), Tool: str(a, "tool"), Subject: str(a, "subject"),
			Degraded: shell.Reason(str(a, "degraded")), Runner: str(a, "runner"), Inner: str(a, "inner"), Quoted: flag(a, "quoted")},
			str(a, "git"))
	case "ask_hint":
		return strings.Join(AskHint(root, str(a, "bare_id"), str(a, "subject")), "\n")
	case "subagent_forbidden":
		return SubagentForbidden(root, str(a, "subject"), str(a, "runner"), str(a, "inner"))
	case "builtin_refusal":
		return BuiltinRefusal(str(a, "code"), str(a, "rule_id"), str(a, "subject"), str(a, "text"))
	case "internal_error":
		return InternalError(root, str(a, "code"), hookio.Event(str(a, "event")), str(a, "kind"), str(a, "log"), flag(a, "passed"))
	case "internal_error_notice":
		return InternalErrorNotice(str(a, "code"), hookio.Event(str(a, "event")), str(a, "kind"), str(a, "log"))
	case "payload_unusable":
		return PayloadUnusable(root, str(a, "code"), str(a, "problem"), hookio.Event(str(a, "event")), config.Mode(str(a, "mode")))
	case "deadline_exceeded":
		return DeadlineExceeded(root, str(a, "code"), time.Duration(a["seconds"].(float64)*float64(time.Second)), flag(a, "passed"))
	case "rewrite":
		return Rewrite(root, str(a, "subject"), shell.Form(str(a, "form")), strs(a, "found"), str(a, "git"))
	}
	panic(c.Kind)
}

const sayOracle = `
import json, os, sys
from ccnavi.hook import reasons
from ccnavi.policy import rules
c = json.load(sys.stdin)
root = c["root"]
def rule(r):
    return rules.Rule(id=r["id"], bare_id=r["id"], message=r["message"], decision=r["decision"], root=root,
                      from_file=bool(r["file"]), file=r["file"])
out = []
for k in c["cases"]:
    os.environ["CLAUDE_CODE_SESSION_ATTENDED"] = k["attended"]
    a, kind = k["args"], k["kind"]
    git = a.get("git") or None
    if kind == "fallen_back":
        s = reasons.fallen_back(a["path"], root)
    elif kind == "undeclared":
        s = reasons.undeclared(a["tool"], a["subject"], a["source"], a["degraded"], verdict=a["verdict"],
                               fell_back=a["fell_back"], root=root)
    elif kind == "reason_for":
        s = reasons.reason_for(rule(k["rule"]), a["tool"], a["subject"], "unused", a["degraded"], a["runner"], a["inner"],
                               a["quoted"], root=root, git=git)
    elif kind == "ask_hint":
        s = "\n".join(reasons.ask_hint(root, a["bare_id"], a["subject"]))
    elif kind == "subagent_forbidden":
        s = reasons.subagent_forbidden(root, a["subject"], a["runner"], a["inner"])
    elif kind == "builtin_refusal":
        s = reasons.builtin_refusal(a["code"], a["rule_id"], a["subject"], a["text"])
    elif kind == "internal_error":
        s = reasons.internal_error(root, a["code"], a["event"], a["kind"], a["log"], passed=a["passed"])
    elif kind == "internal_error_notice":
        s = reasons.internal_error_notice(a["code"], a["event"], a["kind"], a["log"])
    elif kind == "payload_unusable":
        s = reasons.payload_unusable(root, a["code"], a["problem"], event=a["event"], mode=a["mode"])
    elif kind == "deadline_exceeded":
        s = reasons.deadline_exceeded(root, a["code"], a["seconds"], passed=a["passed"])
    elif kind == "rewrite":
        s = reasons.rewrite(root, a["subject"], a["form"], a["found"], git)
    out.append(s)
json.dump(out, sys.stdout, ensure_ascii=False)
`

func sayCases() []sayCase {
	long := strings.Repeat("rm -rf  x\n", 40)
	var cases []sayCase
	add := func(kind, attended string, args map[string]any, rule map[string]string) {
		cases = append(cases, sayCase{Kind: kind, Attended: attended, Args: args, Rule: rule})
	}
	add("fallen_back", "1", map[string]any{"path": "/p/rules.yml"}, nil)
	degradeds := []string{"", string(shell.ReasonUnterminated), string(shell.ReasonTakenAsCode), string(shell.ReasonAnsiC),
		string(shell.ReasonUnterminatedSubst), string(shell.ReasonBackquote)}
	for _, attended := range []string{"0", "1"} {
		for _, degraded := range degradeds {
			for _, verdict := range []string{"ask", "deny", "handover"} {
				for _, fellBack := range []bool{false, true} {
					for _, tool := range []string{"Bash", "Write"} {
						add("undeclared", attended, map[string]any{"tool": tool, "subject": long, "source": ".ccnavi/config/common/rules.yml",
							"degraded": degraded, "verdict": verdict, "fell_back": fellBack}, nil)
					}
				}
			}
		}
	}
	ruleSet := []map[string]string{
		{"id": "no-rm", "message": "消さない {root}", "decision": "deny", "file": ".ccnavi/config/common/rules.yml"},
		{"id": "look", "message": "", "decision": "ask", "file": "rules.yml"},
		{"id": "builtin:x", "message": "組み込み", "decision": "deny", "file": ""},
		{"id": useronly.TicketApprovalRuleID, "message": "承認", "decision": "deny", "file": ""},
	}
	for _, r := range ruleSet {
		for _, degraded := range degradeds[:3] {
			for _, via := range [][2]string{{"", ""}, {"env", "rm\x00-rf\x01x"}, {shell.Moved, "/w/settings.json"}} {
				for _, quoted := range []bool{false, true} {
					for _, git := range []string{"", "sh /w/.ccnavi/scripts/ccnavi-git.sh"} {
						add("reason_for", "0", map[string]any{"tool": "Bash", "subject": "rm -rf x", "degraded": degraded,
							"runner": via[0], "inner": via[1], "quoted": quoted, "git": git}, r)
					}
				}
			}
		}
		add("reason_for", "1", map[string]any{"tool": "Edit", "subject": long, "degraded": "", "runner": "", "inner": "",
			"quoted": false, "git": ""}, r)
	}
	for _, attended := range []string{"0", "1", ""} {
		for _, id := range []string{"direct-worktree-confirm", "prefer-webfetch", "dependencies"} {
			for _, subject := range []string{"sh x/ccnavi-worktree.sh a --adopt --base main", "sh x/ccnavi-worktree.sh a --ADOPT=1",
				"sh x/ccnavi-worktree.sh a --adoption-x", "sh x/ccnavi-worktree.sh a\u3000--adopt", "curl http://x"} {
				add("ask_hint", attended, map[string]any{"bare_id": id, "subject": subject}, nil)
			}
		}
	}
	add("subagent_forbidden", "1", map[string]any{"subject": strings.Repeat("x", 205), "runner": "", "inner": ""}, nil)
	add("subagent_forbidden", "1", map[string]any{"subject": "env ccnavi ticket finish", "runner": "env", "inner": "ccnavi\x00ticket finish"}, nil)
	add("builtin_refusal", "1", map[string]any{"code": "CODE", "rule_id": "rule-id", "subject": strings.Repeat("y", 207), "text": "text"}, nil)
	for _, event := range []string{"PreToolUse", "PostToolUse", "Stop"} {
		for _, passed := range []bool{false, true} {
			add("internal_error", "1", map[string]any{"code": "internal-error", "event": event, "kind": "IndexError", "log": "/log", "passed": passed}, nil)
		}
	}
	add("internal_error_notice", "1", map[string]any{"code": "internal-error", "event": "Stop", "kind": "E", "log": "/log"}, nil)
	for _, event := range []string{"", "PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "SessionStart"} {
		for _, mode := range []string{"enable", "dry-run"} {
			add("payload_unusable", "1", map[string]any{"code": "payload-unusable", "problem": "壊れた JSON", "event": event, "mode": mode}, nil)
		}
	}
	for _, seconds := range []float64{3, 2.5, 0.25} {
		for _, passed := range []bool{false, true} {
			add("deadline_exceeded", "1", map[string]any{"code": "deadline-exceeded", "seconds": seconds, "passed": passed}, nil)
		}
	}
	for _, form := range []shell.Form{shell.FormBrace, shell.FormCommandName, shell.FormBackquote, shell.FormAmbiguous} {
		for _, n := range []int{1, 5, 7} {
			var found []any
			for i := range n {
				found = append(found, fmt.Sprintf("{a,b}%d\x00x", i))
			}
			for _, git := range []string{"", "sh /w/.ccnavi/scripts/ccnavi-git.sh"} {
				add("rewrite", "1", map[string]any{"subject": "echo {a,b}", "form": string(form), "found": found, "git": git}, nil)
			}
		}
	}
	return cases
}

func TestTextsMatchPython(t *testing.T) {
	root := testkit.RepoRoot(t)
	cases := sayCases()
	want := testkit.Answers[[]string](t, "", sayOracle, map[string]any{"root": root, "cases": cases})
	for i, c := range cases {
		t.Setenv(env.ClaudeSessionAttended, c.Attended)
		if got := c.run(root); got != want[i] {
			t.Errorf("%s %v %v:\nGo:\n%s\nPython:\n%s", c.Kind, c.Attended, c.Args, got, want[i])
		}
	}
}

func TestUndeclaredHasTextsForEveryVerdict(t *testing.T) {
	root := testkit.RepoRoot(t)
	for _, degraded := range []shell.Reason{"", shell.ReasonUnterminated} {
		for _, verdict := range []rules.Decision{rules.Ask, rules.Deny} {
			for _, fellBack := range []bool{false, true} {
				said := Undeclared(root, "Bash", "ls", "/s/rules.yml", degraded, verdict, fellBack)
				if said == "" || strings.Contains(said, "{") || strings.Contains(said, "引けませんでした") {
					t.Errorf("%q %s %v: %q", degraded, verdict, fellBack, said)
				}
			}
		}
	}
	if Undeclared(root, "Bash", "ls", "s", "", rules.Decision("handover"), false) != "" {
		t.Error("渡した回に文を組んだ")
	}
}

func TestRewriteCodeAndRule(t *testing.T) {
	if RewriteCode(shell.FormBrace) != "DENY_BRACE_EXPANSION" || RewriteRule(shell.FormBrace) != "builtin:brace-expansion" {
		t.Error(RewriteCode(shell.FormBrace), RewriteRule(shell.FormBrace))
	}
	if CodeFor("Bash", "") != CodeCommand || CodeFor("Write", "") != CodePath || CodeFor("Bash", shell.ReasonAnsiC) != CodeUncertain {
		t.Error("CodeFor")
	}
}
