// claude -p のスタブ（harness/fakeclaude）が、本物から録った形と振る舞いに合っているかを見る。
//
// 本物の記録は tests/contract/claude/recorded.json（M2 の claudrecord が作る）。場面（木・hook・打つ手）は fakeclaude の scene.go で本物と共有する。
//   - 形: スタブが出す欄は本物にもあり、型は本物の型に含まれる。本物にだけある欄は比べない（スタブは ccnavi と実行器が読む欄だけを出す）。
//     ただし ccnavi が読む hook の欄（readsFields）は、本物にあればスタブにもなければならない
//   - 振る舞い: observations（呼ばれたイベントの並び、止めたときの理由の出どころ、Stop の差し戻し、サブエージェント、--resume、hook の環境）は本物と同じ
package claude

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/fakeclaude"
	"ccnavi/internal/harness/run"
)

// ccnavi が hook の payload から読む欄（go/internal/infra/hookio/decode.go の欄の一覧）。
var readsFields = []string{
	"hook_event_name", "tool_name", "session_id", "cwd", "permission_mode", "tool_use_id", "tool_input", "tool_response",
	"agent_id", "agent_type", "error", "is_interrupt", "stop_hook_active", "source", "prompt", "transcript_path",
	"background_tasks", "session_crons",
}

// tool_response の中で ccnavi が読む欄（Write の作成・Agent の識別子）。
var responseReads = map[string][]string{"PostToolUse:Write": {"type"}, "PostToolUse:Agent": {"agentId"}}

const session = "00000000-0000-4000-8000-000000000000"

// runFake は使い捨ての木でスタブを 2 回回し、記録にまとめる。
func runFake(t *testing.T) fakeclaude.Doc {
	t.Helper()
	dir := base.TempDir(t)
	stub := exe.TeststubPath(t)
	tree, err := fakeclaude.BuildTree(dir, stub)
	if err != nil {
		t.Fatal(err)
	}
	doc, err := fakeclaude.Collect(tree, func(n int, prompt string) (string, error) {
		script, err := json.Marshal(fakeclaude.ScenePlan(n))
		if err != nil {
			return "", err
		}
		scriptPath := filepath.Join(dir, fmt.Sprintf("script-%d.json", n))
		if err := writeFile(scriptPath, script); err != nil {
			return "", err
		}
		args := []string{"claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-hook-events", "--permission-mode", "auto"}
		if n > 0 {
			args = append(args, "--resume", session)
		} else {
			args = append(args, "--session-id", session)
		}
		r := run.Command(t, stub, args, run.Opts{Dir: tree, Env: map[string]string{
			fakeclaude.ScriptEnv: scriptPath,
			fakeclaude.HomeEnv:   filepath.Join(dir, "home"),
		}})
		if r.Code != 0 {
			return "", fmt.Errorf("スタブが終了コード %d で終わった: %s", r.Code, r.Stderr)
		}
		return r.Stdout, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

func TestFakeClaudeMatchesTheRecording(t *testing.T) {
	t.Parallel()
	real, err := fakeclaude.Recorded(filepath.Join(base.Root(), fakeclaude.RecordedRel))
	if err != nil {
		t.Fatal(err)
	}
	fake := runFake(t)

	t.Run("スタブの形は本物の形に含まれる", func(t *testing.T) {
		var problems []string
		for section, pair := range map[string][2]map[string]*fakeclaude.Node{
			"hooks":  {fake.Hooks, real.Hooks},
			"stream": {fake.Stream, real.Stream},
		} {
			for key, node := range pair[0] {
				want, ok := pair[1][key]
				if !ok {
					problems = append(problems, section+"/"+key+": 本物に無い")
					continue
				}
				problems = append(problems, notInReal(node, want, section+"/"+key)...)
			}
		}
		sort.Strings(problems)
		if len(problems) > 0 {
			t.Errorf("本物に無い形: %v", problems)
		}
	})

	t.Run("ccnavi が読む欄はスタブにもある", func(t *testing.T) {
		var problems []string
		for key, want := range real.Hooks {
			got, ok := fake.Hooks[key]
			if !ok {
				problems = append(problems, key+": スタブが出さない")
				continue
			}
			for _, field := range readsFields {
				if _, inReal := want.Keys[field]; inReal {
					if _, inFake := got.Keys[field]; !inFake {
						problems = append(problems, key+"."+field+": スタブに無い")
					}
				}
			}
			for _, field := range responseReads[key] {
				if _, ok := got.Keys["tool_response"].Keys[field]; !ok {
					problems = append(problems, key+".tool_response."+field+": スタブに無い")
				}
			}
		}
		sort.Strings(problems)
		if len(problems) > 0 {
			t.Errorf("スタブに無い欄: %v", problems)
		}
	})

	t.Run("振る舞いは本物と同じ", func(t *testing.T) {
		got := roundTrip(t, fake.Observations)
		want := roundTrip(t, real.Observations)
		if !reflect.DeepEqual(got, want) {
			t.Errorf("observations が違う\nスタブ: %s\n本物:   %s", pretty(got), pretty(want))
		}
	})
}

// notInReal はスタブの形のうち、本物に無い欄と、本物の型に含まれない型。
func notInReal(fake, real *fakeclaude.Node, path string) []string {
	var out []string
	for _, kind := range fake.T {
		if !slices.Contains(real.T, kind) {
			out = append(out, fmt.Sprintf("%s: 型 %v が本物 %v に無い", path, fake.T, real.T))
			break
		}
	}
	for k, sub := range fake.Keys {
		if want, ok := real.Keys[k]; ok {
			out = append(out, notInReal(sub, want, path+"."+k)...)
		} else {
			out = append(out, path+"."+k+": 本物に無い欄")
		}
	}
	if fake.Items != nil && real.Items != nil {
		out = append(out, notInReal(fake.Items, real.Items, path+"[]")...)
	}
	return out
}

func roundTrip(t *testing.T, v any) any {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func pretty(v any) string {
	data, _ := json.MarshalIndent(v, "", " ")
	return string(data)
}

func writeFile(path string, data []byte) error { return os.WriteFile(path, data, 0o644) }
