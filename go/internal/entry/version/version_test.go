package version

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/tickets/testkit"
)

const versionOracle = `
import io, json, sys
from ccnavi.entry import version
out = {}
for key, as_json in (("text", False), ("json", True)):
    buf = io.StringIO()
    version.report(buf, as_json)
    out[key] = buf.getvalue()
json.dump(out, sys.stdout, ensure_ascii=False)
`

func report(t *testing.T, asJSON bool) string {
	t.Helper()
	var b strings.Builder
	if code := Report(&b, asJSON); code != 0 {
		t.Fatalf("終了コード %d", code)
	}
	return b.String()
}

// ソースで動いているとき（commit を埋めていない）の 2 つの形が、Python のソースで動いているときと 1 字も違わない。
func TestReportMatchesPython(t *testing.T) {
	want := testkit.Answers[map[string]string](t, "report", versionOracle, map[string]any{})
	if got := report(t, false); got != want["text"] {
		t.Errorf("text:\n%s\nPython:\n%s", got, want["text"])
	}
	if got := report(t, true); got != want["json"] {
		t.Errorf("json:\n%s\nPython:\n%s", got, want["json"])
	}
}

// 埋めたコミットを言い、組み立てた実行ファイルと言う。sh は `compat: <数>` の行を読む。
func TestReportSaysTheStampedCommit(t *testing.T) {
	commit = "0123abc-dirty"
	t.Cleanup(func() { commit = "" })
	text := report(t, false)
	for _, line := range []string{"commit: 0123abc-dirty\n", "built: yes (組み立てた実行ファイル)\n",
		"compat: " + strconv.Itoa(Compat) + "\n"} {
		if !strings.Contains(text, line) {
			t.Errorf("%q が無い:\n%s", line, text)
		}
	}
	if j := report(t, true); !strings.Contains(j, `"commit": "0123abc-dirty"`) || !strings.Contains(j, `"built": true`) {
		t.Errorf("json:\n%s", j)
	}
}

func readRepo(t *testing.T, rel ...string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(append([]string{testkit.RepoRoot(t)}, rel...)...))
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// 互換の版は実行ファイル・sh・VS Code 拡張・Python の実行ファイル（移行の間は両方を上げる）で同じ値。
func TestCompatAgreesWithTheShAndTheExtension(t *testing.T) {
	for _, c := range []struct {
		rel     []string
		pattern string
	}{
		{[]string{".ccnavi", "scripts", "ccnavi-common.sh"}, `(?m)^` + env.Compat + `=([0-9]+)$`},
		{[]string{"src", "ccnavi", "entry", "version.py"}, `(?m)^COMPAT = ([0-9]+)$`},
		{[]string{"extensions", "vscode", "ccnavi-board", "src", "core", "version.ts"}, `(?m)^export const EXTENSION_COMPAT = ([0-9]+);$`},
	} {
		m := regexp.MustCompile(c.pattern).FindStringSubmatch(readRepo(t, c.rel...))
		if m == nil || m[1] != strconv.Itoa(Compat) {
			t.Errorf("%s の互換の版 %v が %d と違う", filepath.Join(c.rel...), m, Compat)
		}
	}
}

// 版は pyproject.toml の version と同じ。
func TestVersionAgreesWithPyproject(t *testing.T) {
	m := regexp.MustCompile(`(?m)^version = "([^"]+)"$`).FindStringSubmatch(readRepo(t, "pyproject.toml"))
	if m == nil || m[1] != Version {
		t.Errorf("pyproject.toml の版 %v が %s と違う", m, Version)
	}
}
