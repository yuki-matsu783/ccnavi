package core

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/yaml"
)

// このパッケージの _test.go が共有する道具。

// writeFile は dir の下の相対パス rel に text を書き（途中のディレクトリも作る）、書いたパスを返す。
func writeFile(t testing.TB, dir, rel, text string) string {
	t.Helper()
	path := filepath.Join(dir, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// mustJSON は v を JSON 文字列にする。YAML は JSON の上位互換なので、設定は JSON で書いて実行ファイルに読ませる。
func mustJSON(t testing.TB, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// ccnavi は `ccnavi --root <root> <args…>` を、リポジトリの根を作業ディレクトリにして起こす（標準入力は空）。
// このリポジトリは ccnavi を自分自身に仕掛けているので、--root でテスト用の木に固定する。
func ccnavi(t testing.TB, root string, env map[string]string, args ...string) run.Result {
	t.Helper()
	return run.Ccnavi(t, append([]string{"--root", root}, args...), run.Opts{Dir: base.Root(), Env: env})
}

// workspace は作業の決まり（.ccnavi/CCNAVI.md）を置いた使い捨ての木。
func workspace(t testing.TB) string {
	t.Helper()
	root := base.TempDir(t)
	base.PutCCNaviMD(t, root)
	return root
}

var countsRe = regexp.MustCompile(`(?m)^error (\d+) 件、warn (\d+) 件`)

// counts は報告の最後の件数の行から error と warn の件数を読む。
func counts(t testing.TB, text string) (errors, warns int) {
	t.Helper()
	found := countsRe.FindAllStringSubmatch(text, -1)
	if len(found) == 0 {
		t.Fatalf("件数の行が無い: %q", text)
	}
	last := found[len(found)-1]
	errors, _ = strconv.Atoi(last[1])
	warns, _ = strconv.Atoi(last[2])
	return errors, warns
}

// mustContain は text が want を全部含むことを見る。
func mustContain(t testing.TB, what, text string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !strings.Contains(text, w) {
			t.Errorf("%s に %q が無い: %q", what, w, text)
		}
	}
}

// mustNotContain は text が unwanted のどれも含まないことを見る。
func mustNotContain(t testing.TB, what, text string, unwanted ...string) {
	t.Helper()
	for _, w := range unwanted {
		if strings.Contains(text, w) {
			t.Errorf("%s に %q がある: %q", what, w, text)
		}
	}
}

// git は dir で `git <args…>`（名前とメールは固定）を回し、出力を返す。走った機械の git の設定は base.Main が締め出してある。
func git(t testing.TB, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@example.com"}, args...)...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return string(out)
}

// emptyRoot は空の使い捨ての木（設定ファイルの有無まで自分で決めたいテスト用）。
func emptyRoot(t testing.TB) string { t.Helper(); return base.TempDir(t) }

// mkdir は dir（と途中）を作って返す。
func mkdir(t testing.TB, dir string) string {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	return dir
}

// putCommonRules は共通レイヤーの既定の場所にルールファイルを置く。`--rules` は診断でだけ有効で、hook の判定には届かない。
// 書き出すのは JSON（YAML は JSON の上位互換で、判定が読むのと同じ読み手が受け取る）。
func putCommonRules(t testing.TB, root string, sections map[string]any) string {
	t.Helper()
	body := map[string]any{"version": 1}
	for k, v := range sections {
		body[k] = v
	}
	return writeFile(t, root, base.CommonRelpath("rules"), mustJSON(t, body))
}

// judge は PreToolUse の payload を渡して判定を 1 回取り、hookSpecificOutput を返す（標準出力が空なら空の map）。
// extra は ccnavi の引数（`--mode enable` は既定で付く）。
func judge(t testing.TB, root string, payload map[string]any, extra ...string) map[string]any {
	t.Helper()
	p := map[string]any{"hook_event_name": "PreToolUse", "cwd": root}
	for k, v := range payload {
		p[k] = v
	}
	args := []string{"--root", root}
	if !slices.Contains(extra, "--mode") {
		args = append(args, "--mode", "enable")
	}
	done := run.Ccnavi(t, append(args, extra...), run.Opts{Input: mustJSON(t, p), Dir: base.Root()})
	if strings.TrimSpace(done.Stdout) == "" {
		return map[string]any{}
	}
	var out struct {
		HookSpecificOutput map[string]any `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &out); err != nil || out.HookSpecificOutput == nil {
		t.Fatalf("標準出力が期待した JSON ではない: %v\nstdout: %q", err, done.Stdout)
	}
	return out.HookSpecificOutput
}

// judgeTool は Bash（command）か、それ以外（file_path）の呼び出しを判定する。
func judgeTool(t testing.TB, root, tool, subject string, extra ...string) map[string]any {
	field := "file_path"
	if tool == "Bash" {
		field = "command"
	}
	return judge(t, root, map[string]any{"tool_name": tool, "tool_input": map[string]any{field: subject}}, extra...)
}

func str(m map[string]any, key string) string { s, _ := m[key].(string); return s }

// loadYAML は YAML のファイルを、実行ファイルと同じ読み手（infra/yaml）で読み、map・[]any・スカラーの木にして返す。
func loadYAML(t testing.TB, path string) map[string]any {
	t.Helper()
	text, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	v, err := yaml.Load(string(text))
	if err != nil {
		t.Fatalf("%s を読めない: %v", path, err)
	}
	m, _ := plain(v).(map[string]any)
	return m
}

func plain(v any) any {
	switch x := v.(type) {
	case *yaml.Map:
		m := map[string]any{}
		for k, val := range x.All() {
			m[fmt.Sprint(k)] = plain(val)
		}
		return m
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = plain(e)
		}
		return out
	}
	return v
}

// ruleByID は rules.yml の木から、id が合うルール（deny・ask・allow の順）を返す。
func ruleByID(t testing.TB, rules map[string]any, id string) map[string]any {
	t.Helper()
	for _, section := range []string{"deny", "ask", "allow"} {
		list, _ := rules[section].([]any)
		for _, r := range list {
			if m, _ := r.(map[string]any); m["id"] == id {
				return m
			}
		}
	}
	t.Fatalf("id %q のルールが無い", id)
	return nil
}

func jsonUnmarshal(text string, v any) error { return json.Unmarshal([]byte(text), v) }
