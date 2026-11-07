package diagnose

import (
	"bytes"
	"encoding/json"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// diagnoseOracle は Python の診断（entry/diagnose）を 1 件ずつ回し、終了コード・標準出力・標準エラーを返す。
// 出力のワークスペースルートは <root> に、ボードの generated_at は固定の値にする（normalize と同じ）。
const diagnoseOracle = `
import io, json, re, sys
from ccnavi.infra import settings
from ccnavi.entry import diagnose
c = json.load(sys.stdin)
root = c["root"]
def norm(text):
    text = text.replace(json.dumps(root)[1:-1], "<root>").replace(root, "<root>")
    return re.sub(r'"generated_at": "[^"]*"', '"generated_at": "-"', text)
out = []
for case in c["cases"]:
    conf, _ = settings.load(root)
    o, e = io.StringIO(), io.StringIO()
    kind = case["kind"]
    call = diagnose.Call(cwd=case.get("cwd", ""), permission_mode=case.get("pm", ""), agent_id=case.get("agent", ""))
    if kind == "test":
        code = diagnose.test(o, e, conf, root, case["tool"], case["subject"], call)
    elif kind == "test_json":
        code = diagnose.test_json(o, e, conf, root, case["tool"], case["subject"], call)
    elif kind == "expect":
        code = diagnose.test_expectations(o, e, conf, root, case["path"], case.get("json", False))
    elif kind == "explain":
        code = diagnose.explain(o, e, conf, root)
    else:
        code = diagnose.explain_json(o, e, conf, root, case.get("mode", ""), case.get("tc", ""))
    out.append({"code": code, "stdout": norm(o.getvalue()), "stderr": norm(e.getvalue())})
print(json.dumps(out, ensure_ascii=False))
`

type diagCase struct {
	Kind    string `json:"kind"`
	Tool    string `json:"tool,omitempty"`
	Subject string `json:"subject,omitempty"`
	Cwd     string `json:"cwd,omitempty"`
	PM      string `json:"pm,omitempty"`
	Agent   string `json:"agent,omitempty"`
	Path    string `json:"path,omitempty"`
	JSON    bool   `json:"json,omitempty"`
	Mode    string `json:"mode,omitempty"`
	TC      string `json:"tc,omitempty"`
}

type diagAnswer struct {
	Code   int    `json:"code"`
	Stdout string `json:"stdout"`
	Stderr string `json:"stderr"`
}

var generatedAt = regexp.MustCompile(`"generated_at": "[^"]*"`)

func normalize(root, text string) string {
	inner, _ := json.Marshal(root)
	text = strings.ReplaceAll(text, string(inner[1:len(inner)-1]), "<root>")
	text = strings.ReplaceAll(text, root, "<root>")
	return generatedAt.ReplaceAllString(text, `"generated_at": "-"`)
}

const rootMark = "{ROOT}"

func (c diagCase) at(root string) diagCase {
	c.Subject = strings.ReplaceAll(c.Subject, rootMark, root)
	c.Cwd = strings.ReplaceAll(c.Cwd, rootMark, root)
	c.Path = strings.ReplaceAll(c.Path, rootMark, root)
	return c
}

func runGo(t *testing.T, root string, c diagCase) diagAnswer {
	t.Helper()
	conf, _ := config.Load(root)
	var o, e bytes.Buffer
	call := Call{Cwd: c.Cwd, PermissionMode: c.PM, AgentID: c.Agent}
	var code int
	var err error
	switch c.Kind {
	case "test", "test_json":
		code, err = Test(&o, &e, conf, root, c.Tool, c.Subject, call, c.Kind == "test_json")
	case "expect":
		code, err = TestExpectations(&o, &e, conf, root, c.Path, c.JSON)
	case "explain":
		code, err = Explain(&o, &e, conf, root)
	default:
		code, err = ExplainJSON(&o, &e, conf, root, c.Mode, c.TC)
	}
	if err != nil {
		t.Fatalf("%+v: %v", c, err)
	}
	return diagAnswer{code, normalize(root, o.String()), normalize(root, e.String())}
}

// compare は cases を Python と Go で回して、1 件ずつ突き合わせる。
func compare(t *testing.T, name, root string, cases []diagCase) {
	t.Helper()
	sent := make([]diagCase, len(cases))
	for i, c := range cases {
		sent[i] = c.at(root)
	}
	want := testkit.Answers[[]diagAnswer](t, name, diagnoseOracle, map[string]any{"root": root, "cases": sent})
	if len(want) != len(cases) {
		t.Fatalf("答えの数 %d が cases の数 %d と違う", len(want), len(cases))
	}
	for i, c := range sent {
		got := runGo(t, root, c)
		if got != want[i] {
			t.Errorf("%s %s %q:\nGo:\n%d\n%s\n%s\nPython:\n%d\n%s\n%s", c.Kind, c.Tool, cases[i].Subject,
				got.Code, got.Stdout, got.Stderr, want[i].Code, want[i].Stdout, want[i].Stderr)
		}
	}
}

const sampleRules = `version: 1
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: push しません。ユーザに頼んでください。
  - id: secrets
    match: Write|Edit
    when:
      any:
        - glob: "*.pem"
        - all:
            - glob: "*/secrets/*"
            - not:
                glob: "*.md"
    message: 秘密は書かない
ask:
  - id: rm
    match: Bash
    regex: '\brm\b'
    except:
      - glob: "*rm -i*"
    message: 消す前に確認
    additionalContext: 消す対象を確かめる
allow:
  - id: listing
    match: Bash
    regex: '\bls\b'
  - id: docs
    match: Write
    glob: "*/docs/*"
  - id: stop-every
    match: Stop
    glob: "*"
    every: 3
`

const sampleExpectations = `deny:
  - tool: Bash
    subject: git push origin main
    why: push はユーザ
  - tool: Bash
    subject: ls
    why: 食い違う例
ask:
  - tool: Bash
    subject: rm -rf build
    why: 消す前
allow:
  - tool: Write
    subject: /repo/docs/a.md
    why: 文書は自由
  - tool: TodoWrite
    subject: x
    why: 判定に入らない
`

func rulesWorkspace(t *testing.T) string {
	t.Helper()
	root := testkit.Workspace(t)
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), sampleRules)
	testkit.Put(t, filepath.Join(root, "expect.yml"), sampleExpectations)
	testkit.Put(t, filepath.Join(root, "list.yml"), "- a\n")
	testkit.Put(t, filepath.Join(root, "sub", ".keep"), "")
	return root
}

func TestTryMatchesPython(t *testing.T) {
	root := rulesWorkspace(t)
	var cases []diagCase
	for _, c := range []diagCase{
		{Tool: "Bash", Subject: "git push origin main"},
		{Tool: "Bash", Subject: "ls -la"},
		{Tool: "Bash", Subject: "rm -rf build"},
		{Tool: "Bash", Subject: "rm -i build"},
		{Tool: "Bash", Subject: "env FOO=1 sh -c 'git push'"},
		{Tool: "Bash", Subject: "echo \"$(git push)\""},
		{Tool: "Bash", Subject: "cat x", PM: "auto", Cwd: rootMark + "/sub"},
		{Tool: "Bash", Subject: "cat x", PM: "bypassPermissions", Agent: "a1"},
		{Tool: "Bash", Subject: "cat x", PM: "nope"},
		{Tool: "Bash", Subject: "cat x", Cwd: rootMark + "/missing"},
		{Tool: "Write", Subject: rootMark + "/docs/a.md"},
		{Tool: "Write", Subject: rootMark + "/keys/a.pem"},
		{Tool: "Write", Subject: "relative/x.txt"},
		{Tool: "Read", Subject: rootMark + "/README.md"},
		{Tool: "Stop", Subject: "(stop)"},
		{Tool: "TodoWrite", Subject: "x"},
	} {
		for _, kind := range []string{"test", "test_json"} {
			c.Kind = kind
			cases = append(cases, c)
		}
	}
	compare(t, "try", root, cases)
}

func TestExpectationsMatchPython(t *testing.T) {
	root := rulesWorkspace(t)
	compare(t, "expectations", root, []diagCase{
		{Kind: "expect", Path: rootMark + "/expect.yml"},
		{Kind: "expect", Path: rootMark + "/expect.yml", JSON: true},
		{Kind: "expect", Path: rootMark + "/missing.yml"},
		{Kind: "expect", Path: rootMark + "/list.yml"},
	})
}

func TestStopWithoutRulesMatchesPython(t *testing.T) {
	root := testkit.Workspace(t)
	compare(t, "stop-empty", root, []diagCase{{Kind: "test", Tool: "Stop", Subject: "(stop)"}})
}

// boardWorkspace は親 1 本（research → design）と子 3 枚（着手中・閉じた・承認待ち）と、承認済みの無い提案の親の場面。
func boardWorkspace(t *testing.T) string {
	t.Helper()
	root := testkit.ProposalWorkspace(t, "i0001", "i0001-01-01")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), sampleRules)
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "risks.yml"),
		"version: 1\nlevels:\n  high: 45\nfactors:\n  - id: big\n    points: 25\n    lines_over: 300\n    message: 行数が多い\n"+
			"  - id: ci\n    points: 35\n    glob: .github/**\n    message: CI\n")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "project", "phases.yml"), "version: 2\n")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "project", "rules.yml"),
		"version: 1\nask:\n  - id: curl\n    match: Bash\n    glob: \"*curl *\"\n    message: 外に出る\n")
	parent := testkit.Worktree(root, "i0001")
	approved := filepath.Join(parent, ".ccnavi", "approved")
	testkit.Put(t, filepath.Join(approved, "doing", "i0001.md"),
		testkit.ProposalParent("i0001", []string{"  - research", "  - type: design", "    after: [1]"}))
	testkit.Put(t, filepath.Join(approved, "doing", "i0001-01-01.md"),
		testkit.ProposalChild("i0001-01-01", "i0001", "1", []string{"wip/research/*"},
			`started_at: "2026-01-05T00:00:00+0000"`, `base_sha: "abc"`))
	testkit.Put(t, filepath.Join(approved, "done", "i0001-01-02.md"),
		testkit.ProposalChild("i0001-01-02", "i0001", "1", []string{"wip/research/*"},
			`started_at: "2026-01-05T00:00:00+0000"`, `completed_at: "2026-01-05T00:00:00+0000"`))
	testkit.Put(t, filepath.Join(approved, "phases", "i0001", "i0001-01-02.risk.json"),
		`{"points": 50, "level": "HIGH", "hits": [], "unmeasured": []}`)
	testkit.Put(t, filepath.Join(approved, "phases", "i0001", "1.requested"), `{"at": "x"}`)
	testkit.Put(t, filepath.Join(parent, "wip", "proposals", "todo", "i0001-02-03.md"),
		testkit.ProposalChild("i0001-02-03", "i0001", "2", []string{"wip/design/*"}))
	testkit.Commit(t, parent, "approve")
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0002.md"), testkit.ProposalParent("i0002", nil))
	return root
}

func TestBoardAndExplainMatchPython(t *testing.T) {
	root := boardWorkspace(t)
	compare(t, "board", root, []diagCase{
		{Kind: "board"},
		{Kind: "board", Mode: "nope", TC: "disable"},
		{Kind: "explain"},
	})
}
