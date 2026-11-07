package fields

import (
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/tickets/model/ticket"
)

// Python の ticket_fields と突き合わせる。行単位の書き換えは、ユーザのコメントと `|` のブロックを残すことと、
// スクリプトが書いた直後の内容が正規化で元と同じになること（実行後チェックの約束）が要。

type fieldsAnswers struct {
	Set    []string   `json:"set"`
	MR     [][]string `json:"mr"`
	Int    []int      `json:"int"`
	Empty  []bool     `json:"empty"`
	Held   [][]string `json:"held"`
	Shape  [][]any    `json:"shape"`
	Quoted []string   `json:"quoted"`
}

var fieldsTexts = []string{
	"no frontmatter",
	"---\nticket: a\n",
	"---\nticket: a\n---\n本文\n",
	"---\nticket: a\nstarted_at: \"\"\nbase_sha: ''\nmr:\n# c\n---\n",
	"---\nticket: a\nstarted_at: 2026-01-01\nbase_sha: abc\nmr: 12\n---\n本文",
	"---\nticket: a\nmerged_sha: |\n  abc\n  def\nrationale: |\n  started_at: 1\nmr: \"!12\"\n---\n",
	"---\nticket: a\n  started_at: x\ncancel_reason: [unclosed\nmr: '#13'\n---\n",
	"---\nticket: a\nfinished_to: null\nsettled_by: ~\nmr: 12 # c\n---\n",
	"---\r\nticket: a\r\nmr: 0\r\n---\r\n",
	"---\nticket: a\nmr: 12\n  more\nstarted_at: " + strings.Repeat("x", 1100) + "\n---\n",
	"---\nmr: true\n---\n",
	"---\nmr: ０１２\n---\n",
}

var fieldsWrites = [][2]string{
	{"started_at", "2026-11-06T10:00:00+0900"}, {"base_sha", "abc"}, {"title", "書かない"},
	{"cancel_reason", strings.Repeat("理由 ", 30)}, {"settled_by", "user\n\"x\""},
}

var quotedValues = []string{
	"", "abc", "a\"b\\c", "tab\there\nnl\r", "\x00\x07\x1b\u0085   \ufeff￾\U0001F600é",
	strings.Repeat("a", 100), strings.Repeat("a ", 60), strings.Repeat("あいう ", 30), strings.Repeat("x\n", 50),
	" lead and trail ", strings.Repeat("ab", 39) + "  c d",
}

func TestFieldsMatchPython(t *testing.T) {
	want := golden.Load[fieldsAnswers](t, "fields")
	got := fieldsAnswers{}
	for _, text := range fieldsTexts {
		got.Set = append(got.Set, SetFields(text, fieldsWrites))
		for _, n := range []int{12, 13} {
			out, r := SetMR(text, n)
			got.MR = append(got.MR, []string{out, string(r)})
		}
		n, _ := PlainIntField(text, MRKey)
		got.Int = append(got.Int, n)
		got.Empty = append(got.Empty, MREmpty(text))
		held := ScriptFieldsSet(text)
		if held == nil {
			held = []string{}
		}
		got.Held = append(got.Held, held)
		for _, drop := range [][]string{ticket.ScriptFields, {"base_sha"}, nil} {
			s, ok := ScriptShape(text, drop)
			got.Shape = append(got.Shape, []any{ok, s})
		}
	}
	for _, v := range quotedValues {
		got.Quoted = append(got.Quoted, yaml.Quoted(v))
	}
	for _, f := range []struct {
		name      string
		got, want any
	}{
		{"set", got.Set, want.Set}, {"mr", got.MR, want.MR}, {"int", got.Int, want.Int}, {"empty", got.Empty, want.Empty},
		{"held", got.Held, want.Held}, {"shape", got.Shape, want.Shape}, {"quoted", got.Quoted, want.Quoted},
	} {
		if !reflect.DeepEqual(f.got, f.want) {
			t.Errorf("%s\n got  %q\n want %q", f.name, f.got, f.want)
		}
	}
}

// スクリプトの欄を書いた直後の内容は、書いた欄を除いて正規化すると元と同じ（実行後チェックがサブコマンドの
// 書き込みを内容で見分ける約束）。
func TestShapeIgnoresWhatTheScriptWrote(t *testing.T) {
	before := "---\nticket: a\nallow:\n  - glob: x\nstarted_at: \"\"\n---\n本文\n"
	after := SetFields(before, [][2]string{{"started_at", "t"}, {"merged_sha", "m"}})
	a, _ := ScriptShape(before, ticket.ScriptFields)
	b, _ := ScriptShape(after, ticket.ScriptFields)
	if a != b {
		t.Fatalf("%q != %q", a, b)
	}
	changed, _ := ScriptShape(strings.Replace(after, "glob: x", "glob: y", 1), ticket.ScriptFields)
	if changed == a {
		t.Fatal("範囲が変わっても同じ内容に見える")
	}
}
