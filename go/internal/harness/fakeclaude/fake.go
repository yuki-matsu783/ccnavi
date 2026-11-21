package fakeclaude

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"ccnavi/internal/infra/env"
)

const (
	// ScriptEnv は台本の JSON のパスを入れる環境変数。
	ScriptEnv = env.FakeClaudeScript
	// HomeEnv はセッションの記録の置き場（既定は一時置き場の ccnavi-fake-claude）。
	HomeEnv = env.FakeClaudeHome

	hookTimeout        = 60 * time.Second
	subagentDonePrompt = "<task-notification>サブエージェントが終わった</task-notification>"
)

// args は実行器が渡す引数。
type args struct {
	prompt            string
	outputFormat      string
	includeHookEvents bool
	permissionMode    string
	sessionID, resume string
	noPersistence     bool
}

func parseArgs(argv []string, stderr io.Writer) (args, bool) {
	var a args
	fs := flag.NewFlagSet("fake_claude", flag.ContinueOnError)
	fs.SetOutput(stderr)
	fs.StringVar(&a.prompt, "p", "", "prompt")
	fs.StringVar(&a.prompt, "print", "", "prompt")
	fs.StringVar(&a.outputFormat, "output-format", "", "stream-json")
	fs.Bool("verbose", false, "")
	fs.BoolVar(&a.includeHookEvents, "include-hook-events", false, "")
	fs.String("model", "", "")
	fs.String("max-budget-usd", "", "")
	fs.String("setting-sources", "", "")
	fs.StringVar(&a.permissionMode, "permission-mode", "default", "")
	fs.StringVar(&a.sessionID, "session-id", "", "")
	fs.StringVar(&a.resume, "resume", "", "")
	fs.BoolVar(&a.noPersistence, "no-session-persistence", false, "")
	if err := fs.Parse(argv); err != nil || fs.NArg() > 0 { // 知らない引数は終了コード 2（実行器の追加に追従させる）
		return a, false
	}
	switch {
	case !seen(fs, "p", "print"):
		fmt.Fprintln(stderr, "-p が要る")
	case a.outputFormat != "stream-json":
		fmt.Fprintln(stderr, "--output-format は stream-json")
	case a.sessionID != "" && a.resume != "":
		fmt.Fprintln(stderr, "--session-id と --resume は一緒に使えない")
	default:
		return a, true
	}
	return a, false
}

func seen(fs *flag.FlagSet, names ...string) bool {
	found := false
	fs.Visit(func(f *flag.Flag) {
		for _, n := range names {
			found = found || f.Name == n
		}
	})
	return found
}

// session は 1 回の claude -p。
type session struct {
	args       args
	root       string
	id         string
	home       string
	transcript string
	promptID   string
	hooks      map[string][]hookGroup
	env        environ
	out        io.Writer
	lastText   string
	ranAgent   bool
	current    string // いま実行中の呼び出しの ID
}

func newSession(a args, root string, vars environ, out io.Writer) (*session, error) {
	s := &session{args: a, root: root, out: out, env: vars, promptID: uuid4()}
	s.id = a.resume
	if s.id == "" {
		s.id = a.sessionID
	}
	if s.id == "" {
		s.id = uuid4()
	}
	s.home, _ = vars.get(HomeEnv)
	if s.home == "" {
		s.home = filepath.Join(os.TempDir(), "ccnavi-fake-claude")
	}
	if err := os.MkdirAll(s.home, 0o755); err != nil {
		return nil, err
	}
	s.transcript = filepath.Join(s.home, s.id+".jsonl")
	cfg, err := loadSettings(root)
	if err != nil {
		return nil, err
	}
	s.hooks = cfg.Hooks
	// 本物と同じく、settings.json の env を自分の環境に入れる（hook と Bash が受け継ぐ）。
	// ccnavi の hook の命令は、ここで入る CCNAVI_ENTRY_PATH を使う
	for k, v := range cfg.Env {
		s.env = s.env.set(k, fmt.Sprint(v))
	}
	return s, nil
}

func loadSettings(root string) (settings, error) {
	data, err := os.ReadFile(filepath.Join(root, ".claude", "settings.json"))
	if os.IsNotExist(err) {
		return settings{}, nil
	}
	if err != nil {
		return settings{}, err
	}
	var cfg settings
	if err := json.Unmarshal(data, &cfg); err != nil {
		return settings{}, fmt.Errorf(".claude/settings.json が読めない: %v", err)
	}
	return cfg, nil
}

// Run はスタブの本体。argv は claude と同じ引数、environ は `NAME=value` の並び（子の hook と Bash が受け継ぐ）、cwd がワークスペースルート。
// 終了コードを返す。
func Run(argv []string, environment []string, cwd string, stdout, stderr io.Writer) int {
	a, ok := parseArgs(argv, stderr)
	if !ok {
		return 2
	}
	vars := environ(environment)
	path, _ := vars.get(ScriptEnv)
	if path == "" {
		fmt.Fprintf(stderr, "%s に台本の JSON のパスを入れる\n", ScriptEnv)
		return 1
	}
	data, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	plan, err := ParsePlan(string(data))
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	s, err := newSession(a, cwd, vars, stdout)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	s.emit(object{
		"type":           "system",
		"subtype":        "init",
		"cwd":            s.root,
		"tools":          append(append([]string{}, toolNames...), "Agent"),
		"permissionMode": a.permissionMode,
	}, false)
	source := "startup"
	if a.resume != "" {
		source = "resume"
	}
	s.fire(s.base("SessionStart", nil, object{"source": source}))
	s.fire(s.base("UserPromptSubmit", nil, object{"prompt": a.prompt}))
	s.play(plan.Script, nil)
	if s.ranAgent { // 本物はサブエージェントが終わった知らせで、もう一度 UserPromptSubmit を起こす
		s.fire(s.base("UserPromptSubmit", nil, object{"prompt": subagentDonePrompt}))
	}
	stopReason := "end_turn"
	if b := s.stop(false); b != nil {
		s.emit(object{
			"type":               "user",
			"message":            object{"role": "user", "content": []any{object{"type": "text", "text": b.text}}},
			"parent_tool_use_id": nil,
			"isSynthetic":        true,
		}, true)
		s.play(plan.AfterStopBlock, nil)
		if s.stop(true) != nil {
			stopReason = "stop_hook_blocked_twice"
		}
	}
	s.emit(object{
		"type":           "result",
		"subtype":        "success",
		"is_error":       false,
		"result":         s.lastText,
		"stop_reason":    stopReason,
		"total_cost_usd": json.Number("0.0"),
		"num_turns":      1,
	}, false)
	return 0
}

// 出力 ----------------------------------------------------------------------

func (s *session) emit(line object, transcript bool) {
	out := object{"session_id": s.id, "uuid": uuid4()}
	for k, v := range line {
		out[k] = v
	}
	text := marshal(out)
	fmt.Fprintln(s.out, text)
	if transcript && !s.args.noPersistence {
		if f, err := os.OpenFile(s.transcript, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644); err == nil {
			fmt.Fprintln(f, text)
			_ = f.Close()
		}
	}
}

// hook ----------------------------------------------------------------------

// agent は走っているサブエージェント。
type agent struct{ id, kind, call string }

func (s *session) base(event string, ag *agent, extra object) object {
	p := object{
		"session_id":      s.id,
		"transcript_path": s.transcript,
		"cwd":             s.root,
		"hook_event_name": event,
	}
	if event != "SessionStart" {
		p["prompt_id"] = s.promptID
	}
	if event != "SessionStart" && event != "SubagentStart" {
		p["permission_mode"] = s.args.permissionMode
	}
	if ag != nil {
		p["agent_id"], p["agent_type"] = ag.id, ag.kind
	}
	for k, v := range extra {
		p[k] = v
	}
	return p
}

type hookResult struct {
	code   int
	stdout string
	stderr string
}

// fire は payload のイベントに当たる hook を並行に起こし、止めたら blocked を返す（最初の 1 本）。
func (s *session) fire(payload object) *blocked {
	event := asString(payload["hook_event_name"])
	tool, hasTool := payload["tool_name"].(string)
	name := event
	if hasTool && tool != "" {
		name = event + ":" + tool
	}
	if event == "SessionStart" {
		name = event + ":" + asString(payload["source"])
	}
	hooks := matching(s.hooks[event], tool, hasTool && tool != "")
	ids := make([]string, len(hooks))
	for i := range hooks {
		ids[i] = uuid4()
		s.hookLine("hook_started", ids[i], name, event, nil)
	}
	results := make([]hookResult, len(hooks))
	var wg sync.WaitGroup
	for i, h := range hooks {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = s.runHook(h, payload)
		}()
	}
	wg.Wait()
	var first *blocked
	for i, r := range results {
		outcome := "success"
		if r.code != 0 {
			outcome = "error"
		}
		s.hookLine("hook_response", ids[i], name, event, object{
			"output": r.stdout + r.stderr, "stdout": r.stdout, "stderr": r.stderr,
			"exit_code": r.code, "outcome": outcome,
		})
		if b := blocking(event, tool, r.code, r.stdout, r.stderr); first == nil {
			first = b
		}
	}
	return first
}

func (s *session) hookLine(subtype, id, name, event string, extra object) {
	if !s.args.includeHookEvents {
		return
	}
	line := object{"type": "system", "subtype": subtype, "hook_id": id, "hook_name": name, "hook_event": event}
	for k, v := range extra {
		line[k] = v
	}
	s.emit(line, false)
}

func (s *session) runHook(h hook, payload object) hookResult {
	vars := s.env.set(env.ClaudeProjectDir, s.root).set(env.ClaudeEnvFile, filepath.Join(s.home, s.id+".env"))
	// 本物の -p は、外から渡されていなければ sdk-cli を入れる。ccnavi は、この値で非対話かどうかを見分ける
	if _, ok := vars.get(env.ClaudeCodeEntrypoint); !ok {
		vars = vars.set(env.ClaudeCodeEntrypoint, "sdk-cli")
	}
	vars = vars.without(ScriptEnv)
	limit := hookTimeout
	if h.Timeout != nil {
		limit = time.Duration(*h.Timeout * float64(time.Second))
	}
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	cmd := exec.CommandContext(ctx, "sh", "-c", h.Command)
	cmd.Dir = s.root
	cmd.Env = vars
	cmd.Stdin = strings.NewReader(marshal(payload))
	cmd.WaitDelay = 5 * time.Second
	var stdout, stderr strings.Builder
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if ctx.Err() != nil {
		return hookResult{1, "", "hook がタイムアウトした"}
	}
	code := 0
	if err != nil {
		code = 1
		if cmd.ProcessState != nil {
			code = cmd.ProcessState.ExitCode()
		}
	}
	return hookResult{code, stdout.String(), stderr.String()}
}

// 手 ------------------------------------------------------------------------

func (s *session) play(moves []Move, ag *agent) {
	for _, m := range moves {
		switch {
		case m.Say != nil:
			s.lastText = *m.Say
			s.emit(assistant([]any{object{"type": "text", "text": *m.Say}}, ag), true)
		case m.Agent != nil:
			s.runAgent(*m.Agent)
		default:
			in := object{}
			for k, v := range m.Input {
				in[k] = v
			}
			s.useTool(*m.Tool, in, ag, nil)
		}
	}
}

// runner はツールの代わりに走らせる処理（Agent ツールと SubagentHandback 用）。
type runner func() (string, object, *toolFailed)

func (s *session) useTool(tool string, in object, ag *agent, run runner) {
	if p, ok := in["file_path"].(string); ok && !filepath.IsAbs(p) {
		in["file_path"] = filepath.Join(s.root, p)
	}
	if tool == "Bash" {
		if _, ok := in["description"]; !ok {
			in["description"] = ""
		}
	}
	call := "toolu_" + hex24()
	s.emit(assistant([]any{object{"type": "tool_use", "id": call, "name": tool, "input": in}}, ag), true)
	pre := s.base("PreToolUse", ag, object{"tool_name": tool, "tool_input": in, "tool_use_id": call})
	if b := s.fire(pre); b != nil {
		s.emit(result(call, b.text, true, ag), true)
		return
	}
	s.current = call
	var (
		text string
		resp object
		fail *toolFailed
	)
	if run != nil {
		text, resp, fail = run()
	} else {
		text, resp, fail = tools[tool](s, in)
	}
	if fail != nil {
		s.fire(s.base("PostToolUseFailure", ag, object{
			"tool_name": tool, "tool_input": in, "tool_use_id": call, "error": fail.text, "is_interrupt": false,
		}))
		s.emit(result(call, fail.text, true, ag), true)
		return
	}
	s.fire(s.base("PostToolUse", ag, object{"tool_name": tool, "tool_input": in, "tool_response": resp, "tool_use_id": call}))
	s.emit(result(call, text, false, ag), true)
}

func (s *session) runAgent(spec AgentSpec) {
	kind := spec.Type
	if kind == "" {
		kind = "general-purpose"
	}
	ag := &agent{id: hex17(), kind: kind}
	in := object{"description": spec.Description, "prompt": spec.Prompt, "subagent_type": kind, "run_in_background": false}
	s.useTool("Agent", in, nil, func() (string, object, *toolFailed) {
		ag.call = s.current
		s.fire(s.base("SubagentStart", ag, nil))
		s.play(spec.Script, ag)
		report := "完了"
		if spec.Report != nil {
			report = *spec.Report
		}
		s.useTool("SubagentHandback", object{"message": report}, ag, func() (string, object, *toolFailed) {
			return `{"success": true}`, object{"success": true, "message": "Report delivered"}, nil
		})
		s.fire(s.base("SubagentStop", ag, object{
			"stop_hook_active": false, "agent_transcript_path": s.transcript,
			"background_tasks": []any{}, "session_crons": []any{},
		}))
		return report, object{
			"status": "completed", "prompt": in["prompt"], "agentId": ag.id, "agentType": ag.kind,
			"content": []any{object{"type": "text", "text": report}},
		}, nil
	})
	s.ranAgent = true
}

func (s *session) stop(active bool) *blocked {
	return s.fire(s.base("Stop", nil, object{
		"stop_hook_active": active, "last_assistant_message": s.lastText,
		"background_tasks": []any{}, "session_crons": []any{},
	}))
}

func parent(ag *agent) any {
	if ag == nil {
		return nil
	}
	return ag.call
}

func assistant(content []any, ag *agent) object {
	return object{
		"type":               "assistant",
		"message":            object{"role": "assistant", "content": content},
		"parent_tool_use_id": parent(ag),
	}
}

func result(call, text string, isError bool, ag *agent) object {
	block := object{"type": "tool_result", "tool_use_id": call, "content": text}
	if isError {
		block["is_error"] = true
	}
	return object{
		"type":               "user",
		"message":            object{"role": "user", "content": []any{block}},
		"parent_tool_use_id": parent(ag),
	}
}
