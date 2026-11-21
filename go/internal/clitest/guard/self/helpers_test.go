package self

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

// ruleBlob は共通レイヤーのルールの JSON 文字列。
func ruleBlob(t testing.TB, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

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

// mkdirs は dir の下に空のディレクトリを作る。
func mkdirs(t testing.TB, dir string) string {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	return dir
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

// payload は PreToolUse などの入力。欄を足したいときは map にそのまま書く。
type payload = map[string]any

// hook は hook の入口（`ccnavi --root <root> <args…>`）へ payload を 1 回渡す。Python の run_ccnavi(cwd=ROOT) と同じく、
// 起こす作業ディレクトリはこのリポジトリの根にする（相対の行き先は payload の cwd が決める）。
func hook(t testing.TB, root string, args []string, body payload, env map[string]string) run.Result {
	t.Helper()
	b, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	return run.Ccnavi(t, append([]string{"--root", root}, args...), run.Opts{Input: string(b), Env: env, Dir: base.Root()})
}

// verdict は hook の答えの判定と理由。何も出さない呼び出し（通した・保護が黙った）は空。
func verdict(t testing.TB, r run.Result) (decision, reason string) {
	t.Helper()
	if strings.TrimSpace(r.Stdout) == "" {
		return "", ""
	}
	var out struct {
		Specific struct {
			Decision string `json:"permissionDecision"`
			Reason   string `json:"permissionDecisionReason"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("hook の出力が JSON でない: %v\n%s", err, r.Stdout)
	}
	return out.Specific.Decision, out.Specific.Reason
}

// preToolUse は PreToolUse の payload（session s1・cwd）。
func preToolUse(tool string, input map[string]any, cwd string) payload {
	return payload{"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": input, "session_id": "s1", "cwd": cwd}
}

// bash は Bash の PreToolUse の payload。
func bash(command, cwd string) payload {
	return preToolUse("Bash", map[string]any{"command": command}, cwd)
}

// mustLink はハードリンクを作る。作れない機械は飛ばす。
func mustLink(t testing.TB, from, to string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(from, to); err != nil {
		t.Skip("ハードリンクを作れない機械")
	}
}

// initRepo は dir を空の git リポジトリにする。
func initRepo(t testing.TB, dir string) {
	t.Helper()
	git(t, dir, "init", "--quiet")
}
