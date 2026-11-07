package judge

import (
	"encoding/json"
	"math/rand/v2"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

const (
	parent  = "i0001"
	child   = "i0001-01-01" // 着手済み・ワークツリーあり・フローあり
	waiting = "i0001-02-01" // 未着手
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

func childText(id, phase string, entries string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: false\n  reason: t\ntitle: 子 " + id + "\nrationale: r\n" + entries
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}

// selfRules は自身のレイヤーのルール。文脈（additionalContext）・刻み・ask・when・except を当てる。
const selfRules = `version: 1
deny:
  - id: no-curl
    match: Bash
    regex: '(^|\x00)curl\b'
    message: curl is not used here.
    additionalContext: Use the fetch helper instead.
ask:
  - id: migrations
    match: Write|Edit
    glob: "*/migrations/*"
    message: Migrations are read by a person.
  - id: npm-publish
    match: Bash
    regex: 'npm\s+publish'
    message: Publishing is a person's call.
    additionalContextOnce: Say which version you publish.
allow:
  - id: read-anything
    match: Read|Grep|Glob
    glob: "*"
  - id: ls
    match: Bash
    regex: '^(ls|pwd|echo)\b'
    additionalContext: listing is fine
    every: 2
  - id: src-writes
    match: Write|Edit|MultiEdit
    glob: "*/src/*"
    except:
      - glob: "*/src/secret*"
`

// workspace は判定の材料を一通り置いたワークスペース。共通レイヤーと sh はリポジトリのものを写し、自身のレイヤーに
// selfRules を置く。チケットは親 1 本と、着手済みの子（ワークツリーとフローあり）・未着手の子・閉じた子。
func workspace(t *testing.T) string {
	t.Helper()
	repo := testkit.RepoRoot(t)
	root := testkit.Workspace(t, parent, "direct-a")
	copyDir(t, filepath.Join(repo, ".ccnavi", "config", "common"), filepath.Join(root, ".ccnavi", "config", "common"))
	copyDir(t, filepath.Join(repo, ".ccnavi", "scripts"), filepath.Join(root, ".ccnavi", "scripts"))
	testkit.Put(t, filepath.Join(root, config.DefaultProjectHome, filepath.FromSlash(config.LayerConfigDir), "rules.yml"), selfRules)
	testkit.Put(t, filepath.Join(root, ".claude", "settings.json"), `{"hooks": {}}`+"\n")
	testkit.Put(t, filepath.Join(root, "CLAUDE.md"), "# x\n")
	approved := testkit.ApprovedDir(root, parent)
	started := `started_at: "2026-01-02T00:00:00+0900"`
	done := `completed_at: "2026-01-03T00:00:00+0900"`
	research := "allow:\n  - match: Write|Edit\n    glob: \"wip/research/*\"\n" +
		"deny:\n  - match: Write|Edit\n    glob: \"wip/research/private*\"\nask:\n  - match: Write|Edit\n    glob: \"wip/research/ask*\"\n"
	design := "allow:\n  - match: Write|Edit\n    glob: \"wip/design/*\"\n"
	put := func(state, id, text string) { testkit.Put(t, filepath.Join(approved, state, id+".md"), text) }
	put("doing", parent, parentText)
	put("doing", child, childText(child, "1", research, started))
	put("doing", waiting, childText(waiting, "2", design))
	put("done", "i0001-01-03", childText("i0001-01-03", "1", research, started, done))
	testkit.Put(t, filepath.Join(approved, "flows", child+".yml"), "id: wf-1\nname: x\n")
	testkit.Commit(t, testkit.Worktree(root, parent), "approve")
	testkit.AddWorktree(t, root, child, parent)
	return root
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

// call は 1 件の payload。cwd などの {ROOT} は回す側が置き換える。
func call(tool, cwd string, input map[string]any, extra ...string) string {
	p := map[string]any{"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "s1",
		"permission_mode": "default", "tool_input": input}
	if cwd != "" {
		p["cwd"] = cwd
	}
	for i := 0; i+1 < len(extra); i += 2 {
		p[extra[i]] = extra[i+1]
	}
	data, _ := json.Marshal(p)
	return string(data)
}

var baseEnv = map[string]string{"CCNAVI_LEARNING": "disable"}

func with(kv ...string) map[string]string {
	out := map[string]string{"CCNAVI_LEARNING": "disable"}
	for i := 0; i+1 < len(kv); i += 2 {
		out[kv[i]] = kv[i+1]
	}
	return out
}

// handCases は判定の経路を一通り通す表。
func handCases() []judgeCase {
	const (
		ws        = rootMark
		parentWT  = rootMark + "/.claude/worktrees/" + parent
		childWT   = rootMark + "/.claude/worktrees/" + child
		approvedP = parentWT + "/.ccnavi/approved"
	)
	var out []judgeCase
	n := 0
	add := func(name, payload string, envs map[string]string) {
		n++
		var p map[string]any
		_ = json.Unmarshal([]byte(payload), &p)
		p["tool_use_id"] = "h" + string(rune('a'+n%26)) + strings.Repeat("x", n/26)
		data, _ := json.Marshal(p)
		out = append(out, judgeCase{Name: name, Payload: string(data), Env: envs})
	}
	bash := func(cmd, cwd string, extra ...string) string {
		return call(config.ToolBash, cwd, map[string]any{"command": cmd}, extra...)
	}
	write := func(path, cwd string, extra ...string) string {
		return call(config.ToolWrite, cwd, map[string]any{"file_path": path, "content": "x"}, extra...)
	}
	commands := []string{
		"ls", "ls -la", "echo hi", "pwd", "git status", "git push origin main", "sh .ccnavi/scripts/ccnavi-git.sh status",
		"sh .ccnavi/scripts/ccnavi-git.sh push", "CCNAVI_WORKSPACE=/x sh .ccnavi/scripts/ccnavi-git.sh push",
		"curl https://example.com", "env curl x", "sudo -u me cat /etc/hosts", "sh -c 'curl x'", "npm publish",
		"# only a comment", "", "   ", "cat .env", "rm -rf .ccnavi/config", "echo x > .ccnavi/config/common/rules.yml",
		"echo x > CLAUDE.md", "echo x > notes.txt", "cd .claude && echo x > settings.json", "tee README.md < x",
		"{git,push,origin,main}", "c=git; $c push origin main", "echo `date`", "echo $(date)", "ls /work/ccnavi/{src,tests}",
		"echo 'unterminated", "echo \"x" + strings.Repeat(" >", 60), "grep 'git push' notes.md", "echo \"git push\"",
		"sh .ccnavi/scripts/ccnavi-ticket.sh start " + child, "sh .ccnavi/scripts/ccnavi-review.sh decide " + child,
		"env sh .ccnavi/scripts/ccnavi-ticket.sh start " + child, "sh .ccnavi/scripts/ccnavi-user-prune.sh",
		"ccnavi --prune", "ccnavi --prune --preview", "python -m ccnavi user approve " + parent,
		"sh .ccnavi/scripts/ccnavi-git.sh rm .ccnavi/config/common/rules.yml", "sed -i s/a/b/ x", "cat <<EOF\nx\nEOF",
		"sh .ccnavi/scripts/ccnavi-ticket.sh integrate " + child, "sh .ccnavi/scripts/ccnavi-review.sh request " + child,
		"echo x > wip/research/a.md", "cp a .claude/worktrees/" + parent + "/src/x", "git -C . push",
	}
	for _, cmd := range commands {
		add("bash "+cmd, bash(cmd, ws), baseEnv)
	}
	// サブエージェント・子のワークツリー・親のワークツリーから。
	for _, cmd := range []string{
		"ls", "sh .ccnavi/scripts/ccnavi-ticket.sh start " + child, "env sh .ccnavi/scripts/ccnavi-ticket.sh done " + child,
		"echo x > ../" + parent + "/src/a.py", "sh .ccnavi/scripts/ccnavi-git.sh commit -m x", "sh .ccnavi/scripts/ccnavi-git.sh push",
		"echo x > " + parentWT + "/a.txt", "python -c 'open(\"" + parentWT + "/x\",\"w\")'",
	} {
		add("agent bash "+cmd, bash(cmd, childWT, "agent_id", "ag1", "agent_type", "general-purpose"), baseEnv)
		add("child bash "+cmd, bash(cmd, childWT), baseEnv)
		add("parent bash "+cmd, bash(cmd, parentWT), baseEnv)
	}
	// Write・Edit の行き先。
	for _, path := range []string{
		ws + "/notes.md", ws + "/src/app.py", ws + "/src/secret.py", ws + "/db/migrations/1.sql", ws + "/.env",
		ws + "/.ccnavi/config/common/rules.yml", ws + "/.claude/settings.json", ws + "/CLAUDE.md",
		childWT + "/wip/research/a.md", childWT + "/wip/research/private.md", childWT + "/wip/research/ask.md",
		childWT + "/wip/design/a.md", childWT + "/src/x.py", childWT + "/wip/proposals/todo/i0002.md",
		parentWT + "/wip/a.md", parentWT + "/src/a.py", parentWT + "/wip/proposals/todo/i0002.md",
		approvedP + "/doing/" + child + ".md", approvedP + "/flows/" + child + ".yml", approvedP + "/flows/" + waiting + ".yml",
		rootMark + "/.claude/worktrees/I0001-01-01/wip/research/b.md", "/elsewhere/x.md", "relative.md",
	} {
		add("write "+path, write(path, ws), baseEnv)
		add("agent write "+path, write(path, childWT, "agent_id", "ag1"), baseEnv)
		add("edit "+path, call(config.ToolEdit, ws, map[string]any{"file_path": path, "old_string": "a", "new_string": "b"}), baseEnv)
	}
	add("notebook", call(config.ToolNotebookEdit, ws, map[string]any{"notebook_path": childWT + "/wip/research/n.ipynb"}), baseEnv)
	add("multiedit", call(config.ToolMultiEdit, ws, map[string]any{"file_path": ws + "/src/a.py", "edits": []any{}}), baseEnv)
	for _, path := range []string{ws + "/.env", ws + "/src/a.py", "/etc/hosts"} {
		add("read "+path, call(config.ToolRead, ws, map[string]any{"file_path": path}), baseEnv)
	}
	add("grep", call(config.ToolGrep, ws, map[string]any{"pattern": "x"}), baseEnv)
	add("glob", call(config.ToolGlob, ws, map[string]any{"pattern": "*.go", "path": ws + "/src"}), baseEnv)
	add("webfetch", call(config.ToolWebFetch, ws, map[string]any{"url": "https://example.com"}), baseEnv)
	add("unknown", call("mcp__x__y", ws, map[string]any{"q": "x"}), baseEnv)
	for _, cmd := range []string{"Get-ChildItem", "Remove-Item .ccnavi/config -Recurse", "git push", "echo x" + strings.Repeat(" > a", 60)} {
		add("pwsh "+cmd, call(config.ToolPowerShell, ws, map[string]any{"command": cmd}), baseEnv)
	}
	// サブエージェントの起動。
	for _, description := range []string{"d", child + " の調査", waiting + " を始める", "direct-a の作業", parent + " で作業する"} {
		input := map[string]any{"description": description, "prompt": "直す"}
		add("agent "+description, call(config.ToolAgent, ws, input), baseEnv)
		add("task "+description, call(config.ToolTask, parentWT, input), baseEnv)
	}
	add("ask", call(config.ToolAskUserQuestion, ws, map[string]any{"questions": []any{map[string]any{"question": "どちらにするか"}}},
		"transcript_path", "/nonexistent/t.jsonl"), baseEnv)
	add("ask agent", call(config.ToolAskUserQuestion, ws, map[string]any{"questions": []any{}}, "agent_id", "a"), baseEnv)
	// 権限モードとモード・切り替え。
	for _, pm := range []string{"default", "auto", "acceptEdits", "plan", "dontAsk", "bypassPermissions", "weird", ""} {
		for _, cmd := range []string{"make build", "echo 'x"} {
			add("pm "+pm+" "+cmd, bash(cmd, ws, "permission_mode", pm), baseEnv)
			add("pm unwatched "+pm+" "+cmd, bash(cmd, ws, "permission_mode", pm), with("CCNAVI_GUARD_UNWATCHED", "disable"))
		}
	}
	envs := []map[string]string{
		with("CCNAVI_MODE", "dry-run"), with("CCNAVI_GUARD_CORE_FILES", "disable"), with("CCNAVI_TICKET_CONTROL", "disable"),
		with("CCNAVI_GUARD_TICKET_APPROVAL", "disable"), with("CCNAVI_MODE", "dry-run", "CCNAVI_TICKET_CONTROL", "disable"),
		with("CLAUDE_CODE_SESSION_ATTENDED", "0"), with("CCNAVI_DENY_REPEAT", "1", "CCNAVI_FAIL_STREAK_COUNT", "1"),
		with("CCNAVI_GUARD_CORE_FILES", "DryRun"),
	}
	for _, e := range envs {
		for _, cmd := range []string{"git push", "make build", "echo x > .ccnavi/config/common/rules.yml", "{a,b}",
			"sh .ccnavi/scripts/ccnavi-ticket.sh integrate " + child, "sh .ccnavi/scripts/ccnavi-review.sh decide " + child, "npm publish"} {
			add("env bash "+cmd, bash(cmd, ws), e)
		}
		add("env write", write(childWT+"/src/x.py", ws), e)
		add("env write rules", write(ws+"/.ccnavi/config/common/rules.yml", ws), e)
	}
	// 同じ拒否の繰り返しと、ターン分離のゲートの続き（A のあとの B）。
	for range 4 {
		add("repeat", bash("git push origin main", ws), baseEnv)
	}
	add("no subject", call(config.ToolWrite, ws, map[string]any{"content": "x"}), baseEnv)
	return out
}

// randomCases は語を乱数で組んだコマンド。読みの分かれる形（引用・置換・リダイレクト・実行役）を混ぜる。
func randomCases(n int) []judgeCase {
	words := []string{
		"git", "push", "origin", "main", "ls", "echo", "cat", "rm", "-rf", "sh", "-c", "env", "sudo", "xargs", "cd", "..",
		".ccnavi/config/common/rules.yml", ".claude/settings.json", "CLAUDE.md", ".env", "notes.txt", "src/a.py",
		"'git push'", "\"$(git push)\"", "`ls`", "$x", "x=git", ">", ">>", "|", "&&", ";", "||", "2>&1", "<", "#", "{a,b}",
		"*", "?", "~", "curl", "npm", "publish", "sed", "-i", "<<EOF", "tee", "cp", "mv", "python", "-m", "ccnavi",
		"user", "approve", "--prune", ".ccnavi/scripts/ccnavi-git.sh", ".ccnavi/scripts/ccnavi-ticket.sh", "start",
		child, "CCNAVI_WORKSPACE=x", "'", "\"", "\\", "(", ")", "$(", "ccnavi-review.sh", "decide", "--yes",
	}
	r := rand.New(rand.NewPCG(1, 2))
	out := make([]judgeCase, 0, n)
	for i := range n {
		k := 1 + r.IntN(8)
		parts := make([]string, k)
		for j := range parts {
			parts[j] = words[r.IntN(len(words))]
		}
		cwd := rootMark
		var extra []string
		switch r.IntN(4) {
		case 0:
			cwd = rootMark + "/.claude/worktrees/" + child
		case 1:
			extra = []string{"agent_id", "ag"}
		}
		payload := call(config.ToolBash, cwd, map[string]any{"command": strings.Join(parts, " ")},
			append(extra, "tool_use_id", "r"+strings.Repeat("0", i%7)+string(rune('a'+i%26)))...)
		out = append(out, judgeCase{Name: "random", Payload: payload, Env: baseEnv})
	}
	return out
}

// 決めた payload と環境で、標準出力・終了コード・標準エラー・記録の 1 行が見本と同じになる。
func TestBeforeMatchesGolden(t *testing.T) {
	if testing.Short() {
		t.Skip("大きな表は -short では回さない")
	}
	checkCases(t, workspace, slices.Concat(handCases(), randomCases(400)))
}

// 表から集めた payload（testdata/captured.jsonl）でも見本と同じになる。
func TestBeforeMatchesGoldenOnCapturedCalls(t *testing.T) {
	if testing.Short() {
		t.Skip("大きな表は -short では回さない")
	}
	checkCases(t, workspace, readCases(t, "captured.jsonl"))
}
