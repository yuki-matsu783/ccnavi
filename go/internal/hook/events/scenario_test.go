package events

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

const (
	parent  = "i0001"
	child   = "i0001-01-01" // 着手済み・ワークツリーあり
	waiting = "i0001-02-01" // 未着手
	direct  = "fix-1-docs"  // チケットに結び付かない直接作業
	session = "s1"

	ws       = rootMark
	parentWT = rootMark + "/.claude/worktrees/" + parent
	childWT  = rootMark + "/.claude/worktrees/" + child
	directWT = rootMark + "/.claude/worktrees/" + direct
)

const parentText = "---\nversion: 1\nticket: " + parent + "\nmr: 1\nissue: 1\n" + `phases:
  research:
    kind: work
    title: 調査
    review: false
    scope:
    - wip/research/*
  design:
    kind: work
    title: 設計
    review: true
    scope:
    - wip/design/*
plan:
  - research
  - {type: design, after: [1]}
` + "human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
	"allow:\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
	"started_at: \"2026-01-01T00:00:00+0900\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"

func childText(id, phase, entries string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: false\n  reason: t\ntitle: 子 " + id + "\nrationale: r\n" + entries
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}

// selfRules は自身のレイヤーのルール。保護領域（deny・ask）と、ターンの終わりの振り返り（match: Stop）を置く。
const selfRules = `version: 1
deny:
  - id: no-curl
    match: Bash
    regex: '(^|\x00)curl\b'
    message: curl is not used here.
  - id: secrets
    match: Write|Edit
    glob: "secrets/*"
    message: secrets are kept by hand.
ask:
  - id: migrations
    match: Write|Edit
    glob: "*/migrations/*"
    message: Migrations are read by a person.
allow:
  - id: reflect
    match: Stop
    regex: '.'
    every: 2
    additionalContext: このターンを振り返る。
  - id: ls
    match: Bash
    regex: '^(ls|pwd|echo)\b'
`

// workspace は判定の材料を一通り置き、コミットしたワークスペース。共通レイヤー・sh・作業の決まり（CCNAVI.md）は
// リポジトリのものを写す。チケットは親 1 本と、着手済みの子（ワークツリーあり）・未着手の子。直接作業のワークツリーも
// 1 つ置く。
func workspace(t *testing.T, root string) {
	t.Helper()
	repo := testkit.RepoRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Git(t, root, "commit", "-q", "--allow-empty", "-m", "init")
	testkit.AddWorktree(t, root, parent, "main")
	testkit.AddWorktree(t, root, direct, "main")
	testkit.CopyMessages(t, root)
	copyDir(t, filepath.Join(repo, ".ccnavi", "config", "common"), filepath.Join(root, ".ccnavi", "config", "common"))
	copyDir(t, filepath.Join(repo, ".ccnavi", "scripts"), filepath.Join(root, ".ccnavi", "scripts"))
	data, err := os.ReadFile(filepath.Join(repo, ".ccnavi", "CCNAVI.md"))
	if err != nil {
		t.Fatal(err)
	}
	testkit.Put(t, filepath.Join(root, ".ccnavi", "CCNAVI.md"), string(data))
	testkit.Put(t, filepath.Join(root, config.DefaultProjectHome, filepath.FromSlash(config.LayerConfigDir), "rules.yml"), selfRules)
	testkit.Put(t, filepath.Join(root, ".claude", "settings.json"), `{"hooks": {}}`+"\n")
	testkit.Put(t, filepath.Join(root, "CLAUDE.md"), "# x\n")
	testkit.Put(t, filepath.Join(root, "docs", "a.md"), "---\ntype: guide\ntitle: a\n---\n\n# a\n")
	testkit.Put(t, filepath.Join(root, ".gitignore"), "logs/\n.claude/worktrees/\nremote.git/\n")
	testkit.Commit(t, root, "base")
	approved := testkit.ApprovedDir(root, parent)
	research := "allow:\n  - match: Write|Edit\n    glob: \"wip/research/*\"\n"
	design := "allow:\n  - match: Write|Edit\n    glob: \"wip/design/*\"\n"
	put := func(state, id, text string) { testkit.Put(t, filepath.Join(approved, state, id+".md"), text) }
	put("doing", parent, parentText)
	put("doing", child, childText(child, "1", research, `started_at: "2026-01-02T00:00:00+0900"`, `base_sha: "0000000"`))
	put("doing", waiting, childText(waiting, "2", design))
	testkit.Commit(t, testkit.Worktree(root, parent), "approve")
	testkit.AddWorktree(t, root, child, parent)
}

func copyDir(t *testing.T, from, to string) {
	t.Helper()
	entries, err := os.ReadDir(from)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		data, err := os.ReadFile(filepath.Join(from, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
		testkit.Put(t, filepath.Join(to, e.Name()), string(data))
	}
}

// payload は 1 件の payload。extra は欄の名前と値の組。
func payload(event, cwd string, extra ...any) string {
	p := map[string]any{"hook_event_name": event, "session_id": session, "permission_mode": "default", "cwd": cwd}
	for i := 0; i+1 < len(extra); i += 2 {
		p[extra[i].(string)] = extra[i+1]
	}
	data, _ := json.Marshal(p)
	return string(data)
}

// tool は PreToolUse・PostToolUse・PostToolUseFailure の payload。
func tool(event, name, cwd, id string, input map[string]any, extra ...any) string {
	return payload(event, cwd, append([]any{"tool_name", name, "tool_input", input, "tool_use_id", id}, extra...)...)
}

func text(s string) *string { return &s }

var plain = map[string]string{"CCNAVI_LEARNING": "disable"}

func with(kv ...string) map[string]string {
	out := map[string]string{"CCNAVI_LEARNING": "disable"}
	for i := 0; i+1 < len(kv); i += 2 {
		out[kv[i]] = kv[i+1]
	}
	return out
}

// bashPair は Bash の実行前と実行後。files は実行で起きたこととして、実行後の前に書く。
func bashPair(name, cmd, cwd, id string, envs map[string]string, files map[string]*string) []step {
	in := map[string]any{"command": cmd}
	return []step{
		{Name: name + " pre", Payload: tool("PreToolUse", config.ToolBash, cwd, id, in), Env: envs},
		{Name: name + " post", Payload: tool("PostToolUse", config.ToolBash, cwd, id, in, "tool_response", map[string]any{"stdout": ""}),
			Env: envs, Files: files},
	}
}

// writePair は Write の実行前と実行後。rel はワークスペースからの相対で、実行後の前に書く。
func writePair(name, cwd, rel, id string, envs map[string]string, extra ...any) []step {
	in := map[string]any{"file_path": rootMark + "/" + rel, "content": "x\n"}
	return []step{
		{Name: name + " pre", Payload: tool("PreToolUse", config.ToolWrite, cwd, id, in, extra...), Env: envs},
		{Name: name + " post", Payload: tool("PostToolUse", config.ToolWrite, cwd, id, in, extra...), Env: envs,
			Files: map[string]*string{rel: text("x\n")}},
	}
}

func concat(parts ...[]step) []step {
	var out []step
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

// メインのセッションを、ワークスペースルートで始めから終わりまで回す。
func TestMainSessionMatchesPython(t *testing.T) {
	heavyScenario(t)
	steps := concat(
		[]step{
			{Name: "start", Payload: payload("SessionStart", ws, "source", "startup"), Env: plain},
			{Name: "prompt", Payload: payload("UserPromptSubmit", ws, "prompt", "#12 を直してください"), Env: plain},
		},
		bashPair("ls", "ls", ws, "t1", plain, nil),
		writePair("notes", ws, "notes.md", "t2", plain),
		writePair("secret", ws, "secrets/key.txt", "t3", plain),
		bashPair("echo secret", "echo y > secrets/b.txt", ws, "t4", plain, map[string]*string{"secrets/b.txt": text("y\n")}),
		bashPair("rules", "echo x >> .ccnavi/config/common/rules.yml", ws, "t5", plain,
			map[string]*string{".ccnavi/config/common/rules.yml": text("broken: [\n")}),
		bashPair("claude", "echo x > CLAUDE.md", ws, "t6", plain, map[string]*string{"CLAUDE.md": text("changed\n")}),
		bashPair("curl", "curl https://example.com", ws, "t7", plain, nil),
		bashPair("curl again", "curl https://example.com", ws, "t8", plain, nil),
		bashPair("md", "echo x > docs/b.md", ws, "t9", plain, map[string]*string{"docs/b.md": text("# b\n")}),
		[]step{
			{Name: "failure", Payload: tool("PostToolUseFailure", config.ToolBash, ws, "t10", map[string]any{"command": "make"},
				"error", "exit 2"), Env: plain},
			{Name: "failure again", Payload: tool("PostToolUseFailure", config.ToolBash, ws, "t11", map[string]any{"command": "make"},
				"error", "exit 2"), Env: with("CCNAVI_FAIL_STREAK_COUNT", "2")},
			{Name: "artifact failure", Payload: tool("PostToolUseFailure", "Artifact", ws, "t12", map[string]any{"file_path": "x.html"},
				"error", "x"), Env: plain},
			{Name: "interrupt", Payload: tool("PostToolUseFailure", config.ToolBash, ws, "t13", map[string]any{"command": "sleep 9"},
				"error", "x", "is_interrupt", true), Env: plain},
			{Name: "stop", Payload: payload("Stop", ws, "stop_hook_active", false), Env: with("CCNAVI_DENY_REPEAT", "2")},
			{Name: "notification", Payload: payload("UserPromptSubmit", ws, "prompt", "<task-notification>done</task-notification>"),
				Env: plain, Files: map[string]*string{"secrets/c.txt": text("z\n")}},
			{Name: "stop busy", Payload: payload("Stop", ws, "background_tasks", []any{map[string]any{"id": "b1"}}), Env: plain},
			{Name: "prompt after busy", Payload: payload("UserPromptSubmit", ws, "prompt", "続けて"), Env: plain,
				Files: map[string]*string{".claude/settings.json": nil}},
			{Name: "stop active", Payload: payload("Stop", ws, "stop_hook_active", true), Env: plain},
			{Name: "stop rule", Payload: payload("Stop", ws), Env: plain},
			{Name: "prompt user", Payload: payload("UserPromptSubmit", ws, "prompt", "ありがとう"), Env: plain,
				Files: map[string]*string{"secrets/d.txt": text("w\n")}},
			{Name: "resume", Payload: payload("SessionStart", ws, "source", "resume"), Env: plain},
			{Name: "compact", Payload: payload("SessionStart", ws, "source", "compact"), Env: plain},
			{Name: "unknown event", Payload: payload("Notification", ws, "message", "x"), Env: plain},
		},
		bashPair("dry-run secret", "echo y > secrets/e.txt", ws, "t14", with("CCNAVI_MODE", "dry-run"),
			map[string]*string{"secrets/e.txt": text("y\n")}),
		bashPair("disable", "curl x", ws, "t15", with("CCNAVI_MODE", "disable"), nil),
		[]step{
			{Name: "stop disable", Payload: payload("Stop", ws), Env: with("CCNAVI_MODE", "disable")},
			{Name: "stop guard off", Payload: payload("Stop", ws), Env: with("CCNAVI_GUARD_CORE_FILES", "disable", "CCNAVI_TICKET_CONTROL", "disable")},
		},
	)
	compareScenario(t, workspace, steps)
}

// チケットの作業（子のワークツリー）と、サブエージェントの始まりと終わりを回す。
func TestTicketSessionMatchesPython(t *testing.T) {
	heavyScenario(t)
	childRel := ".claude/worktrees/" + child
	agent := []any{"agent_id", "ag1", "agent_type", "general-purpose"}
	steps := concat(
		[]step{
			{Name: "start", Payload: payload("SessionStart", childWT, "source", "startup"), Env: plain},
			{Name: "prompt", Payload: payload("UserPromptSubmit", childWT, "prompt", "調査を進めて"), Env: plain},
		},
		writePair("in scope", childWT, childRel+"/wip/research/a.md", "t1", plain),
		writePair("out of scope", childWT, childRel+"/src/x.py", "t2", plain),
		[]step{
			{Name: "stop dirty", Payload: payload("Stop", childWT), Env: plain},
			{Name: "prompt again", Payload: payload("UserPromptSubmit", childWT, "prompt", "コミットして"), Env: plain},
		},
		bashPair("commit", "sh .ccnavi/scripts/ccnavi-git.sh commit -m x", childWT, "t3", plain, nil),
		[]step{
			{Name: "stop finish", Payload: payload("Stop", childWT), Env: plain,
				Git: []gitCall{{Dir: childRel, Args: []string{"add", "-A"}}, {Dir: childRel, Args: []string{"commit", "-q", "-m", "research"}}}},
			{Name: "stop finish again", Payload: payload("Stop", childWT), Env: plain},
			{Name: "subagent start", Payload: payload("SubagentStart", childWT, agent...), Env: plain},
			{Name: "agent launch", Payload: tool("PreToolUse", config.ToolAgent, childWT, "t4",
				map[string]any{"description": child + " の調査", "prompt": "調べる"}), Env: plain},
		},
		writePair("agent write", childWT, childRel+"/wip/research/b.md", "t5", plain, agent...),
		writePair("agent out", childWT, childRel+"/docs/c.md", "t6", plain, agent...),
		[]step{
			{Name: "subagent stop", Payload: payload("SubagentStop", childWT, agent...), Env: plain},
			{Name: "subagent stop again", Payload: payload("SubagentStop", childWT, append(agent, "stop_hook_active", true)...), Env: plain},
			{Name: "agent returned", Payload: tool("PostToolUse", config.ToolAgent, childWT, "t4",
				map[string]any{"description": child + " の調査", "prompt": "調べる"}, "tool_response", map[string]any{"agentId": "ag1"}), Env: plain},
			{Name: "stop parent", Payload: payload("Stop", parentWT), Env: plain},
			{Name: "stop parent again", Payload: payload("Stop", parentWT), Env: plain},
			{Name: "prompt parent", Payload: payload("UserPromptSubmit", parentWT, "prompt", "次へ"), Env: plain},
			{Name: "stop no tickets", Payload: payload("Stop", childWT), Env: with("CCNAVI_TICKET_CONTROL", "disable")},
		},
		writePair("dry-run out", childWT, childRel+"/src/y.py", "t7", with("CCNAVI_MODE", "dry-run")),
		writePair("restore", childWT, childRel+"/src/z.py", "t8", with("CCNAVI_RESTORE_IF_DENY", "enable")),
	)
	compareScenario(t, workspace, steps)
}

// 直接作業のワークツリーで、integrate まで進んでいない Stop を回す。
func TestDirectWorkMatchesPython(t *testing.T) {
	heavyScenario(t)
	rel := ".claude/worktrees/" + direct
	steps := concat(
		[]step{
			{Name: "start", Payload: payload("SessionStart", directWT, "source", "startup"), Env: plain,
				Git: []gitCall{{Dir: ".", Args: []string{"init", "-q", "--bare", "remote.git"}},
					{Dir: ".", Args: []string{"remote", "add", "origin", "remote.git"}},
					{Dir: ".", Args: []string{"push", "-q", "origin", "main"}},
					{Dir: ".", Args: []string{"remote", "set-head", "origin", "main"}}}},
			{Name: "prompt", Payload: payload("UserPromptSubmit", directWT, "prompt", "直して"), Env: plain},
		},
		writePair("write", directWT, rel+"/notes.md", "t1", plain),
		[]step{
			{Name: "stop dirty", Payload: payload("Stop", directWT), Env: plain},
			{Name: "stop committed", Payload: payload("Stop", directWT), Env: plain,
				Git: []gitCall{{Dir: rel, Args: []string{"add", "-A"}}, {Dir: rel, Args: []string{"commit", "-q", "-m", "work"}}}},
			{Name: "stop again", Payload: payload("Stop", directWT), Env: plain},
			{Name: "agent stop", Payload: payload("Stop", directWT, "agent_id", "a1"), Env: plain},
			{Name: "prompt next", Payload: payload("UserPromptSubmit", directWT, "prompt", "次"), Env: plain},
		},
		writePair("write more", directWT, rel+"/more.md", "t2", plain),
		[]step{
			{Name: "stop more", Payload: payload("Stop", directWT), Env: plain,
				Git: []gitCall{{Dir: rel, Args: []string{"add", "-A"}}, {Dir: rel, Args: []string{"commit", "-q", "-m", "more"}}}},
			{Name: "stop root dirty", Payload: payload("Stop", ws), Env: plain, Files: map[string]*string{"notes.txt": text("x")}},
			{Name: "prompt blocked", Payload: payload("UserPromptSubmit", directWT, "prompt", "もう一つ"), Env: plain},
			{Name: "stop blocked", Payload: payload("Stop", directWT), Env: plain,
				Files: map[string]*string{rel + "/third.md": text("3\n"), "secrets/k.txt": text("k\n")},
				Git:   []gitCall{{Dir: rel, Args: []string{"add", "-A"}}, {Dir: rel, Args: []string{"commit", "-q", "-m", "third"}}}},
			{Name: "prompt report", Payload: payload("UserPromptSubmit", directWT, "prompt", "片付けた"), Env: plain,
				Files: map[string]*string{"notes.txt": nil}},
			{Name: "stop over report", Payload: payload("Stop", directWT), Env: plain,
				Files: map[string]*string{rel + "/fourth.md": text("4\n"), "secrets/l.txt": text("l\n")},
				Git:   []gitCall{{Dir: rel, Args: []string{"add", "-A"}}, {Dir: rel, Args: []string{"commit", "-q", "-m", "fourth"}}}},
		},
	)
	compareScenario(t, workspace, steps)
}

// Stop の AI 判定の判定役を登録したセッション。提案の操作実績・未送信の提案・計画・再開の疑いを回す。
func TestStopReviewMatchesPython(t *testing.T) {
	heavyScenario(t)
	review := with("CCNAVI_STOP_REVIEW_AGENT", "1", "CCNAVI_STOP_REVIEW", "enable")
	transcript := ".claude/t.jsonl"
	line := func(uuid string) *string {
		return text(`{"type":"user","uuid":"u0"}` + "\n" + `{"type":"assistant","uuid":"` + uuid + `"}` + "\n")
	}
	tp := rootMark + "/" + transcript
	parentRel := ".claude/worktrees/" + parent
	steps := concat(
		[]step{
			{Name: "start", Payload: payload("SessionStart", parentWT, "source", "startup"), Env: review},
			{Name: "prompt", Payload: payload("UserPromptSubmit", parentWT, "prompt", "!34 と #12 を見て提案して"), Env: review},
		},
		bashPair("propose", "sh .ccnavi/scripts/ccnavi-ticket.sh approval-request i0002", parentWT, "t1", review,
			map[string]*string{"logs/state/stopops-result/r1.kv": text("id=i0002\nroute=failed_before\nstarted={NOW}\nended={NOW}\nrc=1\n")}),
		writePair("todo", parentWT, parentRel+"/wip/proposals/todo/i0003.md", "t2", review),
		[]step{
			{Name: "stop no key", Payload: payload("Stop", parentWT), Env: review},
			{Name: "stop", Payload: payload("Stop", parentWT, "transcript_path", tp), Env: review,
				Files: map[string]*string{transcript: line("a1")}},
			{Name: "stop active", Payload: payload("Stop", parentWT, "transcript_path", tp, "stop_hook_active", true), Env: review,
				Files: map[string]*string{transcript: line("a2")}},
			{Name: "stop child", Payload: payload("Stop", childWT, "transcript_path", tp), Env: review,
				Files: map[string]*string{transcript: line("a3")}},
			{Name: "stop dry-run", Payload: payload("Stop", ws, "transcript_path", tp), Env: with("CCNAVI_STOP_REVIEW_AGENT", "1"),
				Files: map[string]*string{transcript: line("a4")}},
			{Name: "stop disabled", Payload: payload("Stop", ws, "transcript_path", tp), Env: with("CCNAVI_STOP_REVIEW_AGENT", "1", "CCNAVI_MODE", "disable"),
				Files: map[string]*string{transcript: line("a5")}},
			{Name: "notification", Payload: payload("UserPromptSubmit", ws, "prompt", "<task-notification>x"), Env: review},
			{Name: "stop sent back", Payload: payload("Stop", childWT, "transcript_path", tp, "stop_hook_active", true), Env: review,
				Files: map[string]*string{transcript: line("a7"), "logs/state/stopreview-attempt-x1.json": text(
					`{"attempt_id": "x1", "key": "s1:a3", "created": {NOW}, "state": "sent_back", "sent_at": {NOW}, "original": ""}`)}},
			{Name: "agent", Payload: payload("Stop", ws, "transcript_path", tp, "agent_id", "a1"), Env: review,
				Files: map[string]*string{transcript: line("a6")}},
		},
	)
	compareScenario(t, workspace, steps)
}
