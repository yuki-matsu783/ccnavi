package subagent

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/hook/outcome"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/testkit"
)

// サブエージェントの始まりと終わりの一連の呼び出しを、Python 用と Go 用に同じ形で作ったワークスペースへ打ち、
// 応答・終了コード・標準エラー・判定の記録・state に書いた印を突き合わせる。

// step は 1 つの手順。op が write ならワークスペースにファイルを書き、call なら呼ぶ。
type step struct {
	Op   string `json:"op"`
	Path string `json:"path,omitempty"`
	Text string `json:"text,omitempty"`
	// call の中身。kind は start・stop・post（親の PostToolUse が言う知らせ）・error・running・forget。
	Kind      string    `json:"kind,omitempty"`
	Cwd       string    `json:"cwd,omitempty"` // root からの相対
	Agent     string    `json:"agent,omitempty"`
	AgentType string    `json:"agent_type,omitempty"`
	Tools     *[]string `json:"tools,omitempty"`
	Session   string    `json:"session,omitempty"`
	Mode      string    `json:"mode,omitempty"`
	Tool      string    `json:"tool,omitempty"`
	Launched  string    `json:"launched,omitempty"` // tool_response の agentId
	Off       bool      `json:"off,omitempty"`
}

func (s step) payload(root string) string {
	session := s.Session
	if session == "" {
		session = "s1"
	}
	event := map[string]string{"start": "SubagentStart", "stop": "SubagentStop", "post": "PostToolUse",
		"error": "SubagentStop", "running": "Stop", "forget": "SessionStart"}[s.Kind]
	p := map[string]any{"hook_event_name": event, "cwd": filepath.Join(root, s.Cwd), "session_id": session}
	if s.Agent != "" {
		p["agent_id"] = s.Agent
	}
	if s.AgentType != "" {
		p["agent_type"] = s.AgentType
	}
	if s.Tools != nil {
		p["tools"] = *s.Tools
	}
	if s.Tool != "" {
		p["tool_name"] = s.Tool
		p["tool_input"] = map[string]any{}
		p["tool_response"] = map[string]any{}
	}
	if s.Launched != "" {
		p["tool_response"] = map[string]any{"agentId": s.Launched}
	}
	data, _ := json.Marshal(p)
	return string(data)
}

const scenarioOracle = `
import copy, io, json, os, sys
from ccnavi.hook import subagent
from ccnavi.infra import hookio, settings
from ccnavi.records import audit
c = json.load(sys.stdin)
root = c["root"]
out = []
for st, payload_text in zip(c["steps"], c["payloads"]):
    if st["op"] == "write":
        p = os.path.join(root, st["path"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(st["text"])
        continue
    conf, _ = settings.load(root)
    if st.get("off"):
        conf = copy.copy(conf)
        conf.ticket_control = "disable"
    payload = hookio.decode(io.StringIO(payload_text))
    record, so, se = audit.Record(), io.StringIO(), io.StringIO()
    rc, text, running = 0, "", False
    kind = st["kind"]
    if kind == "start":
        rc = subagent.at_start(so, se, conf, root, payload, record)
    elif kind == "stop":
        rc = subagent.at_stop(so, se, st.get("mode") or "enable", conf, root, payload, record)
    elif kind == "post":
        bounced = subagent.ignored_bounce(conf.state, payload)
        unchecked = subagent.unchecked_scope(conf.state, payload)
        outcomes, told = subagent.outcome_notices(conf.state, payload)
        text = "\n\n".join(p for p in (bounced, unchecked, outcomes) if p)
        subagent.confirm_outcomes(conf.state, told)
    elif kind == "error":
        subagent.unchecked_after_error(se, conf, root, payload)
    elif kind == "running":
        running = subagent.running(conf.state, payload.session_id)
    else:
        subagent.forget_running(conf.state, payload.session_id)
    files = {}
    if os.path.isdir(conf.state):
        for name in sorted(os.listdir(conf.state)):
            p = os.path.join(conf.state, name)
            if os.path.isfile(p):
                with open(p, encoding="utf-8", newline="") as f:
                    files[name] = f.read()
    out.append({"rc": rc, "stdout": so.getvalue(), "stderr": se.getvalue(), "text": text, "running": running,
                "decision": record.decision, "reason": record.reason, "enforced": record.enforced, "code": record.code,
                "paths": list(record.paths), "rules": list(record.rules), "detail": record.detail, "files": files})
json.dump(out, sys.stdout, ensure_ascii=False)
`

type result struct {
	RC       int               `json:"rc"`
	Stdout   string            `json:"stdout"`
	Stderr   string            `json:"stderr"`
	Text     string            `json:"text"`
	Running  bool              `json:"running"`
	Decision string            `json:"decision"`
	Reason   string            `json:"reason"`
	Enforced bool              `json:"enforced"`
	Code     string            `json:"code"`
	Paths    []string          `json:"paths"`
	Rules    []string          `json:"rules"`
	Detail   string            `json:"detail"`
	Files    map[string]string `json:"files"`
}

func runScenario(t *testing.T, steps []step, root string) []result {
	t.Helper()
	var out []result
	for _, st := range steps {
		if st.Op == "write" {
			testkit.Put(t, filepath.Join(root, filepath.FromSlash(st.Path)), st.Text)
			continue
		}
		conf := testkit.Settings(t, root)
		if st.Off {
			off := *conf
			off.TicketControl = string(config.Disable)
			conf = &off
		}
		in, err := hookio.Decode(strings.NewReader(st.payload(root)))
		if err != nil {
			t.Fatal(err)
		}
		record := &audit.Record{}
		var so, se bytes.Buffer
		r := result{}
		switch st.Kind {
		case "start":
			r.RC, err = AtStart(&so, &se, conf, root, in, record)
		case "stop":
			mode := config.Mode(st.Mode)
			if mode == "" {
				mode = config.Enable
			}
			r.RC, err = AtStop(&so, &se, mode, conf, root, in, record)
		case "post":
			notices, told := Notices(conf.State, in)
			var parts []string
			for _, p := range []string{IgnoredBounce(conf.State, in), UncheckedScope(conf.State, in), notices} {
				if p != "" {
					parts = append(parts, p)
				}
			}
			r.Text = strings.Join(parts, "\n\n")
			outcome.Confirm(told)
		case "error":
			AfterError(&se, conf, root, in)
		case "running":
			r.Running = Running(conf.State, in.SessionID)
		default:
			ForgetRunning(conf.State, in.SessionID)
		}
		if err != nil {
			t.Fatal(err)
		}
		r.Stdout, r.Stderr = so.String(), se.String()
		r.Decision, r.Reason, r.Enforced, r.Code = string(record.Decision), string(record.Reason), record.Enforced, record.Code
		r.Paths, r.Rules, r.Detail = record.Paths, record.Rules, record.Detail
		r.Files = map[string]string{}
		if names, err := os.ReadDir(conf.State); err == nil {
			for _, e := range names {
				if !e.IsDir() {
					data, _ := os.ReadFile(filepath.Join(conf.State, e.Name()))
					r.Files[e.Name()] = string(data)
				}
			}
		}
		out = append(out, r)
	}
	return out
}

// 時刻は 2 回の実行で揃わないので置き換える（印の開始の秒、差し戻しの時刻、終わり方の記録の時刻と id）。
var (
	startedLine = regexp.MustCompile(`(session [^\\]*\\n)\d{9,}\\n`)
	stampText   = regexp.MustCompile(`\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:?\d\d`)
	atField     = regexp.MustCompile(`\\"at\\": [0-9.e+]+`)
	idStamp     = regexp.MustCompile(`:\d{13}\\"`)
)

func normalized(t *testing.T, r result, root string) string {
	t.Helper()
	if r.Paths == nil {
		r.Paths = []string{}
	}
	if r.Rules == nil {
		r.Rules = []string{}
	}
	data, err := json.MarshalIndent(r, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	for _, p := range []string{root, pathcase.Fold(root)} {
		quoted, _ := json.Marshal(p)
		text = strings.ReplaceAll(text, strings.Trim(string(quoted), `"`), "<root>")
	}
	text = startedLine.ReplaceAllString(text, "${1}<t>\\n")
	text = stampText.ReplaceAllString(text, "<stamp>")
	text = atField.ReplaceAllString(text, `\"at\": <t>`)
	return idStamp.ReplaceAllString(text, `:<ms>\"`)
}

const (
	parent = "i0001"
	child  = "i0001-01-01" // 調査。ワークツリーあり・フローあり
	child2 = "i0001-02-02" // 設計。ワークツリーあり・フローあり・着手後にフローが変わった・範囲が定義と親の外にもある
	child3 = "i0001-03-01" // 設計。着手済みだがワークツリーが無い（範囲を確かめられない）
	direct = "direct-a"
)

const phasesCopy = `phases:
  research:
    kind: work
    title: 調査
    review: false
    agent: Explore
    scope:
    - wip/research/*
  design:
    kind: work
    title: 設計
    review: false
    scope:
    - wip/design/*
plan:
  - research
  - {type: design, after: [1]}
  - {type: design, after: [2]}
`

const workflow = `id: wf-1
name: 調査の手順
version: 1.0.0
nodes:
  - {id: s, type: start, name: 開始, position: {x: 0, y: 0}, data: {}}
  - {id: p1, type: prompt, name: 読む, position: {x: 1, y: 0}, data: {prompt: 既存の振る舞いを読む}}
  - {id: e, type: end, name: 終了, position: {x: 4, y: 0}, data: {}}
connections:
  - {id: c1, from: s, to: p1, fromPort: output, toPort: input}
  - {id: c2, from: p1, to: e, fromPort: output, toPort: input}
`

func parentText() string {
	return "---\nversion: 1\nticket: " + parent + "\nmr: 1\nissue: 1\n" + phasesCopy +
		"human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
		"started_at: \"2026-01-01T00:00:00+0900\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

func childText(id, phase string, globs ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: false\n  reason: t\ntitle: 子 " + id + "\nrationale: r\nallow:\n"
	for _, g := range globs {
		text += "  - match: Write|Edit\n    glob: \"" + g + "\"\n"
	}
	return text + "started_at: \"2026-01-02T00:00:00+0900\"\n---\n\n本文\n"
}

func workspace(t *testing.T) string {
	root := testkit.Workspace(t, parent, direct)
	approved := testkit.ApprovedDir(root, parent)
	put := func(rel, text string) { testkit.Put(t, filepath.Join(approved, rel), text) }
	put("doing/"+parent+".md", parentText())
	put("doing/"+child+".md", childText(child, "1", "wip/research/*"))
	put("doing/"+child2+".md", childText(child2, "2", "wip/design/*", "wip/notes/*", "src/*"))
	put("doing/"+child3+".md", childText(child3, "3", "wip/design/*"))
	put("flows/"+child+".yml", workflow)
	put("flows/"+child2+".yml", workflow)
	put("phases/"+parent+"/"+child2+".flow.json", `{"fingerprint": "sha256:0000000000000000"}`)
	testkit.Put(t, filepath.Join(root, ".claude", "agents", "reader.md"), "---\nname: reader\ntools: Read, Grep\n---\n")
	testkit.Commit(t, testkit.Worktree(root, parent), "approve")
	for _, id := range []string{child, child2} {
		testkit.AddWorktree(t, root, id, parent)
	}
	return root
}

func TestScenarioMatchesPython(t *testing.T) {
	wt := func(name string) string { return filepath.Join(".claude", "worktrees", name) }
	call := func(kind, cwd, agent string) step { return step{Op: "call", Kind: kind, Cwd: cwd, Agent: agent} }
	editor := func(cwd, agent string) step {
		s := call("start", cwd, agent)
		s.AgentType = "general-purpose"
		return s
	}
	writeTools := []string{"Read", "Write"}
	steps := []step{
		editor("", "a1"),
		editor("", "a2"),
		{Op: "call", Kind: "start", Agent: "r1", AgentType: "reader"},
		{Op: "call", Kind: "start", Agent: "r2", AgentType: "Explore"},
		// 親のツリーからの起動（full=false）。どの子の担当か決まらないので、フローは 1 行だけ。
		editor(wt(parent), "p1"),
		editor(wt(child), "c1"),
		{Op: "call", Kind: "start", Cwd: wt(child), Agent: "c1b", AgentType: "general-purpose", Tools: &writeTools},
		// 再開で同じ相手を重ねて案内しない。
		{Op: "call", Kind: "start", Cwd: wt(child), Agent: "c1b", AgentType: "general-purpose", Tools: &writeTools},
		editor(wt(child), "c1x"),
		editor(wt(direct), "d1"),
		editor(wt(child), ""),
		{Op: "call", Kind: "start", Cwd: wt(child), Agent: "off1", Off: true},
		{Op: "call", Kind: "start", Cwd: wt(child), Agent: "other", Session: "s2"},
		call("stop", wt(child), "c1"),
		{Op: "write", Path: wt(child) + "/src/out.py", Text: "x\n"},
		call("stop", wt(child), "c1"),
		call("stop", wt(child), "c1"),
		{Op: "call", Kind: "post", Tool: config.ToolAgent, Launched: "c1"},
		{Op: "write", Path: wt(child2) + "/wip/notes/a.md", Text: "x\n"},
		{Op: "write", Path: wt(child2) + "/src/b.py", Text: "x\n"},
		{Op: "write", Path: wt(child2) + "/wip/design/ok.md", Text: "x\n"},
		{Op: "write", Path: wt(child2) + "/other/x.txt", Text: "x\n"},
		{Op: "call", Kind: "stop", Cwd: wt(child2), Agent: "c2", Mode: string(config.DryRun)},
		call("stop", wt(parent), "p1"),
		{Op: "call", Kind: "post", Tool: config.ToolBash},
		{Op: "call", Kind: "post", Tool: config.ToolBash},
		call("error", wt(child), "e1"),
		call("error", wt(child), "e1"),
		{Op: "call", Kind: "post", Tool: config.ToolTask, Launched: "e1", Agent: "nested"},
		call("error", wt(parent), ""),
		{Op: "call", Kind: "post", Tool: config.ToolRead},
		call("stop", wt(child2), ""),
		call("stop", wt(child2), ""),
		{Op: "call", Kind: "post", Tool: config.ToolAgent, Launched: child2},
		call("running", "", ""),
		{Op: "call", Kind: "running", Session: "s3"},
		call("forget", "", ""),
		call("running", "", ""),
		{Op: "call", Kind: "running", Session: "s2"},
		{Op: "call", Kind: "stop", Cwd: wt(child), Agent: "c1x", Off: true},
		call("stop", "", "a1"),
	}
	pyRoot, goRoot := workspace(t), workspace(t)
	var payloads []string
	for _, st := range steps {
		payloads = append(payloads, st.payload(pyRoot))
	}
	want := testkit.Answers[[]result](t, "", scenarioOracle,
		map[string]any{"root": pyRoot, "steps": steps, "payloads": payloads})
	got := runScenario(t, steps, goRoot)
	calls := 0
	for i, st := range steps {
		if st.Op != "call" {
			continue
		}
		g, w := normalized(t, got[calls], goRoot), normalized(t, want[calls], pyRoot)
		if g != w {
			t.Errorf("手順 %d（%s %s %s）:\nGo:\n%s\nPython:\n%s", i, st.Kind, st.Cwd, st.Agent, g, w)
		}
		calls++
	}
	if len(want) != calls {
		t.Fatalf("Python の答えの数 %d が呼び出しの数 %d と違う", len(want), calls)
	}
	// 見本が覆うべき形を全部通ったか（どれかが空振りしていれば、突き合わせが一致しても意味が無い）。
	all, _ := json.Marshal(got)
	if os.Getenv("SUBAGENT_DUMP") != "" {
		out, _ := json.MarshalIndent(got, "", " ")
		os.WriteFile(os.Getenv("SUBAGENT_DUMP"), out, 0o644)
	}
	for _, mark := range []string{
		ownFlowOnly, "別の編集するサブエージェントが動いています", "ワークスペースルート", "（" + child + "）",
		"定義 設計 の上限の外", "親 " + parent + " の範囲の外", "SUBAGENT_UNATTRIBUTED", "差し戻されたまま終わっています",
		"内部エラーで止まり", "のワークツリーを読めず", "着手後にフローが書き換わった", "スキル候補", "unchecked: " + child3,
	} {
		if !strings.Contains(string(all), strings.Trim(mustJSON(mark), `"`)) {
			t.Errorf("見本が %q を通っていない", mark)
		}
	}
	for _, mark := range []string{`"rc":2`, `"running":true`, `"running":false`} {
		if !strings.Contains(string(all), mark) {
			t.Errorf("見本が %s を通っていない", mark)
		}
	}
}

func mustJSON(s string) string {
	data, _ := json.Marshal(s)
	return string(data)
}
