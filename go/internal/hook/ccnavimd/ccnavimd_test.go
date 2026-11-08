package ccnavimd

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/tickets/testkit"
)

const nested = `# タイトル
- いつも
<!-- ccnavi:if a -->
- a のとき {{x}}
<!-- ccnavi:if b -->
- a と b のとき
<!-- ccnavi:endif -->
<!-- ccnavi:endif -->

<!-- ccnavi:if b -->
- b のとき
<!-- ccnavi:endif -->
- 最後
`

const withElse = `<!-- ccnavi:if a -->
- a
<!-- ccnavi:if b -->
- a と b
<!-- ccnavi:else -->
- a で b でない
<!-- ccnavi:endif -->
<!-- ccnavi:else -->
- a でない
<!-- ccnavi:endif -->
`

func set(names ...string) map[string]bool {
	out := map[string]bool{}
	for _, n := range names {
		out[n] = true
	}
	return out
}

func TestRender(t *testing.T) {
	cases := []struct {
		name, text string
		flags      map[string]bool
		values     map[string]string
		want       string
	}{
		{"成り立たない囲みは落ちる", nested, set(), nil, "# タイトル\n- いつも\n\n- 最後"},
		{"成り立つ囲みだけ載る", nested, set("b"), nil, "# タイトル\n- いつも\n\n- b のとき\n- 最後"},
		{"入れ子は内も外も成り立つとき", nested, set("a", "b"), map[string]string{"x": "値"},
			"# タイトル\n- いつも\n- a のとき 値\n- a と b のとき\n\n- b のとき\n- 最後"},
		{"入れ子の内だけでは載らない", nested, set("a"), nil, "# タイトル\n- いつも\n- a のとき {{x}}\n\n- 最後"},
		{"知らない差し込みは残る", "{{nothing}}", set(), nil, "{{nothing}}"},
		{"空行は 1 つにまとめ前後は取る", "\n\na\n\n\n\nb\n\n", set(), nil, "a\n\nb"},
		{"else は成り立たないとき", withElse, set(), nil, "- a でない"},
		{"内側の else", withElse, set("a"), nil, "- a\n- a で b でない"},
		{"else を使わない側", withElse, set("a", "b"), nil, "- a\n- a と b"},
		{"外が成り立たなければ内の else も載らない",
			"<!-- ccnavi:if a -->\n<!-- ccnavi:if b -->\nx\n<!-- ccnavi:else -->\ny\n<!-- ccnavi:endif -->\n<!-- ccnavi:endif -->",
			set("b"), nil, ""},
		{"深さの分だけインデントを取る",
			"<!-- ccnavi:if a -->\n  - a\n    - 深い\n  <!-- ccnavi:if b -->\n    - b\n  <!-- ccnavi:endif -->\n<!-- ccnavi:endif -->\n- 外",
			set("a", "b"), nil, "- a\n  - 深い\n- b\n- 外"},
		{"-->にくっついた名前", "<!-- ccnavi:if a-->\nx\n<!-- ccnavi:endif -->", set("a"), nil, "x"},
	}
	for _, c := range cases {
		if got := render(c.text, c.flags, c.values); got != c.want {
			t.Errorf("%s: %q, want %q", c.name, got, c.want)
		}
	}
}

func TestProblems(t *testing.T) {
	text := "<!-- ccnavi:if nothing -->\n{{nope}}\n<!-- ccnavi:endif -->\n<!-- ccnavi:endif -->\n<!-- ccnavi:if docs -->\n" +
		"<!-- ccnavi:else -->\n<!-- ccnavi:else -->\nx <!-- ccnavi:if a -->\n"
	want := []string{
		"1 行目: 条件 nothing は指定できない（組み込みの条件でも ccnavimd.sh の ccnavi_cond_nothing でもない）",
		"2 行目: 差し込み {{nope}} は指定できない",
		"4 行目: ccnavi:endif に対応する ccnavi:if がありません。",
		"7 行目: 1 つの ccnavi:if に ccnavi:else は 1 つだけ",
		"8 行目: ccnavi: の囲みは 1 行に 1 つ、単独で書く",
		"ccnavi:if に対応する ccnavi:endif がありません。",
	}
	if got := Problems(text, nil); !slices.Equal(got, want) {
		t.Errorf("Problems:\n%q\nwant\n%q", got, want)
	}
	if got := Problems("<!-- ccnavi:else -->\n", nil); !slices.Equal(got, []string{"1 行目: ccnavi:else に対応する ccnavi:if がありません。"}) {
		t.Errorf("else だけ: %q", got)
	}
	uses := "<!-- ccnavi:if has_docker -->\n<!-- ccnavi:endif -->\n"
	if got := Problems(uses, set("has_docker")); len(got) != 0 {
		t.Errorf("sh の関数の名前は許す: %q", got)
	}
	// 名前の形の誤りは NameProblems が言い、Problems は言わない。
	bad := "<!-- ccnavi:if has-docker -->\n<!-- ccnavi:endif -->\n<!-- ccnavi:if 1a -->\n<!-- ccnavi:endif -->\n"
	if got := Problems(bad, nil); len(got) != 0 {
		t.Errorf("形の誤りを Problems が言った: %q", got)
	}
	names := NameProblems(bad)
	if len(names) != 2 || !strings.HasPrefix(names[0], "1 行目: 条件の名前 has-docker は指定できない") ||
		!strings.HasPrefix(names[1], "3 行目: 条件の名前 1a は指定できない") {
		t.Errorf("NameProblems: %q", names)
	}
	for name := range conditions {
		if !shell.IsIdentifier(name) {
			t.Errorf("組み込みの名前 %s が使えない形", name)
		}
	}
}

func TestRepositoryCCNAVIMDHasNoProblems(t *testing.T) {
	root := testkit.RepoRoot(t)
	data, err := os.ReadFile(filepath.Join(root, ".ccnavi", file))
	if err != nil {
		t.Fatal(err)
	}
	if got := Problems(string(data), nil); len(got) != 0 {
		t.Errorf("%q", got)
	}
}

func TestBuiltinConditions(t *testing.T) {
	s := situation{conf: &config.Settings{}, docs: true, denyIDs: set(workspaceRootRule), mode: config.DryRun}
	for _, name := range []string{"workspace_root", "docs", "dry_run"} {
		if !conditions[name](s) {
			t.Errorf("%s が偽", name)
		}
	}
}

type scriptFixture struct {
	t    *testing.T
	root string
	conf *config.Settings
}

func newScriptFixture(t *testing.T) scriptFixture {
	root := testkit.TempRoot(t)
	return scriptFixture{t: t, root: root, conf: &config.Settings{}}
}

func (f scriptFixture) write(body string) {
	testkit.Put(f.t, filepath.Join(f.root, config.DefaultProjectHome, config.ScriptsDir, scriptFile), body)
}

func (f scriptFixture) flags(md string, docs bool, deny map[string]bool) map[string]bool {
	s := situation{conf: f.conf, root: f.root, docs: docs, denyIDs: deny, mode: config.Enable}
	return scriptFlags(f.conf, f.root, md, s)
}

func uses(names ...string) string {
	var b strings.Builder
	for _, n := range names {
		b.WriteString("<!-- ccnavi:if " + n + " -->\n<!-- ccnavi:endif -->\n")
	}
	return b.String()
}

func sameSet(t *testing.T, label string, got, want map[string]bool) {
	t.Helper()
	if len(got) != len(want) {
		t.Errorf("%s: %v, want %v", label, got, want)
		return
	}
	for k := range want {
		if !got[k] {
			t.Errorf("%s: %v, want %v", label, got, want)
			return
		}
	}
}

func TestScriptConditions(t *testing.T) {
	f := newScriptFixture(t)
	sameSet(t, "sh が無い", f.flags(uses("nothing"), false, nil), set("nothing"))
	f.write("ccnavi_cond_yes() { return 0; }\nccnavi_cond_no() { return 1; }\nccnavi_cond_has_docker() { return 0; }\nccnavi_cond_broken() { return 127; }\n")
	// 0 が真、1 が偽、それ以外は判定できないので真。関数の無い名前も真。
	sameSet(t, "関数ごと", f.flags(uses("yes", "no", "has_docker", "broken", "nothing"), false, nil),
		set("yes", "has_docker", "broken", "nothing"))
	f.write("ccnavi_cond_x() { \n")
	sameSet(t, "壊れた sh", f.flags(uses("x"), false, nil), set("x"))
	f.write("ccnavi_cond_env() {\n  [ \"$CCNAVI_DOCS\" = 1 ] || return 1\n  case \" $CCNAVI_DENY_IDS \" in *\" a \"*) return 0;; esac\n  return 1\n}\n")
	sameSet(t, "状況を環境変数で渡す", f.flags(uses("env"), true, set("a", "b")), set("env"))
	sameSet(t, "状況が合わない", f.flags(uses("env"), false, set("a")), set())
	marker := filepath.Join(f.root, "ran")
	f.write("ccnavi_cond_docs() { return 1; }\nccnavi_cond_unused() { touch '" + filepath.ToSlash(marker) + "'; }\n")
	sameSet(t, "組み込みと同じ名前は走らせない", f.flags(uses("docs"), false, nil), set())
	if _, err := os.Stat(marker); err == nil {
		t.Error("使っていない関数を走らせた")
	}
	sameSet(t, "ScriptNames", ScriptNames(f.conf, f.root), set("unused"))
}

func sessionWorkspace(t *testing.T, md string) string {
	root := testkit.TempRoot(t)
	if md != "" {
		testkit.Put(t, filepath.Join(root, config.DefaultProjectHome, file), md)
	}
	testkit.Put(t, filepath.Join(root, config.DefaultProjectHome, config.ScriptsDir, scriptFile), "ccnavi_cond_mine() { return 0; }\n")
	os.MkdirAll(filepath.Join(root, "projects", "lib", ".git"), 0o755)
	return root
}

// 決めた本文（条件の段・置き換え・長い本文・空）の組み立ては、Go の答えの見本（答えが確かめ済みだった Go の
// 答えから取った）と比べる。リポジトリの CCNAVI.md は書き換わるので、段と置き換えが残らないことだけを見る。
func TestSessionTextMatchesGolden(t *testing.T) {
	repo := testkit.RepoRoot(t)
	data, err := os.ReadFile(filepath.Join(repo, ".ccnavi", file))
	if err != nil {
		t.Fatal(err)
	}
	extra := "\n<!-- ccnavi:if mine -->\n- 自前 {{scratch}} {{projects}} {{tickets_dir}} {{mode_env}}\n<!-- ccnavi:endif -->\n"
	fixed := "# 作業の決まり\n\n- 置き場 {{scratch}}\n<!-- ccnavi:if workspace-root -->\n- ルート\n<!-- ccnavi:endif -->\n" + extra
	long := strings.Repeat("長い行。\n", 2000)
	cases := [][]any{
		{false, []string{}, "enable"},
		{true, []string{"workspace-root"}, "dry-run"},
	}
	t.Setenv(env.ClaudeSessionAttended, "0")
	say := func(md string) []string {
		root := sessionWorkspace(t, md)
		conf := testkit.Settings(t, root)
		var out []string
		for _, c := range cases {
			deny := set(c[1].([]string)...)
			out = append(out, strings.ReplaceAll(SessionText(conf, root, c[0].(bool), deny, config.Mode(c[2].(string))), root, "<root>"))
		}
		return out
	}
	for i, text := range say(string(data) + extra) {
		if strings.Contains(text, "{{") || strings.Contains(text, "ccnavi:if") || strings.Contains(text, "ccnavi:endif") {
			t.Errorf("case %d: 置き換えか条件の段が残った:\n%s", i, text)
		}
	}
	golden.Check(t, "session", map[string][]string{"fixed": say(fixed), "long": say(long), "empty": say("")})
}
