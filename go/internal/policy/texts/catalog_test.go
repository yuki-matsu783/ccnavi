package texts

import (
	"bytes"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/policy/rules"
)

// リポジトリの文面ファイルと、それを引くコード・sh の食い違いを見る。
// - 共通の文面ファイルは検査を通る
// - Go の texts.Say と sh の ccnavi_text が引く識別子は文面ファイルにあり、渡す値の名前が values と一致する
// - 文面ファイルの識別子は、どこかから引かれている

func repoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, ".ccnavi", "messages")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Skip("リポジトリの .ccnavi/messages が無い")
		}
		dir = parent
	}
}

type call struct {
	id    string
	names []string // nil なら値の名前を決められない（変数を渡している）
	where string
}

// goCalls は go/ の下の texts.Say(root, "ID", texts.Values{...}, ...) の呼び出し。
func goCalls(t *testing.T, repo string) []call {
	t.Helper()
	var found []call
	err := filepath.WalkDir(filepath.Join(repo, "go"), func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			return err
		}
		local := filepath.ToSlash(filepath.Dir(path)) == filepath.ToSlash(filepath.Join(repo, "go", "internal", "policy", "texts"))
		ast.Inspect(f, func(n ast.Node) bool {
			c, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			// texts.Say(root, "ID", Values, …) は識別子が 2 番目。fault.New("ID", Values, what) は 1 番目。
			idAt := 1
			if isFaultNew(c.Fun) {
				idAt = 0
			} else if !isSay(c.Fun, local) {
				return true
			}
			if len(c.Args) < idAt+1 {
				return true
			}
			lit, ok := c.Args[idAt].(*ast.BasicLit)
			if !ok || lit.Kind != token.STRING {
				return true
			}
			id, _ := strconv.Unquote(lit.Value)
			names := []string{}
			if len(c.Args) > idAt+1 {
				names = literalKeys(c.Args[idAt+1])
			}
			found = append(found, call{id: id, names: names, where: fset.Position(c.Pos()).String()})
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return found
}

// isFaultNew は fault.New(識別子, 値, what) の呼び出し。エラーが運ぶ識別子も、文面ファイルにあることを見る。
func isFaultNew(fun ast.Expr) bool {
	sel, ok := fun.(*ast.SelectorExpr)
	if !ok {
		return false
	}
	x, ok := sel.X.(*ast.Ident)
	return ok && x.Name == "fault" && sel.Sel.Name == "New"
}

func isSay(fun ast.Expr, local bool) bool {
	if sel, ok := fun.(*ast.SelectorExpr); ok {
		x, ok := sel.X.(*ast.Ident)
		return ok && x.Name == "texts" && sel.Sel.Name == "Say"
	}
	id, ok := fun.(*ast.Ident)
	return local && ok && id.Name == "Say"
}

// literalKeys は Values{...} の書いたキー。nil は値なし、書いたキーで決まらなければ nil。
func literalKeys(e ast.Expr) []string {
	if id, ok := e.(*ast.Ident); ok && id.Name == "nil" {
		return []string{}
	}
	lit, ok := e.(*ast.CompositeLit)
	if !ok {
		return nil
	}
	keys := []string{}
	for _, elt := range lit.Elts {
		kv, ok := elt.(*ast.KeyValueExpr)
		if !ok {
			return nil
		}
		k, ok := kv.Key.(*ast.BasicLit)
		if !ok || k.Kind != token.STRING {
			return nil
		}
		s, _ := strconv.Unquote(k.Value)
		keys = append(keys, s)
	}
	return keys
}

var (
	shCall  = regexp.MustCompile(`ccnavi_text "[^"]*" "?([A-Z][A-Z0-9_$a-z]*)"?((?: "[a-z_@]+=[^"]*")*)`)
	shValue = regexp.MustCompile(`"([a-z_@]+)=`)
)

func shCalls(t *testing.T, repo string) []call {
	t.Helper()
	paths, _ := filepath.Glob(filepath.Join(repo, ".ccnavi", "scripts", "*.sh"))
	var found []call
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		for number, line := range strings.Split(string(data), "\n") {
			for _, hit := range shCall.FindAllStringSubmatch(line, -1) {
				names := []string{}
				for _, v := range shValue.FindAllStringSubmatch(hit[2], -1) {
					if !strings.HasPrefix(v[1], "@") {
						names = append(names, v[1])
					}
				}
				found = append(found, call{id: hit[1], names: names, where: filepath.Base(path) + ":" + strconv.Itoa(number+1)})
			}
		}
	}
	return found
}

func sameNames(a, b []string) bool {
	x, y := slices.Clone(a), slices.Clone(b)
	slices.Sort(x)
	slices.Sort(y)
	return slices.Equal(slices.Compact(x), slices.Compact(y))
}

func TestCommonFilesPassTheCheck(t *testing.T) {
	c := Load(repoRoot(t))
	if p := problemText(c); p != "" {
		t.Fatal(p)
	}
	if len(c.Entries) <= 20 {
		t.Fatalf("%d 件しか読めない", len(c.Entries))
	}
}

func TestEveryIDTheCodeAsksForExists(t *testing.T) {
	repo := repoRoot(t)
	c := Load(repo)
	sh := shCalls(t, repo)
	if len(sh) < 25 {
		t.Fatalf("ccnavi_text の呼び出しを拾えていない（%d 件）", len(sh))
	}
	for _, k := range append(goCalls(t, repo), sh...) {
		if head, _, dynamic := strings.Cut(k.id, "$"); dynamic {
			if !slices.ContainsFunc(keys(c), func(id string) bool { return strings.HasPrefix(id, head) }) {
				t.Errorf("%s: %s", k.where, k.id)
			}
			continue
		}
		e := c.Entries[k.id]
		if e == nil {
			t.Errorf("%s: %s が文面ファイルに無い", k.where, k.id)
			continue
		}
		if k.names != nil && !sameNames(e.Values, k.names) {
			t.Errorf("%s: %s の値の名前 %v、文面ファイルは %v", k.where, k.id, k.names, e.Values)
		}
	}
}

func keys(c *Catalog) []string {
	out := make([]string, 0, len(c.Entries))
	for id := range c.Entries {
		out = append(out, id)
	}
	return out
}

// 文面ファイルの識別子は、どこかから引かれている。引く側は Go と sh。
func TestEveryIDInTheFilesIsAskedForSomewhere(t *testing.T) {
	repo := repoRoot(t)
	var body strings.Builder
	for _, dir := range []string{"go", filepath.Join(".ccnavi", "scripts")} {
		_ = filepath.WalkDir(filepath.Join(repo, dir), func(path string, d fs.DirEntry, err error) error {
			if err != nil || d.IsDir() || strings.HasSuffix(path, "_test.go") {
				return nil
			}
			if ext := filepath.Ext(path); ext == ".go" || ext == ".sh" {
				data, _ := os.ReadFile(path)
				body.Write(data)
			}
			return nil
		})
	}
	var heads []string
	for _, k := range shCalls(t, repo) {
		if head, _, dynamic := strings.Cut(k.id, "$"); dynamic {
			heads = append(heads, head)
		}
	}
	text := body.String()
	for id := range Load(repo).Entries {
		if !strings.Contains(text, id) && !slices.ContainsFunc(heads, func(h string) bool { return strings.HasPrefix(id, h) }) {
			t.Errorf("%s をどこも引かない", id)
		}
	}
}

// kind: next の文は、組み立てると次: の行がちょうど 1 行で、残りは案内: の行。
func TestNextTextsRenderOneNextLine(t *testing.T) {
	repo := repoRoot(t)
	c := Load(repo)
	n := 0
	for id, e := range c.Entries {
		if e.kind != kindNext {
			continue
		}
		n++
		said := Say(repo, id, dummyValues(e), firstCases(e))
		if strings.Contains(said, "引けませんでした") || strings.Contains(said, "{") || len(LinesOf(said)) != 1 {
			t.Errorf("%s: %q", id, said)
		}
		for _, line := range strings.Split(said, "\n") {
			if !strings.HasPrefix(line, NextLabel) && !strings.HasPrefix(line, NoteLabel) {
				t.Errorf("%s: %q", id, line)
			}
		}
	}
	if n < 50 {
		t.Errorf("next の文が %d 件しかない", n)
	}
}

func dummyValues(e *Entry) Values {
	v := Values{}
	for _, name := range e.Values {
		v[name] = "v"
	}
	return v
}

func firstCases(e *Entry) Variants {
	v := Variants{}
	for name, f := range e.fields {
		if len(f.cases) > 0 {
			v[name] = f.cases[0]
		}
	}
	return v
}

// 誤りのある文面を、CheckText の答えの見本（testdata）と突き合わせる（苦情の文と、採る件）。
func TestCheckTextMatchesGolden(t *testing.T) {
	entries := []string{
		"  deny_x: {kind: deny}",
		"  FOO_X: {kind: deny}",
		"  DENY_X: [1]",
		"  DENY_X: {kind: [deny]}",
		"  DENY_X: {kind: next, next: a}",
		"  DENY_X: {kind: deny, values: [A], what: a, why: b, next: c, no_bypass: d}",
		"  DENY_X: {kind: deny, values: [a, a], what: '{a}', why: b, next: c, no_bypass: d}",
		"  DENY_X: {kind: deny, values: [a, b], what: '{a} {c} {d:sh} {e_q:path} {f:zz} {G}', why: b, next: c, no_bypass: d, extra: 1, 3: x}",
		"  DENY_X: {kind: deny, what: '{ } }}{{ {script:bad name} {script:ok.sh}', why: '次: x', next: \"a\\nb\", no_bypass: 'ADR-12 と adr 3 と ADR　3'}",
		"  DENY_X: {kind: deny, what: {a: x}, why: {a: x, B: y}, next: {a: '', b: y}, no_bypass: {a: .ccnavi/scripts/x, b: '{root}/.ccnavi/scripts/y'}}",
		"  DENY_X: {kind: deny, what: '" + strings.Repeat("あ", 601) + "', why: b, next: c, no_bypass: d}",
		"  NEXT_X: {kind: next, next: '" + strings.Repeat("い", 500) + "', note: '" + strings.Repeat("う", 590) + "\\n" + strings.Repeat("え", 590) + "\\n" + strings.Repeat("お", 590) + "'}",
		"  CHOICES_X: {kind: choices, items: [a]}",
		"  CHOICES_X: {kind: choices, items: [a, '', c]}",
		"  CHOICES_X: {kind: choices, items: [a, \"b\\nc\"]}",
		"  CHOICES_X: {kind: choices, items: [a, '次の手: b']}",
		"  CHOICES_X: {kind: choices, items: 3}",
		"  NOTE_X: {kind: note, note: \"a\\n\\nb {x}\", values: [x]}",
		"  NOTICE_X: {kind: notice}",
		"  NOTICE_X: {kind: notice, what: 3}",
		"  RULE_X: {kind: deny, what: a, why: '', next: null, no_bypass: '  '}",
		"  RULE_ASK__X: {kind: deny, what: a}",
		"  NUDGE_X: {kind: nudge, what: a, next: b, values: null}",
		"  NUDGE_X: {kind: nudge, what: a, next: b, values: x}",
	}
	var docs []string
	for _, e := range entries {
		docs = append(docs, "version: 1\nmessages:\n"+e+"\n")
	}
	docs = append(docs, "version: 2\nmessages: {}\n", "version: true\nmessages: {}\n", "messages: {}\n", "version: 1\nmessages: []\n", "- 1\n")
	want := golden.Load[[][2][]string](t, "check_text")
	said := 0
	for _, w := range want {
		said += len(w[1])
	}
	if said < len(docs) {
		t.Fatalf("見本の苦情が %d 件しかない", said)
	}
	for i, d := range docs {
		found, problems := CheckText(d, "/x/m.yml")
		got := [2][]string{slices.Sorted(maps.Keys(found)), {}}
		if got[0] == nil {
			got[0] = []string{}
		}
		for _, p := range problems {
			got[1] = append(got[1], p.String())
		}
		gj, _ := json.Marshal(got)
		wj, _ := json.Marshal(want[i])
		if !bytes.Equal(gj, wj) {
			t.Errorf("%s\n got  %s\n want %s", d, gj, wj)
		}
	}
}

// sh の案内（note-sh.yml）は、値を埋めると案内: の行だけになり、次: の行を足さない（次: は 1 行と数える）。
func TestShNotesRenderAsNoteLinesOnly(t *testing.T) {
	repo := repoRoot(t)
	data, err := os.ReadFile(filepath.Join(dir(repo), "note-sh.yml"))
	if err != nil {
		t.Fatal(err)
	}
	ids := regexp.MustCompile(`(?m)^  (NOTE_[A-Z0-9_]+):$`).FindAllStringSubmatch(string(data), -1)
	if len(ids) < 25 {
		t.Fatalf("%d 件しかない", len(ids))
	}
	c := Load(repo)
	for _, m := range ids {
		said := Say(repo, m[1], dummyValues(c.Entries[m[1]]), nil)
		if strings.Contains(said, "引けませんでした") || strings.Contains(said, "{") || len(LinesOf(said)) != 0 {
			t.Errorf("%s: %q", m[1], said)
		}
		for _, line := range strings.Split(said, "\n") {
			if !strings.HasPrefix(line, NoteLabel) {
				t.Errorf("%s: %q", m[1], line)
			}
		}
	}
	called := map[string]bool{}
	for _, k := range shCalls(t, repo) {
		called[k.id] = true
	}
	for _, id := range []string{"NOTE_GIT_INTEGRATE_CONFLICT", "NOTE_GIT_MERGE_ON_TARGET_ALLOWED", "NOTE_WORKTREE_RECORD_MISSING",
		"NOTE_START_TEMPLATES", "NOTE_STAGE_NONINTERACTIVE", "NOTE_SYNC_RETIRED_UNANSWERED", "NOTE_TICKET_NEW_ISSUE"} {
		if !called[id] {
			t.Errorf("sh が %s を引かない", id)
		}
	}
}

// 同梱の rules.yml の文面（message・additionalContext・additionalContextOnce）に ADR 番号を書かない。
func TestNoADRNumberInSharedRuleWording(t *testing.T) {
	if adrErr != nil || !hasADR("ADR-12") || !hasADR("adr\u30003") || hasADR("ADR") {
		t.Fatal(adrErr)
	}
	repo := repoRoot(t)
	rs, problems, err := rules.Load(filepath.Join(repo, ".ccnavi", "config", "common", "rules.yml"), repo)
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range problems {
		if p.Severity == rules.Error {
			t.Error(p)
		}
	}
	all := rs.All()
	if len(all) <= 10 {
		t.Fatalf("%d 件しかない", len(all))
	}
	for _, r := range all {
		for _, text := range []string{r.Message, r.AdditionalContext, r.AdditionalContextOnce} {
			if hasADR(text) {
				t.Errorf("%s: %q", r.ID, text)
			}
		}
	}
}

// kind: error の文は、組み立てると what・なぜ・次: の順で、次: の行がちょうど 1 行になる。
func TestErrorTextsRenderWhatWhyNext(t *testing.T) {
	repo := repoRoot(t)
	c := Load(repo)
	n := 0
	for id, e := range c.Entries {
		if e.kind != kindError {
			continue
		}
		n++
		said := Say(repo, id, dummyValues(e), firstCases(e))
		if strings.Contains(said, "引けませんでした") || strings.Contains(said, "{") {
			t.Errorf("%s: %q", id, said)
		}
		lines := strings.Split(said, "\n")
		if len(lines) < 3 || !strings.HasPrefix(lines[1], whyLabel) || !strings.HasPrefix(lines[2], NextLabel) {
			t.Errorf("%s: what・なぜ・次: の順になっていない: %q", id, said)
		}
		if got := strings.Count(said, "\n"+NextLabel); got != 1 {
			t.Errorf("%s: 次: が %d 行", id, got)
		}
	}
	if n < 1 {
		t.Error("error の文が無い")
	}
}
