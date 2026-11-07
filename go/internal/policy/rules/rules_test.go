package rules

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
)

func parseText(t *testing.T, text, root string) (*RuleSet, []Problem) {
	t.Helper()
	data, err := yaml.Load(text)
	if err != nil {
		t.Fatal(err)
	}
	return Parse(data.(*yaml.Map), root)
}

func one(t *testing.T, section, body, root string) (*Rule, []Problem) {
	t.Helper()
	rs, problems := parseText(t, "version: 1\n"+section+":\n  - "+strings.ReplaceAll(body, "\n", "\n    ")+"\n", root)
	list := *rs.Section(Decision(section))
	if len(list) == 0 {
		return nil, problems
	}
	return list[0], problems
}

// glob も regex も、どの機械でも大文字小文字を区別せずに当てる。区切りは "/" と "\" の両方に当てる。
func TestMatchingIgnoresCaseAndSeparator(t *testing.T) {
	rule, problems := one(t, "deny", "id: guard\nmatch: Write|Edit\nglob: '*/.ccnavi/*'\nmessage: m", "/r")
	if rule == nil || len(problems) > 0 {
		t.Fatal(problems)
	}
	for subject, want := range map[string]bool{
		"/a/.Ccnavi/config/project/rules.yml": true,
		`C:\a\.CCNAVI\x`:                      true,
		"/a/ccnavi/x":                         false,
		"/a/.ccnavi":                          false,
	} {
		if got := rule.Matches("Write", subject); got != want {
			t.Errorf("%q: %v", subject, got)
		}
	}
	if rule.Matches("write", "/a/.ccnavi/x") || rule.Matches("Read", "/a/.ccnavi/x") || rule.Matches("Write", "") {
		t.Error("ツール名は区別して完全一致、空の対象には当てない")
	}
	if !ToolMatches(" Bash | Write", "Write") || ToolMatches("Bash|Write", "Writ") {
		t.Error("ToolMatches")
	}
}

// {root} は実パスの区切りを両方に当て、{!root} は「ルートで始まらない」を先読みなしに書く。
func TestRootPlaceholders(t *testing.T) {
	root := "/nonexistent/Users/taniyama/ccnavi"
	rule, problems := one(t, "deny", "id: outside\nmatch: Write\nregex: '^{!root}'\nmessage: m", root)
	if rule == nil || len(problems) > 0 {
		t.Fatal(problems)
	}
	for subject, outside := range map[string]bool{
		root:                                 false, // ルートそのものは中
		root + "/README.md":                  false,
		strings.ToUpper(root) + `\x.md`:      false, // 大文字小文字・区切りの違いは中
		strings.ReplaceAll(root, "i", "İ"):   false, // İ・ı は i と同じに数える（Python の re と同じ。既知の例外）
		strings.ReplaceAll(root, "i", "ı"):   false,
		strings.ReplaceAll(root, "i", "j"):   true,
		root + "-fork/x.md":                  true,
		filepath.Dir(root):                   true, // ルートより上
		root[:len(root)-1]:                   true,
		"/elsewhere/x":                       true,
		"/nonexistent/Users/taniyama/ｃcnavi": true, // 全角は別の字
	} {
		if got := rule.Matches("Write", subject); got != outside {
			t.Errorf("%q: 外=%v", subject, got)
		}
	}
	if !strings.HasPrefix(rule.Compiled.Pattern(), `^(?:\Z|[^\\/]|[\\/](?:\Z|[^n]|n(?:`) || generatedQuantifier(notRootPattern(realRoot(root))) {
		t.Error(rule.Compiled.Pattern())
	}
	in, _ := one(t, "deny", "id: in\nmatch: Write\nregex: '^{root}[\\\\/]wip'\nmessage: m", root)
	if !in.Matches("Write", root+`\WIP\x`) || in.Matches("Write", "/other/wip") {
		t.Error(in.Compiled.Pattern())
	}
}

// {!root} を組み立てられないとき、deny・ask は match の全部を止め、allow は使わない。書いた文面は残す。
func TestUngeneratableNotRoot(t *testing.T) {
	long := "/nonexistent/" + strings.Repeat("a", maxRootLen)
	for _, section := range []string{"deny", "ask"} {
		rule, problems := one(t, section, "id: outside\nmatch: Write\nregex: '^{!root}'\nmessage: 書いた文", long)
		if rule == nil || len(problems) != 1 || problems[0].Severity != Error {
			t.Fatalf("%s: %v", section, problems)
		}
		if _, said := rule.Speech(); !rule.Matches("Write", long+"/x") || rule.Message != "書いた文" || !strings.Contains(said, "長すぎて") {
			t.Errorf("%s: %q", section, said)
		}
	}
	if rule, problems := one(t, "allow", "id: outside\nmatch: Write\nregex: '^{!root}'", long); rule != nil || len(problems) != 1 {
		t.Errorf("allow を使った: %v", problems)
	}
	if rule, problems := one(t, "deny", "id: x\nmatch: Write\nregex: '^{!root}'\nmessage: m", "/"); rule == nil || !strings.Contains(problems[0].Detail, "区切りだけ") {
		t.Errorf("%v", problems)
	}
}

func TestRecordNameAndID(t *testing.T) {
	rs, problems := parseText(t, "version: 1\ndeny:\n  - {match: Write, glob: '*', message: m}\n  - {id: '  ', match: Write, glob: '*', message: m}\n  - {id: ok, match: Write, glob: '*', message: m}\n", "")
	if len(rs.Deny) != 1 || len(problems) != 2 || problems[0].Rule != "deny[0]" || problems[1].Rule != "deny[1]" {
		t.Fatalf("%v", problems)
	}
	rule := rs.Deny[0]
	rule.File = ".ccnavi/config/common/rules.yml"
	if RecordName(rule) != ".ccnavi/config/common/rules.yml:ok" {
		t.Error(RecordName(rule))
	}
	rule.FromFile = false
	if RecordName(rule) != "ok" {
		t.Error(RecordName(rule))
	}
}

func TestBuiltin(t *testing.T) {
	list, problems := Builtin(Deny, []Spec{
		{ID: "builtin:git-push", Match: "Bash", Regex: `git\s+push`, Text: TextRef{ID: "RULE_GIT_PUSH"}, Exempt: strings.ToUpper},
		{ID: "builtin:bad", Match: "Bash", Regex: "("},
		{ID: "builtin:nothing", Match: "Bash", Regex: "x"},
	}, "")
	if len(list) != 1 || len(problems) != 2 {
		t.Fatalf("%d %v", len(list), problems)
	}
	r := list[0]
	if r.FromFile || r.Exempt == nil || r.Text.ID != "RULE_GIT_PUSH" || !r.Matches("Bash", "git push") {
		t.Errorf("%+v", r)
	}
	// ルールファイルからは組み込みの名前も文面ファイルの指定も書けない。
	rule, problems := one(t, "deny", "id: Builtin:x\nmatch: Write\nglob: '*'\nmessage: m", "")
	if rule != nil || !strings.Contains(problems[0].Detail, "組み込みルールの名前") {
		t.Errorf("%v", problems)
	}
	rule, _ = one(t, "deny", "id: x\nmatch: Write\nglob: '*'\nmessage: m\nmessage_id: RULE_X", "")
	if rule.Text.ID != "" {
		t.Error("ルールファイルの message_id を読んだ")
	}
}

func TestLoadErrors(t *testing.T) {
	dir := t.TempDir()
	write := func(name, text string) string {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
		return path
	}
	for path, want := range map[string]string{
		filepath.Join(dir, "none.yml"): "[Errno 2] No such file or directory: '" + filepath.Join(dir, "none.yml") + "'",
		write("bin.yml", "\xff"):       "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte",
		write("list.yml", "- 1\n"):     filepath.Join(dir, "list.yml") + " のルールがマッピングではない",
	} {
		if _, _, err := Load(path, ""); err == nil || err.Error() != want {
			t.Errorf("%s: %v", path, err)
		}
	}
	rs, problems, err := Load(write("ok.yml", "version: 1\ndeny:\n  - {id: a, match: Write, glob: '*', message: m}\n"), "")
	if err != nil || len(problems) != 0 || len(rs.Deny) != 1 {
		t.Errorf("%v %v", err, problems)
	}
}

func TestCompileCondition(t *testing.T) {
	re, why := CompileCondition("", `ccnavi-git\.sh\s+integrate(?:\s|$)`, "gate")
	if re == nil || !re.Search("sh CCNAVI-GIT.SH integrate") {
		t.Fatal(why)
	}
	if re, why := CompileCondition("{root}/x", "", "gate"); re != nil || !strings.Contains(why, "ワークスペースルートが必要") {
		t.Error(why)
	}
	if re, why := CompileCondition("", "a(?=b)", "gate"); re != nil || why != "先読みは利用できない" {
		t.Error(why)
	}
}

func TestFillAndScripts(t *testing.T) {
	root := "/nonexistent/w s"
	got := FillText("{root}/x と {script:ccnavi-git.sh} と {script:a b}", root)
	want := root + "/x と " + config.ScriptCommand(root, "ccnavi-git.sh") + " と {script:a b}"
	if got != want || FillText("{root}", "") != "{root}" {
		t.Errorf("%q", got)
	}
	for text, relative := range map[string]bool{
		".ccnavi/scripts/x.sh":                     true,
		"sh ../.ccnavi/scripts/x":                  true,
		"(./.ccnavi/scripts/x":                     true,
		"{root}/.ccnavi/scripts/x":                 false,
		"$HOME/.ccnavi/scripts/x":                  false,
		"~/.ccnavi/scripts/x":                      false,
		"a.ccnavi/scripts/x":                       false,
		"日.ccnavi/scripts/x":                       true,
		"/w/.ccnavi/scripts/x と .ccnavi/scripts/y": true,
	} {
		if HasRelativeScripts(text) != relative {
			t.Errorf("%q", text)
		}
	}
}

func TestReadableEvery(t *testing.T) {
	for _, c := range []struct {
		written any
		want    int
	}{{nil, 1}, {5, 5}, {0, 0}, {-1, 0}, {true, 0}, {"5", 0}, {2.0, 0}} {
		if got := ReadableEvery(c.written); got != c.want {
			t.Errorf("%v: %d", c.written, got)
		}
	}
}

// glob は先頭に固定するので、長い対象でも照合が 2 乗にならない（#241 の回帰）。
func TestGlobIsLinear(t *testing.T) {
	rule, _ := one(t, "deny", "id: push\nmatch: Bash\nglob: '*git push*'\nmessage: m", "")
	subject := strings.Repeat("a", 200000)
	start := time.Now()
	if rule.Matches("Bash", subject) {
		t.Fatal("当たった")
	}
	if took := time.Since(start); took > time.Second {
		t.Errorf("%v かかった", took)
	}
}

func TestSubjectOf(t *testing.T) {
	cwd := t.TempDir()
	in := func(tool string, kv ...any) hookio.Input {
		return hookio.Input{ToolName: tool, ToolInput: pyjson.NewObject(kv...), Cwd: cwd}
	}
	real, _ := filepath.EvalSymlinks(cwd)
	for _, c := range []struct {
		in   hookio.Input
		want string
	}{
		{in("Bash", "command", "ls"), "ls"},
		{in("Write", "file_path", "a.md"), filepath.Join(real, "a.md")},
		{in("NotebookEdit", "file_path", "n.ipynb"), filepath.Join(real, "n.ipynb")},
		{in("Grep"), real},
		{in("Agent", "prompt", "やること"), "やること"},
		{in("Task"), "(agent)"},
		{in("WebSearch", "query", "x"), ""},
		{in("Bash", "command", 3), ""},
	} {
		if got := SubjectOf(c.in); got != c.want {
			t.Errorf("%s: %q, want %q", c.in.ToolName, got, c.want)
		}
	}
}
