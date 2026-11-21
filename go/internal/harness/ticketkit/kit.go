package ticketkit

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// TryGit は git を 1 回起こす（コミッターの名前を固定する）。標準出力と、失敗したときのエラー（標準エラーを含む）を返す。
func TryGit(dir string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), base.ExecTimeout)
	defer cancel()
	full := append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)
	cmd := exec.CommandContext(ctx, "git", full...)
	cmd.Dir = dir
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return string(out), &gitError{args: args, stderr: stderr.String(), err: err}
	}
	return string(out), nil
}

type gitError struct {
	args   []string
	stderr string
	err    error
}

func (e *gitError) Error() string {
	return "git " + strings.Join(e.args, " ") + ": " + e.err.Error() + "\n" + e.stderr
}

// Git は TryGit で、失敗したらテストを止める。標準出力はそのまま返す（末尾の改行を含む）。
func Git(t testing.TB, dir string, args ...string) string {
	t.Helper()
	out, err := TryGit(dir, args...)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// Write は path に text を書く（親のディレクトリも作る。改行はそのまま）。path を返す。
func Write(t testing.TB, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// Read は path の中身。読めなければテストを止める。
func Read(t testing.TB, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

// ReadJSON は path の JSON を map に読む。
func ReadJSON(t testing.TB, path string) map[string]any {
	t.Helper()
	var data map[string]any
	if err := json.Unmarshal([]byte(Read(t, path)), &data); err != nil {
		t.Fatalf("%s が JSON でない: %v", path, err)
	}
	return data
}

// ParseJSON は text（コマンドの標準出力）の JSON を map に読む。
func ParseJSON(t testing.TB, text string) map[string]any {
	t.Helper()
	var data map[string]any
	if err := json.Unmarshal([]byte(text), &data); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, text)
	}
	return data
}

// Exists は path がある（ファイルでもディレクトリでも）か。
func Exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// OK は終了コードが 0 であることを確かめる（違えば出力つきで止める）。r を返す。
func OK(t testing.TB, r run.Result) run.Result {
	t.Helper()
	if r.Code != 0 {
		t.Fatalf("終了コード %d（0 のはず）\nstdout: %s\nstderr: %s", r.Code, r.Stdout, r.Stderr)
	}
	return r
}

// Fails は終了コードが 0 でないことを確かめる。r を返す。
func Fails(t testing.TB, r run.Result) run.Result {
	t.Helper()
	if r.Code == 0 {
		t.Fatalf("終了コード 0（断るはず）\nstdout: %s\nstderr: %s", r.Stdout, r.Stderr)
	}
	return r
}

// Has は s に sub が含まれることを確かめる。含まれなくてもテストは続ける。
func Has(t testing.TB, s, sub string) {
	t.Helper()
	if !strings.Contains(s, sub) {
		t.Errorf("%q が含まれない:\n%s", sub, s)
	}
}

// Lacks は s に sub が含まれないことを確かめる。
func Lacks(t testing.TB, s, sub string) {
	t.Helper()
	if strings.Contains(s, sub) {
		t.Errorf("%q が含まれている:\n%s", sub, s)
	}
}

// Out は標準出力と標準エラーをつなげた文字列（どちらに出るかを問わずに言葉を探すとき）。
func Out(r run.Result) string { return r.Stdout + r.Stderr }

// Reason は hook の出力（JSON）の理由の文。無ければ追加の文脈。出力が空なら空。
func Reason(t testing.TB, r run.Result) string {
	t.Helper()
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	var out struct {
		Hook struct {
			Reason  string `json:"permissionDecisionReason"`
			Context string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("hook の出力が JSON でない: %q", r.Stdout)
	}
	if out.Hook.Reason != "" {
		return out.Hook.Reason
	}
	return out.Hook.Context
}

// Decision は hook の出力の permissionDecision（allow・ask・deny）。出力が空なら空。
func Decision(t testing.TB, r run.Result) string {
	t.Helper()
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	var out struct {
		Hook struct {
			Decision string `json:"permissionDecision"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("hook の出力が JSON でない: %q", r.Stdout)
	}
	return out.Hook.Decision
}

// Lines は s を行に分ける（末尾の改行で空行を足さない）。
func Lines(s string) []string {
	s = strings.TrimRight(s, "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}
