package learning

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/wiki"
)

// 決めた入力を読ませ、記録の読み取り・整合の検査・節の文・追記・コマンドの語・要約の JSON・
// HEAD の記録から処理すべきものの列挙が 1 字も違わないことを見る。

type textAnswers struct {
	Parse    [][]any  `json:"parse"`
	Entry    []string `json:"entry"`
	Append   []string `json:"append"`
	Words    []string `json:"words"`
	Validate []string `json:"validate"`
	Digest   string   `json:"digest"`
	Whole    string   `json:"whole"`
}

const head = "---\nticket: c-01-01\nparent: c\nlearnings: %s\n---\n\n"

func rec(n string, sections ...string) string {
	return strings.Replace(head, "%s", n, 1) + strings.Join(sections, "")
}

func sec(title, process string) string {
	return "## " + title + "\n\n- 種類: 未定\n- 内容: x\n" + process + "\n"
}

var parseCases = []string{
	"", "learnings: none\n", "---\nticket: x\n---\n", "---\nlearnings: [\n---\n", "---\nlearnings: many\n---\n",
	"---\nlearnings: -1\n---\n", "---\nlearnings: true\n---\n", "---\n- a\n---\n", "---\nlearnings: none\n",
	"---\nticket: x\nlearnings:\n  - 気づき\n---\n", "---\nlearnings: {a: 1}\n---\n", "---\nlearnings: 1.5\n---\n",
	"---\nlearnings: null\n---\n", "---\nlearnings: 2026-01-01\n---\n", "---\nlearnings: 0x10\n---\n",
	"---\nlearnings: 99999999999999999999\n---\n",
	"\uFEFF---\r\nlearnings: 0\r\n---\r\n",
	rec("none"), rec("0"), rec("none", sec("a", "")),
	rec("2", sec("a", ""), sec("b", "")), rec("3", sec("a", "")),
	rec("1", sec("a", "- 処理: 反映 p\n- 処理: 対象外 y")), rec("1", sec("a", "- 処理: たぶん")),
	rec("1", sec("a", "- 処理: 反映 pat")), rec("1", sec("a", "- 処理: 対象外 環境の都合")),
	rec("1", sec("a", "- 処理:　対象外　環境の都合　")), rec("1", sec("a", "- 処理: issue #12 理由")),
	rec("1", sec("a", "- 処理: 反映\u00a0pat")), rec("1", sec("a", "- 処理: 対象外 理由\u00a0")),
	rec("1", "## a\n\n- 内容: x\n  - 処理: 反映 pat\n"),
	rec("1", "## a\n\n- 信号: 誤り（仕様バグ）\n- 信号:\u00a0x\n"), rec("1", "## a\n- 信号:　落とし穴 です\n"),
	rec("1", "##  空白の見出し  \n"),
}

var entryCases = []string{
	"本文1", "テストの気づき\n2 行目\n\n4 行目", "本当の気づき\r\r## 偽の気づき\r\r- 種類: rule",
	"本当 ## 偽 - 種類: rule\x85## 偽2", "a\u2028b\u2029c\x00d\x1be\x7f", "  \n  前後の空白  \n ",
	strings.Repeat("あ", 3000), strings.Repeat("い", 70) + "\n2 行目",
	"接続に使った ghp_0123456789abcdefghijklmnopqrstuvwxyz を忘れない",
}

var appendCases = []string{
	"", "  \n", "---\nticket: a\nlearnings: none\n---\n", "---\nticket: a\nlearnings: none\n---\n\n## x\n",
	"\uFEFF---\r\nlearnings: 3\r\n---\r\nbody", "本文だけ\n", "---\nticket: a\n---\n", "---\nlearnings: 1\n",
}

var wordCases = []string{
	`DB_PASSWORD="my secret pw" psql`, "TOKEN='abc def' curl x", `PASS=abc\ def cmd`,
	"FOO=1 BAR=$(cat /home/alice/.secret) cmd", `"/home/alice/My Docs/run.sh" a`, "env -i HOME=/x git push",
	"FOO=bar env /usr/bin/git push", "/usr/bin/git push", `C:\tools\make.exe all`, "a;b c", "'unclosed",
	`trail\`, `"a\"b" c`, `'' x`, `"a\$b"`, "FOO=1", "", "   ", "ｇｉｔ push",
}

func digestRecords() []any {
	deny := func(tree, subject string) map[string]any {
		return map[string]any{"decision": "deny", "enforced": true, "rules": []any{"r1"}, "tool": "Bash",
			"subject": subject, "tree": tree}
	}
	return []any{
		deny("t1", "/usr/bin/git push"), deny("t1", "git status"), deny("t2", "git push"), deny("t1", "DB=x psql"),
		deny("t1", "DB=x psql"), map[string]any{"decision": "ask", "tree": "t1"}, map[string]any{"decision": "nudge"},
		map[string]any{"decision": "handover", "tree": "t1"},
		map[string]any{"decision": "deny", "enforced": true, "rules": []any{"r2"}, "tool": "Edit", "tree": "t1"},
		map[string]any{"decision": "deny", "enforced": true, "rules": []any{"r2"}, "tool": "Edit", "tree": "t1"},
		map[string]any{"decision": "deny", "enforced": false, "rules": []any{"r2"}, "tool": "Edit", "tree": "t1"},
		map[string]any{"decision": "deny", "enforced": 1, "rules": []any{3}, "tool": "Edit", "tree": "t1"},
		map[string]any{"decision": "deny", "enforced": "yes", "rules": []any{"r3"}, "tool": 7, "tree": "t1"},
		map[string]any{"decision": "deny", "enforced": "yes", "rules": []any{"r3"}, "tool": 7, "tree": "t1"},
		map[string]any{"decision": "deny", "enforced": true, "rules": []any{}, "tree": "t1"},
	}
}

func TestMatchesGolden(t *testing.T) {
	validateCases := [][]string{{"", "", ""}, {"  ", "", ""}, {"本文", "x", ""}, {"本文", "", "x"}, {"本文", "skill", "訂正"}, {"本文", "docs", "誤り"}}
	records := digestRecords()
	want := golden.Load[textAnswers](t, "text")

	for i, text := range parseCases {
		got := []any{"", "", []any{}}
		r, why := parse("c-01-01", text)
		if r == nil {
			got[1] = why
		} else {
			got[0] = consistencyProblem(r)
			var secs []any
			for _, s := range r.sections {
				kind, rest, ok := s.process()
				process := []any{}
				if ok {
					process = []any{kind, rest}
				}
				secs = append(secs, []any{s.title, s.signal, process})
			}
			if secs != nil {
				got[2] = secs
			}
		}
		if !reflect.DeepEqual(got, want.Parse[i]) {
			t.Errorf("parse %q: %#v, Python %#v", text, got, want.Parse[i])
		}
	}
	for i, body := range entryCases {
		if got := entryText(body, "", "", "2026-01-01T00:00:00+0900"); got != want.Entry[i] {
			t.Errorf("entryText %q:\n%q\nPython\n%q", body, got, want.Entry[i])
		}
	}
	for i, text := range appendCases {
		got, err := appendEntry(text, "## t\n\n- 内容: x\n", "c-01-01", "c")
		if err != nil {
			got = "error: " + err.Error()
		}
		if got != want.Append[i] {
			t.Errorf("appendEntry %q:\n%q\nPython\n%q", text, got, want.Append[i])
		}
	}
	for i, subject := range wordCases {
		if got := commandWord(subject); got != want.Words[i] {
			t.Errorf("commandWord %q = %q, Python %q", subject, got, want.Words[i])
		}
	}
	for i, v := range validateCases {
		if got := validate(v[0], v[1], v[2]); got != want.Validate[i] {
			t.Errorf("validate %q = %q, Python %q", v, got, want.Validate[i])
		}
	}
	objects := asObjects(t, records)
	tree := "t1"
	for _, c := range []struct {
		name     string
		tree     *string
		recorded bool
		want     string
	}{{"digest", &tree, true, want.Digest}, {"whole", nil, false, want.Whole}} {
		got, err := pyjson.Dumps(buildDigest(objects, c.recorded, c.tree), pyjson.Options{Indent: 1})
		if err != nil || got != c.want {
			t.Errorf("%s:\n%s\nPython\n%s (%v)", c.name, got, c.want, err)
		}
	}
}

// asObjects は JSON を通して、ログから読んだのと同じ形の値にする。
func asObjects(t *testing.T, records []any) []*pyjson.Object {
	t.Helper()
	var out []*pyjson.Object
	for _, r := range records {
		text, err := pyjson.Dumps(r, pyjson.Options{SortKeys: true})
		if err != nil {
			t.Fatal(err)
		}
		v, err := pyjson.Loads(text)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, v.(*pyjson.Object))
	}
	return out
}

type findAnswers struct {
	Items        [][]string `json:"items"`
	Unreadable   [][]string `json:"unreadable"`
	Inconsistent [][]string `json:"inconsistent"`
	Uncommitted  []string   `json:"uncommitted"`
}

func TestFindMatchesGolden(t *testing.T) {
	root := repo(t)
	pattern := func(name, sources string) {
		put(t, root, "docs/wiki/patterns/"+name+".md", "---\ntype: learning-wiki\ntarget: docs\nsources: "+sources+"\n---\n\n本文\n")
	}
	pattern("pat", "[c-01-01, other]")
	pattern("one", "[c-01-01, C-01-01]")
	pattern("Foo", "[C-01-02, other]")
	pattern("miss", "[x, y]")
	put(t, root, "docs/wiki/patterns/bad.md", "---\nsources: [c-01-01]\n---\n")
	put(t, root, LearningDir+"/c-01-01.md", rec("9",
		sec("無い", ""), sec("反映", "- 処理: 反映 pat"), sec("1 件", "- 処理: 反映 one"), sec("無いパターン", "- 処理: 反映 nope"),
		sec("壊れたパターン", "- 処理: 反映 bad"), sec("外", "- 処理: 反映 ../x"), sec("観測元", "- 処理: 反映 miss"),
		sec("理由なし", "- 処理: 対象外"), "## 誤り\n\n- 信号: 誤り\n- 処理: 対象外 環境\n"))
	put(t, root, LearningDir+"/c-01-02.md", rec("4", sec("大小", "- 処理: 反映 foo"), sec("issue", "- 処理: issue 12 理由"),
		sec("issue だけ", "- 処理: issue 12"), sec("全角", "- 処理:　対象外　環境の都合")))
	put(t, root, LearningDir+"/bad.md", "no frontmatter\n")
	put(t, root, LearningDir+"/none.md", rec("none", sec("a", "")))
	put(t, root, LearningDir+"/notes.txt", "x\n")
	if err := os.WriteFile(filepath.Join(root, LearningDir, "sjis.md"), []byte{0x82, 0xa0}, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("none.md", filepath.Join(root, LearningDir, "link.md")); err != nil {
		t.Skip("シンボリックリンクを作れない")
	}
	put(t, root, wiki.InboxDir+"/.gitkeep", "")
	put(t, root, wiki.InboxDir+"/note.txt", "x\n")
	put(t, root, wiki.InboxDir+"/NOTE.MD", "x\n")
	commit(t, root)
	put(t, root, LearningDir+"/c-01-01.md", "書きかけ\n")
	put(t, root, "docs/wiki/patterns/new.md", "x\n")
	put(t, root, "src/other.go", "x\n")

	want := golden.Load[findAnswers](t, "find")
	found := Find(root)
	got := findAnswers{Uncommitted: Uncommitted(root)}
	for _, i := range found.Items {
		got.Items = append(got.Items, []string{i.path, i.title, i.why, i.Describe()})
	}
	for _, p := range found.unreadable {
		got.Unreadable = append(got.Unreadable, []string{p.path, p.why})
	}
	for _, p := range found.inconsistent {
		got.Inconsistent = append(got.Inconsistent, []string{p.path, p.why})
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Find:\n%#v\nPython\n%#v", got, want)
	}
}
