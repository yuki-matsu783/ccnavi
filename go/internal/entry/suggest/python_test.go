package suggest

import (
	"bytes"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// suggestOracle は Python の --suggest（entry/suggest.report）を文字と JSON で回す。ワークスペースルートは <root> にする。
const suggestOracle = `
import io, json, sys
from ccnavi.infra import settings
from ccnavi.entry import suggest
c = json.load(sys.stdin)
root = c["root"]
out = []
for as_json in (False, True):
    conf, _ = settings.load(root)
    o = io.StringIO()
    code = suggest.report(o, conf, root, as_json)
    text = o.getvalue().replace(json.dumps(root)[1:-1], "<root>").replace(root, "<root>")
    out.append({"code": code, "stdout": text})
print(json.dumps(out, ensure_ascii=False))
`

type answer struct {
	Code   int    `json:"code"`
	Stdout string `json:"stdout"`
}

const sampleRules = `version: 1
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: push しません。ユーザに頼んでください。
  - id: vault
    match: Bash
    when:
      any:
        - glob: "*vault read*"
        - glob: "*vault kv*"
    except:
      - glob: "*--help*"
    message: 秘密は読まない
    additionalContext: 秘密の扱い
    every: 2
allow:
  - id: listing
    match: Bash
    regex: '\bls\b'
`

func line(fields string) string {
	return `{"ts": "2026-09-01T00:00:00+00:00", "mode": "enable", ` + fields + "}"
}

func handover(tool, subject string) string {
	s, _ := json.Marshal(subject)
	return line(fmt.Sprintf(`"event": "PreToolUse", "tool": %q, "subject": %s, "decision": "handover", "enforced": false, "code": "UNDECLARED"`, tool, s))
}

func deny(subject, rule string) string {
	s, _ := json.Marshal(subject)
	return line(fmt.Sprintf(`"event": "PreToolUse", "tool": "Bash", "subject": %s, "decision": "deny", "enforced": true, "code": "DENY_COMMAND_PATTERN", "rules": [%q]`, s, rule))
}

func repeat(n int, f func(int) string) []string {
	var out []string
	for i := range n {
		out = append(out, f(i))
	}
	return out
}

const pushRule = ".ccnavi/config/common/rules.yml:git-push"

func scenes(root string) map[string]map[string][]string {
	return map[string]map[string][]string{
		"no-log": {},
		"handover": {
			"decisions.jsonl": append(repeat(3, func(i int) string { return handover("Bash", fmt.Sprintf("npm install p%d", i%2)) }),
				repeat(6, func(i int) string { return handover("Bash", "FOO=1 /usr/bin/make build") })...),
			"decisions.20260901-000000.jsonl": repeat(3, func(int) string { return handover("Bash", "npm install p0") }),
			"decisions.other.txt":             repeat(9, func(int) string { return handover("Bash", "npm ci") }),
		},
		"paths": {
			"decisions.jsonl": slicesConcat(
				repeat(5, func(i int) string { return handover("Write", fmt.Sprintf("%s/docs/n%d.md", root, i)) }),
				repeat(5, func(i int) string { return handover("Read", fmt.Sprintf("%s/n%d.md", root, i)) }),
				repeat(5, func(i int) string { return handover("WebFetch", fmt.Sprintf("HTTPS://Example.com/p%d", i)) }),
				repeat(5, func(int) string { return handover("Edit", "/etc/x") }),
				repeat(5, func(int) string { return handover("Bash", "git push --force") }),
				repeat(5, func(int) string { return handover("Bash", "cut…(+12)") }),
				[]string{"not json", `["list"]`},
			),
		},
		"denies": {
			"decisions.jsonl": slicesConcat(
				[]string{deny("git push origin main", pushRule), deny("git  push 'origin' main", pushRule),
					deny(`git push "origin" main`, pushRule), deny("git push origin x", pushRule)},
				repeat(3, func(int) string { return deny("vault read secret/a", ".ccnavi/config/common/rules.yml:vault") }),
				repeat(3, func(int) string { return deny("rm x", "builtin:guard-x") }),
			),
		},
	}
}

func slicesConcat(parts ...[]string) []string {
	var out []string
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

func TestReportMatchesPython(t *testing.T) {
	for name := range scenes("") {
		t.Run(name, func(t *testing.T) {
			root := testkit.Workspace(t)
			testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), sampleRules)
			for file, lines := range scenes(root)[name] {
				testkit.Put(t, filepath.Join(root, "logs", file), strings.Join(lines, "\n")+"\n")
			}
			want := testkit.Answers[[]answer](t, "report-"+name, suggestOracle, map[string]any{"root": root})
			for i, asJSON := range []bool{false, true} {
				conf, _ := config.Load(root)
				var out bytes.Buffer
				code, err := Report(&out, conf, root, asJSON)
				if err != nil {
					t.Fatal(err)
				}
				inner, _ := json.Marshal(root)
				text := strings.ReplaceAll(strings.ReplaceAll(out.String(), string(inner[1:len(inner)-1]), "<root>"), root, "<root>")
				if got := (answer{code, text}); got != want[i] {
					t.Errorf("json=%v\nGo:\n%d %s\nPython:\n%d %s", asJSON, got.Code, got.Stdout, want[i].Code, want[i].Stdout)
				}
			}
		})
	}
}
