package fakeclaude

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"ccnavi/internal/infra/env"
)

// 契約の場面（本物とスタブで同じもの）。GitLab の契約の場面と同じ役回りで、使い捨ての木・hook の入力を書き出すだけの hook・
// 打たせる手を持つ。hook が受けた JSON と stream-json を、形（Node）と振る舞い（Observe）にまとめる。
//
//   - 本物の側: M2 の claudrecord が Prompt・PromptResume を `claude -p` に渡し、recorded.json に録る
//   - スタブの側: clitest/contract/claude が ScenePlan(n)（Prompt と同じ手の台本）を `teststub claude` に渡す

// RecordedRel は本物から録った形と振る舞い（リポジトリの根からの相対）。
const RecordedRel = "tests/contract/claude/recorded.json"

const (
	// HookPathEnv は settings.json の env に入れ、hook の命令で使う変数。ccnavi の hook が CCNAVI_ENTRY_PATH を使うのと同じ形にする
	// （env が hook に届かなければ hook が起きず、観察が空になる）。
	HookPathEnv = "CONTRACT_HOOK_PATH"
	// HookPath は木の中の hook の置き場。値は本物から録った記録（recorded.json の settings_env_in_hook）と同じにしてある。
	HookPath = ".rec/hook.py"
)

// Events は場面が hook を足すイベント。
var Events = []string{
	"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "SubagentStart", "SubagentStop", "Stop",
}

// Prompt は本物の claude に渡す、打つ手の列。
const Prompt = `これはツールの動きを録るテストです。
次の手を、上から順に 1 つずつ、書いたとおりのツールで打ってください。
止められたり失敗したりしても、迂回せず、言い換えず、次の手へ進んでください。説明は要りません。

1. Read ツールで a.txt を読む
2. Write ツールで deny-me.txt に x と書く
3. Edit ツールで a.txt の hello を hi に置き換える
4. Bash ツールで ` + "`echo exit2-me`" + ` を打つ
5. Write ツールで ask-me.txt に x と書く
6. Bash ツールで ` + "`sh -c 'exit 3'`" + ` を打つ
7. Bash ツールで ` + "`echo block-post`" + ` を打つ
8. Read ツールで ctx-me.txt を読む
9. サブエージェント（general-purpose）を 1 つ起動し、次のとおり頼む。
   run_in_background は false にして、終わるまで待つ。
   「Read ツールで a.txt を読み、Write ツールで sub.txt に ok と書いて終わる」
10. 最後に「おわり」とだけ答える
`

// PromptResume は --resume でつなぐ 2 回目の prompt。
const PromptResume = "Read ツールで a.txt を読み、「おわり」とだけ答えてください。"

// Forbidden は、機械・人・モデルに結び付く文字列。記録に現れたら置かない。
var Forbidden = []struct{ Name, Regex string }{
	{"メールの形", `[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+`},
	{"モデルの名前", `claude-(opus|sonnet|haiku|fable)|\bhaiku\b|\bsonnet\b|\bopus\b`},
	{"機械のパス", `/home/|/root\b|/tmp/|[A-Za-z]:\\\\`},
	{"呼び出しの ID", `toolu_`},
	{"REC の目印", `REC-`},
}

func tool(name string, input object) Move { return Move{Tool: &name, Input: input} }

func say(text string) Move { return Move{Say: &text} }

// ScenePlan は Prompt（n=0）・PromptResume（n=1）と同じ手の台本（スタブ用）。
func ScenePlan(n int) Plan {
	if n == 1 {
		return Plan{Script: []Move{tool("Read", object{"file_path": "a.txt"}), say("おわり")}}
	}
	return Plan{
		Script: []Move{
			tool("Read", object{"file_path": "a.txt"}),
			tool("Write", object{"file_path": "deny-me.txt", "content": "x\n"}),
			tool("Edit", object{"file_path": "a.txt", "old_string": "hello", "new_string": "hi"}),
			tool("Bash", object{"command": "echo exit2-me"}),
			tool("Write", object{"file_path": "ask-me.txt", "content": "x\n"}),
			tool("Bash", object{"command": "sh -c 'exit 3'"}),
			tool("Bash", object{"command": "echo block-post"}),
			tool("Read", object{"file_path": "ctx-me.txt"}),
			{Agent: &AgentSpec{
				Type: "general-purpose", Description: "Read a.txt and write sub.txt",
				Script: []Move{
					tool("Read", object{"file_path": "a.txt"}),
					tool("Write", object{"file_path": "sub.txt", "content": "ok"}),
				},
			}},
			say("おわり"),
		},
		AfterStopBlock: []Move{tool("Write", object{"file_path": "stop.txt", "content": "done\n"})},
	}
}

// BuildTree は base の下に使い捨ての木（git のリポジトリ）を作って返す。hook は teststub の claude-hook を起こす sh の包み。
func BuildTree(base, teststub string) (string, error) {
	tree := filepath.Join(base, "tree")
	rec := filepath.Join(tree, ".rec")
	if err := os.MkdirAll(filepath.Join(tree, ".claude"), 0o755); err != nil {
		return "", err
	}
	if err := os.MkdirAll(rec, 0o755); err != nil {
		return "", err
	}
	wrapper := "#!/bin/sh\nexec '" + slash(teststub) + "' claude-hook '" + slash(rec) + "' \"$@\"\n"
	data, err := json.MarshalIndent(Settings(), "", "  ")
	if err != nil {
		return "", err
	}
	files := map[string]string{
		filepath.Join(tree, HookPath):                   wrapper,
		filepath.Join(tree, ".claude", "settings.json"): string(data),
		filepath.Join(tree, "a.txt"):                    "hello\n",
		filepath.Join(tree, "ctx-me.txt"):               "ctx\n",
	}
	for path, body := range files {
		if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
			return "", err
		}
	}
	if out, err := exec.Command("git", "init", "-q", tree).CombinedOutput(); err != nil {
		return "", fmt.Errorf("git init: %v\n%s", err, out)
	}
	return tree, nil
}

func slash(path string) string { return strings.ReplaceAll(path, `\`, "/") }

// Settings は場面の settings.json（全イベントに hook を登録する。UserPromptSubmit は 2 本）。
func Settings() object {
	hooks := object{}
	for _, ev := range Events {
		cmd := `sh "${CLAUDE_PROJECT_DIR}/${` + HookPathEnv + `}"`
		entries := []any{object{"type": "command", "command": cmd + " A", "timeout": 30}}
		if ev == "UserPromptSubmit" {
			entries = append(entries, object{"type": "command", "command": cmd + " B", "timeout": 30})
		}
		hooks[ev] = []any{object{"hooks": entries}}
	}
	return object{"env": object{HookPathEnv: HookPath}, "hooks": hooks}
}

// 形と振る舞いへのまとめ -------------------------------------------------------

// Doc は本物またはスタブの 1 回分の記録（recorded.json の hooks・stream・observations）。
type Doc struct {
	Hooks        map[string]*Node `json:"hooks"`
	Stream       map[string]*Node `json:"stream"`
	Observations object           `json:"observations"`
	// Raw は録った生の記録（記録に入れない）。
	Raw Raw `json:"-"`
}

// Raw は 2 回の stream-json（行ごとの文字列）と、hook の記録のパス。
type Raw struct {
	Streams []string
	LogPath string
	Parsed  [][]object
}

// Recorded は recorded.json（本物だけ。source・claude_version のほかは Doc と同じ）。
func Recorded(path string) (Doc, error) {
	var doc Doc
	data, err := os.ReadFile(path)
	if err != nil {
		return doc, err
	}
	return doc, json.Unmarshal(data, &doc)
}

type logEntry struct {
	Tag         string   `json:"tag"`
	Payload     object   `json:"payload"`
	T0          float64  `json:"t0"`
	T1          float64  `json:"t1"`
	EnvNames    []string `json:"env_names"`
	ProjectDir  *string  `json:"project_dir"`
	HookPathEnv *string  `json:"hook_path_env"`
	HookCwd     string   `json:"hook_cwd"`
	run         int
}

var (
	modelKey = regexp.MustCompile(`(?i)claude|haiku|sonnet|opus|fable`)
	// 呼び出しの ID（`toolu_…`・UUID・16 桁以上の 16 進）をキーにした辞書のキー
	idKey = regexp.MustCompile(`^toolu_|^[0-9a-f]{8}-[0-9a-f]{4}-|^[0-9a-f]{16,}$`)
)

// collapse は、形の中でキーにモデルの名前や呼び出しの ID が入る辞書（modelUsage・wire_tool_inputs など）のキーを
// `<model>`・`<id>` にまとめる。まとめたキーどうしの形は合わせる。
func collapse(node *Node) *Node {
	out := *node
	if node.Keys != nil {
		keys := map[string]*Node{}
		renamed := false
		for k, v := range node.Keys {
			name := k
			switch {
			case modelKey.MatchString(k):
				name = "<model>"
			case idKey.MatchString(k):
				name = "<id>"
			}
			renamed = renamed || name != k
			v = collapse(v)
			if have, ok := keys[name]; ok {
				v = Merge(have, v)
			}
			keys[name] = v
		}
		out.Keys = keys
		if renamed {
			out.Opt = nil
		}
	}
	if node.Items != nil {
		out.Items = collapse(node.Items)
	}
	return &out
}

func add(table map[string]*Node, key string, value any) {
	node := collapse(Normalize(value, 0))
	if have, ok := table[key]; ok {
		node = Merge(have, node)
	}
	table[key] = node
}

func toolKey(payload object) string {
	ev := asString(payload["hook_event_name"])
	if ev == "" {
		ev = "?"
	}
	if tool := asString(payload["tool_name"]); tool != "" {
		return ev + ":" + tool
	}
	return ev
}

func readJSONL(text string) []object {
	var out []object
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "{") {
			continue
		}
		if v, err := decode(line); err == nil {
			if o := asObject(v); o != nil {
				out = append(out, o)
			}
		}
	}
	return out
}

func readLog(path string, nFirst int) ([]logEntry, int, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()
	var logs []logEntry
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 16<<20)
	for sc.Scan() {
		var e logEntry
		dec := json.NewDecoder(strings.NewReader(sc.Text()))
		dec.UseNumber()
		if err := dec.Decode(&e); err != nil {
			return nil, 0, err
		}
		if nFirst >= 0 && len(logs) >= nFirst {
			e.run = 1
		}
		logs = append(logs, e)
	}
	return logs, len(logs), sc.Err()
}

// Collect は run(回の番号, prompt) を 2 回呼び、hook と stream-json を形と振る舞いにまとめる。run は stream-json の文字列を返す。
func Collect(tree string, run func(n int, prompt string) (string, error)) (Doc, error) {
	logPath := filepath.Join(tree, ".rec", "log.jsonl")
	first, err := run(0, Prompt)
	if err != nil {
		return Doc{}, err
	}
	_, nFirst, err := readLog(logPath, -1)
	if err != nil {
		return Doc{}, err
	}
	second, err := run(1, PromptResume)
	if err != nil {
		return Doc{}, err
	}
	logs, _, err := readLog(logPath, nFirst)
	if err != nil {
		return Doc{}, err
	}
	streamsText := []string{first, second}
	streams := [][]object{readJSONL(first), readJSONL(second)}
	hooks, stream := map[string]*Node{}, map[string]*Node{}
	for _, e := range logs {
		if e.Tag == "A" {
			add(hooks, toolKey(e.Payload), e.Payload)
		}
	}
	for _, events := range streams {
		for _, e := range events {
			sub, ok := e["subtype"].(string)
			if !ok {
				sub = ""
			}
			add(stream, fmt.Sprintf("%v/%s", e["type"], sub), e)
		}
	}
	return Doc{
		Hooks: hooks, Stream: stream, Observations: observe(tree, logs, streams),
		Raw: Raw{Streams: streamsText, LogPath: logPath, Parsed: streams},
	}, nil
}

// モデルが書く行の type。ここに文が出るかはモデルの答え方で変わるので、出た場所に数えない。
func isModelType(t any) bool { return t == "assistant" || t == "result" }

// textIn は needle を含む stream-json の行の type/subtype の一覧（モデルが書く行を除く）。
func textIn(events []object, needle string) []string {
	set := map[string]bool{}
	for _, e := range events {
		if isModelType(e["type"]) || !strings.Contains(marshal(e), needle) {
			continue
		}
		sub, ok := e["subtype"].(string)
		if !ok {
			sub = ""
		}
		set[fmt.Sprintf("%v/%s", e["type"], sub)] = true
	}
	return keys(set)
}

func keys(set map[string]bool) []string {
	out := []string{}
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// toolResultText は呼び出しの結果（エラーか、内容の JSON）。無ければ ok=false。
func toolResultText(events []object, id string) (isError bool, content string, ok bool) {
	for _, e := range events {
		if e["type"] != "user" {
			continue
		}
		for _, b := range asList(asObject(e["message"])["content"]) {
			if block := asObject(b); block != nil && block["tool_use_id"] == id {
				failed, _ := block["is_error"].(bool)
				return failed, marshal(block["content"]), true
			}
		}
	}
	return false, "", false
}

func exists(path string) bool { _, err := os.Stat(path); return err == nil }

func boolSet(values map[bool]bool) []any {
	out := []any{}
	if values[false] {
		out = append(out, false)
	}
	if values[true] {
		out = append(out, true)
	}
	return out
}

func strList(items []string) []any {
	out := []any{}
	for _, s := range items {
		out = append(out, s)
	}
	return out
}

func observe(tree string, logs []logEntry, streams [][]object) object {
	var first, aHooks, secondA []logEntry
	for _, e := range logs {
		if e.run == 0 {
			first = append(first, e)
			if e.Tag == "A" {
				aHooks = append(aHooks, e)
			}
		} else if e.Tag == "A" {
			secondA = append(secondA, e)
		}
	}
	s1 := streams[0]
	event := func(e logEntry) string { return asString(e.Payload["hook_event_name"]) }

	pre := func(marker string) object {
		for _, e := range aHooks {
			if event(e) == "PreToolUse" && strings.Contains(marshal(orEmpty(e.Payload["tool_input"])), marker) {
				return e.Payload
			}
		}
		return nil
	}
	resultOf := func(marker string) object {
		p := pre(marker)
		if p == nil {
			return object{"called": false}
		}
		posts := []any{}
		for _, e := range aHooks {
			if e.Payload["tool_use_id"] == p["tool_use_id"] && event(e) != "PreToolUse" {
				posts = append(posts, event(e))
			}
		}
		out := object{"called": true, "tool": p["tool_name"], "later_events": posts}
		if failed, content, ok := toolResultText(s1, asString(p["tool_use_id"])); ok {
			out["is_error"] = failed
			reasons := object{}
			for _, m := range []string{"REC-DENY", "REC-ASK", "REC-EXIT2", "REC-POSTBLOCK"} {
				reasons[m] = strings.Contains(content, m)
			}
			out["reason_in_result"] = reasons
		}
		return out
	}
	merged := func(base object, extra object) object {
		for k, v := range extra {
			base[k] = v
		}
		return base
	}

	var ups []logEntry
	for _, e := range first {
		if event(e) == "UserPromptSubmit" {
			ups = append(ups, e)
		}
	}
	var parallel any
	if len(ups) >= 2 {
		a, b := ups[0], ups[1]
		parallel = a.T0 < b.T1 && b.T0 < a.T1
	}
	var stops, agentPayloads, subStart []object
	for _, e := range aHooks {
		switch event(e) {
		case "Stop":
			stops = append(stops, e.Payload)
		case "SubagentStart":
			subStart = append(subStart, e.Payload)
		}
		if truthy(e.Payload["agent_id"]) {
			agentPayloads = append(agentPayloads, e.Payload)
		}
	}
	active := []any{}
	for _, s := range stops {
		active = append(active, s["stop_hook_active"] == true)
	}
	resumeStarts := []any{}
	for _, e := range secondA {
		if event(e) == "SessionStart" {
			resumeStarts = append(resumeStarts, e.Payload["source"])
		}
	}
	nested := false
	for _, e := range s1 {
		if e["type"] == "assistant" && truthy(e["parent_tool_use_id"]) {
			nested = true
		}
	}
	types, nestedKinds, launcher := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, p := range subStart {
		types[fmt.Sprint(p["agent_type"])] = true
	}
	for _, p := range agentPayloads {
		nestedKinds[toolKey(p)] = true
	}
	for _, e := range aHooks {
		p := e.Payload
		name := asString(p["tool_name"])
		if event(e) == "PreToolUse" && !truthy(p["agent_id"]) && name != "Read" && name != "Write" && name != "Edit" && name != "Bash" {
			launcher[name] = true
		}
	}
	seq := func(entries []logEntry) []any {
		out := []any{}
		for _, e := range entries {
			out = append(out, toolKey(e.Payload))
		}
		return out
	}
	projectIsCwd, hookCwd := map[bool]bool{}, map[bool]bool{}
	hookEnv, envNames := map[string]bool{}, map[string]bool{}
	for _, e := range aHooks {
		if cwd := asString(e.Payload["cwd"]); cwd != "" {
			projectIsCwd[e.ProjectDir != nil && *e.ProjectDir == cwd] = true
		}
		hookCwd[e.ProjectDir != nil && *e.ProjectDir == e.HookCwd] = true
		if e.HookPathEnv != nil {
			hookEnv[*e.HookPathEnv] = true
		} else {
			hookEnv["None"] = true
		}
		for _, n := range e.EnvNames {
			if n == env.ClaudeProjectDir || n == env.ClaudeCodeEntrypoint || n == env.ClaudeEnvFile {
				envNames[n] = true
			}
		}
	}
	return object{
		"sequence_run1":              seq(aHooks),
		"sequence_run2":              seq(secondA),
		"deny":                       merged(resultOf("deny-me"), object{"file_written": exists(filepath.Join(tree, "deny-me.txt"))}),
		"ask":                        merged(resultOf("ask-me"), object{"file_written": exists(filepath.Join(tree, "ask-me.txt"))}),
		"exit2":                      resultOf("exit2-me"),
		"bash_exit3":                 resultOf("exit 3"),
		"post_block":                 merged(resultOf("block-post"), object{"seen_in": strList(textIn(s1, "REC-POSTBLOCK"))}),
		"post_context_seen_in":       strList(textIn(s1, "REC-POSTCTX")),
		"session_context_seen_in":    strList(textIn(s1, "REC-SESSION-CTX")),
		"user_prompt_hooks_parallel": parallel,
		"stop": object{
			"count":                 len(stops),
			"stop_hook_active":      active,
			"continued_after_block": exists(filepath.Join(tree, "stop.txt")),
			"reason_seen_in":        strList(textIn(s1, "REC-STOP")),
		},
		"subagent": object{
			"started":                           len(subStart),
			"agent_types":                       strList(keys(types)),
			"nested_tool_events":                strList(keys(nestedKinds)),
			"stream_has_nested_assistant_lines": nested,
			"launcher_tools":                    strList(keys(launcher)),
		},
		"resume_session_start_source": resumeStarts,
		"project_dir_is_cwd":          boolSet(projectIsCwd),
		"hook_cwd_is_project_dir":     boolSet(hookCwd),
		"settings_env_in_hook":        strList(keys(hookEnv)),
		"env_names":                   strList(keys(envNames)),
	}
}

// orEmpty は tool_input が無いときの空の辞書（Python の `p.get("tool_input") or {}`）。
func orEmpty(v any) any {
	if v == nil {
		return object{}
	}
	return v
}

// truthy は Python の真偽（null・空文字・false・0・空の入れ物は偽）。
func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case string:
		return x != ""
	case bool:
		return x
	case []any:
		return len(x) > 0
	case map[string]any:
		return len(x) > 0
	}
	return true
}
