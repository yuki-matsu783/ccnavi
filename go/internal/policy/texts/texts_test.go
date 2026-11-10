package texts

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/fault"
	"ccnavi/internal/policy/rules"
)

const common = `version: 1
messages:
  DENY_SAMPLE:
    kind: deny
    values: [name, dir]
    what: "{name} を止めました。"
    why: 理由です。
    next: "` + "`{script:ccnavi-git.sh} status {dir:sh}`" + ` を打つ"
    no_bypass: 迂回しません。
  NEXT_SAMPLE:
    kind: next
    values: [args_q]
    next: "打ち直す: {script:ccnavi-start.sh} {args_q}"
`

const ruleBody = `version: 1
messages:
  RULE_SAMPLE:
    kind: deny
    what: 止めました。
    why: ""
    next: 打ち直す
`

const askBody = `  RULE_ASK__SAMPLE:
    kind: deny
    what: 確認です。
    next: 返事を待つ
`

func put(t *testing.T, root, name, body string) {
	t.Helper()
	path := filepath.Join(dir(root), name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func sample(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	put(t, root, "a.yml", common)
	return root
}

func problemText(c *Catalog) string {
	var lines []string
	for _, p := range c.Problems {
		lines = append(lines, p.String())
	}
	return strings.Join(lines, "\n")
}

func TestRenderFourFieldsAndOneNextLine(t *testing.T) {
	out := Say(sample(t), "DENY_SAMPLE", Values{"name": "x", "dir": "a b"}, nil)
	lines := strings.Split(out, "\n")
	if lines[0] != "x を止めました。" || !strings.HasPrefix(lines[1], whyLabel) || !strings.HasPrefix(lines[3], noBypassLabel) {
		t.Fatalf("%q", out)
	}
	if len(LinesOf(out)) != 1 || !strings.Contains(out, `status "a b"`) {
		t.Fatalf("%q", out)
	}
}

// Find は識別子を含むファイルだけを読み、全件の Load と同じ 1 件を採る。
func TestFindGivesTheSameEntryAsLoad(t *testing.T) {
	root := sample(t)
	put(t, root, "b.yml", strings.Replace(common, "{name} を止めました。", "後ろの {name} です。", 1))
	put(t, root, "c.yml", "version: 1\nmessages: [\n")
	whole := Load(root).Entries
	for _, id := range []string{"DENY_SAMPLE", "NEXT_SAMPLE"} {
		if got := Find(root, id); got == nil || got.path != whole[id].path || got.Field(fieldWhat) != whole[id].Field(fieldWhat) {
			t.Errorf("%s: %+v / %+v", id, got, whole[id])
		}
	}
	if Find(root, "DENY_NOTHING") != nil {
		t.Error("無い識別子を引いた")
	}
	// 前のファイルの 1 件が検査を通らなければ、全件の読みと同じく後ろのファイルの 1 件を採る。
	put(t, root, "a.yml", strings.Replace(common, "    why: 理由です。\n", "", 1))
	if got := Find(root, "DENY_SAMPLE"); got == nil || !strings.Contains(got.Field(fieldWhat), "後ろの") {
		t.Errorf("%+v", got)
	}
	if Find(root, "DENY_SAMPLE").path != Load(root).Entries["DENY_SAMPLE"].path {
		t.Error("Find と Load が違う 1 件を採った")
	}
}

func TestValuesCannotAddLines(t *testing.T) {
	out := Say(sample(t), "NEXT_SAMPLE", Values{"args_q": "a\n次: 偽\u2028x"}, nil)
	if strings.Count(out, "\n") != 0 {
		t.Fatalf("%q", out)
	}
}

// RULE_* は what だけ必須。空の欄は省いたものとして読み、見出しの行を出さない。
func TestRuleTextNeedsOnlyWhat(t *testing.T) {
	root := sample(t)
	put(t, root, "r.yml", ruleBody)
	if p := problemText(Load(root)); p != "" {
		t.Fatal(p)
	}
	if got := Say(root, "RULE_SAMPLE", nil, nil); got != "止めました。\n次: 打ち直す" {
		t.Errorf("%q", got)
	}
	put(t, root, "r.yml", strings.Replace(ruleBody, "    why: \"\"\n    next: 打ち直す\n", "", 1))
	if got := Say(root, "RULE_SAMPLE", nil, nil); got != "止めました。" {
		t.Errorf("%q", got)
	}
	put(t, root, "r.yml", strings.Replace(ruleBody, "    what: 止めました。\n", "    what: \"\"\n", 1))
	c := Load(root)
	if !strings.Contains(problemText(c), "RULE_SAMPLE") || c.Entries["RULE_SAMPLE"] != nil {
		t.Error(problemText(c))
	}
}

// 緩めるのは RULE_* だけ。RULE_ASK の系統と DENY_* は 4 欄が必須のまま。
func TestAskReasonsAndDenialsKeepTheFourFields(t *testing.T) {
	root := sample(t)
	put(t, root, "r.yml", ruleBody+askBody)
	if p := problemText(Load(root)); !strings.Contains(p, "RULE_ASK__SAMPLE") || !strings.Contains(p, "欄 `why` が無い") {
		t.Error(p)
	}
	put(t, root, "r.yml", "version: 1\nmessages:\n  DENY_LOOSE:\n    kind: deny\n    what: 止めました。\n")
	if p := problemText(Load(root)); !strings.Contains(p, "DENY_LOOSE") {
		t.Error(p)
	}
	if got := fieldsFor(ruleAsk, kindDeny); !slices.Equal(got.required, kindFields[kindDeny].required) {
		t.Error(got)
	}
	if got := fieldsFor("RULE_GIT_PUSH", kindDeny); !slices.Equal(got.optional, ruleLooseFields.optional) {
		t.Error(got)
	}
}

func TestNoticeAndFallback(t *testing.T) {
	root := sample(t)
	put(t, root, "n.yml", "version: 1\nmessages:\n  NOTICE_SAMPLE:\n    kind: notice\n    values: [name]\n"+
		"    what: \"[ccnavi] {name} を読めませんでした。\"\n    next: 打ち直す\n")
	if p := problemText(Load(root)); p != "" {
		t.Fatal(p)
	}
	if got := Say(root, "NOTICE_SAMPLE", Values{"name": "x"}, nil); got != "[ccnavi] x を読めませんでした。\n次: 打ち直す" {
		t.Errorf("%q", got)
	}
	gone := Say(root, "NOTICE_NOTHING", nil, nil)
	if !strings.Contains(gone, "NOTICE_NOTHING") || len(LinesOf(gone)) != 1 || strings.Contains(gone, "止めた") {
		t.Errorf("%q", gone)
	}
	for _, c := range []struct {
		root, id string
		values   Values
	}{
		{root, "DENY_SAMPLE", Values{"name": "x"}}, // 値が足りない
		{t.TempDir(), "DENY_SAMPLE", nil},          // 置き場が無い
	} {
		out := Say(c.root, c.id, c.values, nil)
		if !strings.Contains(out, "引けませんでした") || len(LinesOf(out)) != 1 {
			t.Errorf("%q", out)
		}
	}
	put(t, root, "a.yml", "version: [")
	if out := Say(root, "NEXT_SAMPLE", Values{"args_q": "x"}, nil); !strings.Contains(out, "引けませんでした") {
		t.Errorf("%q", out)
	}
}

func TestProjectLayerIsNotRead(t *testing.T) {
	root := sample(t)
	path := filepath.Join(root, ".ccnavi", "config", "project", "messages", "b.yml")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("version: 1\nmessages:\n  NEXT_SAMPLE:\n    next: \"別の文: {args_q}\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if p := problemText(Load(root)); p != "" {
		t.Fatal(p)
	}
	if out := Say(root, "NEXT_SAMPLE", Values{"args_q": "x"}, nil); strings.Contains(out, "別の文") || !strings.Contains(out, "ccnavi-start.sh") {
		t.Errorf("%q", out)
	}
}

func TestBrokenEntriesAreErrors(t *testing.T) {
	for _, body := range []string{
		"  DENY_X:\n    kind: [deny]\n",
		"  NEXT_X:\n    kind: next\n    values: []\n    next: \"a\\u2028b\"\n",
	} {
		root := sample(t)
		put(t, root, "c.yml", "version: 1\nmessages:\n"+body)
		if !slices.ContainsFunc(Load(root).Problems, func(p rules.Problem) bool { return p.Severity == rules.Error }) {
			t.Errorf("%q を通した", body)
		}
	}
}

func TestSpecChoosesCases(t *testing.T) {
	ref := Spec("RULE_GIT_PUSH", SpecOptions{Git: "sh x", Wrapper: true, Cased: []string{"why", "next"}, Variants: Variants{"note": "root"}})
	if ref.ID != "RULE_GIT_PUSH" || ref.Values["git"] != "sh x" || ref.Variants["why"] != caseWrapper || ref.Variants["note"] != "root" {
		t.Errorf("%+v", ref)
	}
	if plain := Spec("RULE_X", SpecOptions{Cased: []string{"next"}}); len(plain.Values) != 0 || plain.Variants["next"] != casePlain {
		t.Errorf("%+v", plain)
	}
}

func TestSpokenPrefersTheTextFile(t *testing.T) {
	root := sample(t)
	put(t, root, "r.yml", ruleBody)
	r := &rules.Rule{Root: root, Text: rules.TextRef{ID: "RULE_SAMPLE"}, Message: "書いた文 {root}"}
	if got := Spoken(r); got != "止めました。\n次: 打ち直す" {
		t.Errorf("%q", got)
	}
	r.DegradedMessage = "倒した {rule}"
	if got := Spoken(r); got != "倒した " {
		t.Errorf("%q", got)
	}
	r.Text, r.DegradedMessage = rules.TextRef{}, ""
	if got := Spoken(r); got != "書いた文 "+rules.RootGlob(root) {
		t.Errorf("%q", got)
	}
}

type faultErr struct{ fault.Detail }

func TestSayFaultFallsBackToWhat(t *testing.T) {
	root := t.TempDir() // 文面ファイルの無いワークスペース
	cases := []struct {
		name string
		err  error
		want string
	}{
		{"nil", nil, ""},
		{"Detail が無い", errors.New("素のエラー"), "素のエラー"},
		{"識別子が空", &faultErr{fault.Plain("読み込めない")}, "読み込めない"},
	}
	for _, c := range cases {
		if got := SayFault(root, c.err); got != c.want {
			t.Errorf("%s: %q、want %q", c.name, got, c.want)
		}
	}
	// 引けないときは What に、引けなかったことと次の一手を足す。
	got := SayFault(root, &faultErr{fault.New("ERROR_NO_SUCH", nil, "What の 1 行")})
	if !strings.HasPrefix(got, "What の 1 行\n") || !strings.Contains(got, "ERROR_NO_SUCH") || !strings.Contains(got, "\n"+NextLabel) {
		t.Errorf("%q", got)
	}
}

func TestSayFaultUsesTheMessageFile(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, ".ccnavi", "messages")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	file := "version: 1\nmessages:\n  ERROR_T:\n    kind: error\n    values: [x]\n    what: |-\n      止めました: {x}\n    why: |-\n      理由です\n    next: |-\n      直して打ち直す\n"
	if err := os.WriteFile(filepath.Join(dir, "e.yml"), []byte(file), 0o644); err != nil {
		t.Fatal(err)
	}
	want := "止めました: 1\nなぜ: 理由です\n次: 直して打ち直す"
	if got := SayFault(root, &faultErr{fault.New("ERROR_T", fault.Values{"x": "1"}, "what")}); got != want {
		t.Errorf("%q", got)
	}
	// 値が足りなければ What に落ちる（次の一手つき）。
	if got := SayFault(root, &faultErr{fault.New("ERROR_T", nil, "what")}); !strings.HasPrefix(got, "what\n") {
		t.Errorf("%q", got)
	}
}
