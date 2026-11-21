package rules

// この置き場のテストが共有する道具。入口（ccnavi の引数と標準入力）より内側は呼ばない。

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

type payload = map[string]any

func write(t testing.TB, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func read(t testing.TB, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func mkdirs(t testing.TB, dir string) string {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	return dir
}

// jsonText は v の JSON 文字列。YAML は JSON を読めるので、ルールの置き場へはこれで書く。
func jsonText(t testing.TB, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// git は dir で git を 1 回打つ。環境は run が整える（走った機械の git の設定を締め出す）。
func git(t testing.TB, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)...)
	cmd.Dir = dir
	cmd.Env = run.Environment(run.Opts{})
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return string(out)
}

// withRules は tests/fixtures の見本のルールを共通レイヤーに据えた、テストごとの作業場所。
// base.FixtureWorkspace は読むだけの共有の木なので、記録を読むテストは並行するテストと取り合わないようこちらを使う。
func withRules(t testing.TB, fixture string) string {
	t.Helper()
	root := base.TempDir(t)
	write(t, base.CommonPath(root, "rules"), read(t, filepath.Join(base.Root(), "tests", "fixtures", fixture)))
	return root
}

// withRuleText はルールの本文を置いた作業場所（共通レイヤーの rules.yml）。
func withRuleText(t testing.TB, text string) string {
	t.Helper()
	root := base.TempDir(t)
	write(t, base.CommonPath(root, "rules"), text)
	return root
}

// pre は PreToolUse の payload。
func pre(tool string, input map[string]any) payload {
	return payload{"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": input}
}

func preBash(command string) payload { return pre("Bash", map[string]any{"command": command}) }

// fire は hook の入口（`ccnavi --root <root> <flags…>`）へ payload を 1 回渡す。作業ディレクトリはこのリポジトリの根。
func fire(t testing.TB, root string, flags []string, body any, env map[string]string) run.Result {
	t.Helper()
	text, ok := body.(string)
	if !ok {
		text = jsonText(t, body)
	}
	return run.Ccnavi(t, append([]string{"--root", root}, flags...), run.Opts{Input: text, Env: env, Dir: base.Root()})
}

// reply は hook の応答の hookSpecificOutput。何も出さない呼び出し（通した・保護が黙った）は欄がすべて空。
type reply struct {
	Decision, Reason, Context string
	Event                     string
	System                    string
}

// text は理由と追加の文脈をつなげたもの。
func (r reply) text() string { return r.Reason + r.Context }

func parse(t testing.TB, r run.Result) reply {
	t.Helper()
	if strings.TrimSpace(r.Stdout) == "" {
		return reply{}
	}
	var out struct {
		System   string `json:"systemMessage"`
		Specific struct {
			Event    string `json:"hookEventName"`
			Decision string `json:"permissionDecision"`
			Reason   string `json:"permissionDecisionReason"`
			Context  string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("標準出力が期待した JSON ではない: %v\nstdout: %q\nstderr: %q", err, r.Stdout, r.Stderr)
	}
	return reply{Decision: out.Specific.Decision, Reason: out.Specific.Reason, Context: out.Specific.Context, Event: out.Specific.Event, System: out.System}
}

// records は root の logs/decisions.jsonl の行。
func records(t testing.TB, root string) []map[string]any {
	t.Helper()
	path := filepath.Join(root, "logs", "decisions.jsonl")
	if !exists(path) {
		return nil
	}
	var out []map[string]any
	for _, line := range strings.Split(read(t, path), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		row := map[string]any{}
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatalf("記録の行が JSON でない: %q", line)
		}
		out = append(out, row)
	}
	return out
}

func lastRecord(t testing.TB, root string) map[string]any {
	t.Helper()
	rows := records(t, root)
	if len(rows) == 0 {
		t.Fatal("決定の記録が 1 行も無い")
	}
	return rows[len(rows)-1]
}

func str(row map[string]any, key string) string {
	s, _ := row[key].(string)
	return s
}

func strs(row map[string]any, key string) []string {
	var out []string
	if list, ok := row[key].([]any); ok {
		for _, v := range list {
			s, _ := v.(string)
			out = append(out, s)
		}
	}
	return out
}

func mustHave(t testing.TB, got string, wants ...string) {
	t.Helper()
	for _, w := range wants {
		if !strings.Contains(got, w) {
			t.Errorf("%q が無い:\n%s", w, got)
		}
	}
}

func mustNotHave(t testing.TB, got string, unwanted ...string) {
	t.Helper()
	for _, w := range unwanted {
		if strings.Contains(got, w) {
			t.Errorf("%q が有る:\n%s", w, got)
		}
	}
}

func slash(s string) string { return strings.ReplaceAll(s, `\`, "/") }
