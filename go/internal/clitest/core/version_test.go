package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// `ccnavi --version [--json]` の受入テスト。`--version` が版・組み立ての元のコミット・互換の版・書式の版を言い、
// 設定もワークスペースも読まずに 0 で終わること。組み立ての元のコミットは、埋めていなければ `unknown` と言う。
// 版と互換の版が sh・VS Code 拡張と同じであることは `entry/version` の go test が、sh の互換の版の `--lint` での確かめは
// lint_compat が見る。

// version は `ccnavi [--root <root>] <args…>` を、リポジトリを作業ディレクトリにしてMain が整えた素の環境で起こす。
func version(t *testing.T, root string, args ...string) run.Result {
	t.Helper()
	if root != "" {
		args = append([]string{"--root", root}, args...)
	}
	return run.Ccnavi(t, args, run.Opts{Dir: base.Root()})
}

// V10: --json は版・コミット・互換の版・書式の名前を言う。
func TestVersionJSON(t *testing.T) {
	t.Parallel()
	r := version(t, "", "--version", "--json")
	if r.Code != 0 {
		t.Fatalf("code = %d, stderr = %s", r.Code, r.Stderr)
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(r.Stdout), &body); err != nil {
		t.Fatal(err)
	}
	if body["schema"] != float64(1) {
		t.Errorf("schema = %v", body["schema"])
	}
	if v, _ := body["version"].(string); !regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+$`).MatchString(v) {
		t.Errorf("version = %v", body["version"])
	}
	// テストの実行ファイルはコミットを埋めずに組むので、組み立ての元のコミットは分からない。
	if body["commit"] != "unknown" || body["built"] != false {
		t.Errorf("commit = %v, built = %v", body["commit"], body["built"])
	}
	if _, ok := body["compat"].(float64); !ok {
		t.Errorf("compat = %v", body["compat"])
	}
	for _, key := range []string{"flags", "features"} {
		if _, ok := body[key]; ok {
			t.Errorf("%s は廃止した欄", key)
		}
	}
	var formats []string
	for k := range body["formats"].(map[string]any) {
		formats = append(formats, k)
	}
	slices.Sort(formats)
	if want := []string{"phases", "risks", "rules", "ticket"}; !slices.Equal(formats, want) {
		t.Errorf("formats = %v, want %v", formats, want)
	}
}

// V11: 文字の形は 1 項目 1 行。sh は `compat: <数>` の行を sed で読む。
func TestVersionTextFormIsOneItemPerLine(t *testing.T) {
	t.Parallel()
	var body struct {
		Version string
		Compat  int
	}
	if err := json.Unmarshal([]byte(version(t, "", "--version", "--json").Stdout), &body); err != nil {
		t.Fatal(err)
	}
	r := version(t, "", "--version")
	if r.Code != 0 {
		t.Fatalf("code = %d, stderr = %s", r.Code, r.Stderr)
	}
	if !strings.HasPrefix(r.Stdout, "ccnavi "+body.Version+"\n") {
		t.Errorf("先頭の行が版でない: %q", r.Stdout)
	}
	for _, line := range []string{"compat: " + strconv.Itoa(body.Compat), "commit: unknown"} {
		if !slices.Contains(strings.Split(r.Stdout, "\n"), line) {
			t.Errorf("%q の行が無い: %q", line, r.Stdout)
		}
	}
}

// V12: 読めない設定のワークスペースでも、在らないルートでも答える。
func TestVersionReadsNoSettingsAndNoWorkspace(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	rules := base.CommonPath(root, "rules")
	if err := os.MkdirAll(filepath.Dir(rules), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(rules, []byte("version: [\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := version(t, root, "--version"); r.Code != 0 || r.Stderr != "" {
		t.Errorf("壊れた設定: code = %d, stderr = %q", r.Code, r.Stderr)
	}
	if r := version(t, filepath.Join(base.Root(), "no-such-dir"), "--version", "--json"); r.Code != 0 {
		t.Errorf("在らないルート: code = %d, stderr = %s", r.Code, r.Stderr)
	}
}
