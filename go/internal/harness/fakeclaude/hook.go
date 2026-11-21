package fakeclaude

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"ccnavi/internal/infra/env"
)

// RecordHook は契約の場面で使う hook の本体（`teststub claude-hook <記録の置き場> <目印>`）。
//
// 受けた JSON と環境の名前を <記録の置き場>/log.jsonl に足す。目印が A の hook は、tool_input の目印（deny-me・ask-me・exit2-me・
// block-post・ctx-me）に合わせて止め方を変え、SessionStart と PostToolUse に文脈を足し、最初の Stop を 1 度だけ差し戻す。
// UserPromptSubmit は 1 秒待つ（2 本の hook が並行に起きるかを見る）。
func RecordHook(argv []string, stdin io.Reader, stdout, stderr io.Writer, environment []string) int {
	if len(argv) < 1 {
		fmt.Fprintln(stderr, "使い方: claude-hook <記録の置き場> [目印]")
		return 2
	}
	rec, tag := argv[0], ""
	if len(argv) > 1 {
		tag = argv[1]
	}
	raw, _ := io.ReadAll(stdin)
	t0 := seconds(time.Now())
	payload, err := decode(string(raw))
	p := asObject(payload)
	if err != nil || p == nil {
		p = object{"_unparsed": string(raw)}
	}
	event := asString(p["hook_event_name"])
	if event == "UserPromptSubmit" {
		time.Sleep(time.Second) // 2 本の hook が並行に起きるかを見る
	}
	vars := environ(environment)
	var names []string
	for _, kv := range vars {
		if n, _, _ := strings.Cut(kv, "="); strings.HasPrefix(n, "CLAUDE") {
			names = append(names, n)
		}
	}
	sort.Strings(names)
	if names == nil {
		names = []string{}
	}
	cwd, _ := os.Getwd()
	line := object{
		"tag": tag, "payload": p, "t0": t0, "t1": seconds(time.Now()),
		"env_names": names, "project_dir": optional(vars, env.ClaudeProjectDir),
		"hook_path_env": optional(vars, HookPathEnv), "hook_cwd": cwd,
	}
	f, err := os.OpenFile(filepath.Join(rec, "log.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	fmt.Fprintln(f, marshal(line))
	_ = f.Close()
	if tag != "A" {
		return 0
	}
	text := marshal(p["tool_input"])
	if p["tool_input"] == nil {
		text = "{}"
	}
	hookOutput := func(k, v string) object { return object{"hookEventName": event, k: v} }
	var out object
	switch {
	case event == "SessionStart":
		out = object{"hookSpecificOutput": hookOutput("additionalContext", "REC-SESSION-CTX")}
	case event == "PreToolUse" && strings.Contains(text, "deny-me"):
		out = object{"hookSpecificOutput": object{"hookEventName": event, "permissionDecision": "deny", "permissionDecisionReason": "REC-DENY"}}
	case event == "PreToolUse" && strings.Contains(text, "ask-me"):
		out = object{"hookSpecificOutput": object{"hookEventName": event, "permissionDecision": "ask", "permissionDecisionReason": "REC-ASK"}}
	case event == "PreToolUse" && strings.Contains(text, "exit2-me"):
		fmt.Fprint(stderr, "REC-EXIT2")
		return 2
	case event == "PostToolUse" && strings.Contains(text, "block-post"):
		out = object{"decision": "block", "reason": "REC-POSTBLOCK"}
	case event == "PostToolUse" && strings.Contains(text, "ctx-me"):
		out = object{"hookSpecificOutput": hookOutput("additionalContext", "REC-POSTCTX")}
	case event == "Stop" && p["stop_hook_active"] != true:
		mark := filepath.Join(rec, "stop-blocked")
		if _, err := os.Stat(mark); err != nil {
			if f, err := os.Create(mark); err == nil {
				_ = f.Close()
			}
			out = object{"decision": "block", "reason": "REC-STOP: Write ツールで stop.txt に done と書いてから終わってください"}
		}
	}
	if out != nil {
		fmt.Fprintln(stdout, marshal(out))
	}
	return 0
}

func seconds(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

// optional は環境の値。無ければ null（JSON）。
func optional(vars environ, name string) any {
	if v, ok := vars.get(name); ok {
		return v
	}
	return nil
}
