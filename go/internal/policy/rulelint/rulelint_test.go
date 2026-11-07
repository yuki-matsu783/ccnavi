package rulelint

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/policy/ctxfile"
)

// 検査の答えの見本（testdata）と突き合わせる。

type lintCase struct {
	Path    string `json:"path"`
	Root    string `json:"root"`
	Home    string `json:"home"`
	Layer   bool   `json:"layer"`
	Project bool   `json:"project"`
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

// 写しを機械に依らせないため、一時のルートの綴りを置き換えた苦情の文。
const rootMark = "<root>"

func problemsOf(c lintCase, root string) []string {
	got := []string{}
	for _, p := range FileProblems(c.Path, c.Root, Options{Home: c.Home, Layer: c.Layer, Project: c.Project}) {
		got = append(got, masked(p.String(), root))
	}
	return got
}

func masked(s, root string) string {
	if root == "" {
		return s
	}
	return strings.ReplaceAll(s, root, rootMark)
}

func rule(kv ...any) map[string]any {
	m := map[string]any{}
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i].(string)] = kv[i+1]
	}
	return m
}

func TestFileProblemsMatchGolden(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(root, ".ccnavi", "scripts", "ccnavi-git.sh"), "")
	write(t, filepath.Join(root, "docs", "a.md"), "本文\n")
	write(t, filepath.Join(root, "docs", "long.md"), strings.Repeat("い", ctxfile.MaxChars+1))
	allowRead := rule("id", "r", "match", "Read", "glob", "*")
	denyPem := rule("id", "pem", "match", "Write", "glob", "*.pem", "message", "m")
	probes := []any{
		rule("id", "x", "match", "Write", "glob", "*.pem", "except", []any{rule("glob", "*")}, "message", "m"),
		rule("id", "push", "match", "Bash", "glob", "*git push*", "except", []any{rule("glob", "*--dry-run*")}, "message", "m"),
		rule("id", "push", "match", "Bash", "regex", `git\s+push\b`, "except", []any{rule("regex", `git\s+push\b[^\x00]*\s--dry-run(\s|$)`)}, "message", "m"),
		rule("id", "push", "match", "Bash", "regex", `git\s+push\b`, "except", []any{rule("regex", `git\s+push\b.*--dry-run`)}, "message", "m"),
		rule("id", "push", "match", "Bash", "regex", `git\s+push\b`, "except", []any{rule("regex", `git\s+push\b\S*\s--dry-run`)}, "message", "m"),
		rule("id", "push", "match", "Bash", "regex", `git\s+push\b`, "except", []any{rule("regex", `[^\x00]*\s--dry-run`)}, "message", "m"),
		rule("id", "push", "match", "PowerShell|Bash", "regex", `(^|\x00)git\s`, "except", []any{rule("regex", `(^|\x00)git\s[^\x00]*x`), rule("regex", "--force")}, "message", "m"),
		rule("id", "nudge", "match", "Stop", "glob", "*", "except", []any{rule("glob", "(stop)")}, "every", 5, "additionalContext", "x"),
		rule("id", "nudge", "match", "Stop|Bash", "glob", "*", "message", "m"),
		rule("id", "nudge", "match", "Stop", "glob", "x"),
		rule("id", "nudge", "match", "Stop", "glob", "*", "every", 10, "additionalContext", "x"),
		rule("id", "a", "match", "Write", "glob", "*", "message", "m"),
		rule("id", "a", "match", "Write", "regex", ".", "message", "m"),
		rule("id", "a", "match", "Write", "regex", "", "message", "m"),
		rule("id", "a", "match", "Write", "regex", "[^\\w./ -]", "message", "m"),
		rule("id", "a", "match", "Write", "regex", ".*", "except", []any{rule("glob", "*.md")}, "message", "m"),
		rule("id", "a", "match", "Read|Foo|Stop", "glob", "*.md", "additionalContext", "見る", "every", 2),
		rule("id", "a", "match", "Bash", "regex", "ls|cat|sed", "additionalContext", "x"),
		rule("id", "a", "match", "Bash", "regex", "[ls|cat|sed]", "additionalContext", "x"),
		rule("id", "a", "match", "Bash", "glob", "*", "additionalContext", "x"),
		rule("id", "a", "match", "Bash", "when", rule("not", rule("regex", "^rm")), "additionalContext", "x"),
		rule("id", "a", "match", "Bash", "when", rule("any", []any{rule("glob", "*"), rule("glob", "x")}), "additionalContext", "x"),
		rule("id", "a", "match", "Bash", "when", rule("all", []any{rule("glob", "*x*"), rule("not", rule("glob", "*x*"))}), "message", "m"),
		rule("id", "a", "match", "Bash", "when", rule("any", []any{rule("glob", "*x*"), rule("any", []any{rule("not", rule("glob", "*x*"))})}), "message", "m"),
		rule("id", "a", "match", "Bash", "when", rule("not", rule("not", rule("regex", ""))), "message", "m"),
		rule("id", "a", "match", "Bash", "when", rule("all", []any{rule("regex", ""), rule("not", rule("glob", "a"))}), "message", "m"),
		rule("id", "a", "match", "Bash", "every", 0, "glob", "x", "message", "m"),
		rule("id", "a", "match", "Bash", "every", "3", "glob", "x", "message", "m"),
		rule("id", "a", "match", "Bash", "every", 3, "glob", "x", "message", "m"),
		rule("id", "a", "match", "Bash", "every", 3, "glob", "x", "additionalContextOnceFile", "docs/a.md"),
		rule("id", "a", "match", "Read", "glob", "*.md", "message", "m"),
		rule("id", "a", "match", "Read", "glob", "*.md", "additionalContext", "sh .ccnavi/scripts/x.sh と ./.ccnavi/scripts/y と {root}/.ccnavi/scripts/z"),
		rule("id", "a", "match", "Read", "glob", "*.md", "additionalContextOnce", "{script:ccnavi-git.sh} {script:missing.sh} {script:bad name}"),
		rule("id", "a", "match", "Read", "glob", "*.md", "additionalContextFile", "/etc/passwd"),
		rule("id", "a", "match", "Read", "glob", "*.md", "additionalContextFile", "../x.md"),
		rule("id", "a", "match", "Read", "glob", "*.md", "additionalContextFile", `C:\x.md`),
		rule("id", "a", "match", "Read", "glob", "*.md", "additionalContextFile", "docs/none.md"),
		rule("id", "a", "match", "Read", "glob", "*.md", "additionalContextFile", "docs/long.md"),
		rule("id", "a", "match", "Read", "glob", "*.md", "additionalContextFile", "docs/a.md"),
		rule("id", "a", "match", "Write", "regex", "^{!root}", "message", "m"),
	}
	var cases []lintCase
	add := func(name string, doc map[string]any, c lintCase) {
		data, _ := json.Marshal(doc)
		path := filepath.Join(root, "rules", name+".yml")
		write(t, path, string(data))
		c.Path = path
		cases = append(cases, c)
	}
	for i, p := range probes {
		for _, section := range []string{"deny", "ask", "allow"} {
			doc := map[string]any{"version": 1, "deny": []any{denyPem}, "ask": []any{}, "allow": []any{allowRead}}
			doc[section] = append(doc[section].([]any), p, p)
			name := section + "-" + strings.Repeat("x", i%3) + string(rune('a'+i%26)) + string(rune('a'+i/26))
			add(name, doc, lintCase{Root: root})
			add(name+"-layer", doc, lintCase{Root: root, Layer: true, Project: true, Home: filepath.Join(root, "docs")})
			add(name+"-noroot", doc, lintCase{})
		}
	}
	add("empty", map[string]any{"version": 1}, lintCase{Root: root})
	cases = append(cases, lintCase{Path: filepath.Join(root, "none.yml"), Root: root})
	write(t, filepath.Join(root, "bad.yml"), "deny: [\n")
	cases = append(cases, lintCase{Path: filepath.Join(root, "bad.yml"), Root: root})
	write(t, filepath.Join(root, "list.yml"), "- 1\n")
	cases = append(cases, lintCase{Path: filepath.Join(root, "list.yml"), Root: root})
	write(t, filepath.Join(root, "bin.yml"), "\xff\xfe")
	cases = append(cases, lintCase{Path: filepath.Join(root, "bin.yml"), Root: root})
	want := golden.Load[[][]string](t, "file_problems")
	bad := 0
	for i, c := range cases {
		got := problemsOf(c, root)
		if strings.HasSuffix(c.Path, "bad.yml") && len(got) == 1 && len(want[i]) == 1 {
			// YAML の読み手の文面は infra/yaml が持つ（見本を取った環境では libyaml の文になることがある）。頭だけを比べる。
			head := func(s string) string { return s[:strings.Index(s, "読めない: ")] }
			got[0], want[i][0] = head(got[0]), head(want[i][0])
		}
		gj, _ := json.Marshal(got)
		wj, _ := json.Marshal(want[i])
		if !bytes.Equal(gj, wj) {
			bad++
			if bad <= 10 {
				text, _ := os.ReadFile(c.Path)
				t.Errorf("%+v\n%s\n got  %s\n want %s", c, text, gj, wj)
			}
		}
	}
	if bad > 0 {
		t.Errorf("%d / %d 件が見本と違う", bad, len(cases))
	}
}
