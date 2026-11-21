package planedit

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/testkit"
)

// 振り直しのうち、TestMatchesGolden の 1 つのワークスペースに入らない場面。延期の引き受け手が無い順序・feedback の計画・
// 元からある after の誤り・書かずに止まる形（アンカー・終端・循環）・付け替えを止める 5 つの事情・書く途中で落ちたときの
// 書き戻し・計画に無い番号の子・ダイジェスト（source_sha）が子の提案を覆うこと。場面ごとに答えと、書いたあとの提案の置き場を
// 見本（testdata/plan-scenes.json）と比べる。

// partName は書きかけのファイルの名前（毎回ランダム）。
var partName = regexp.MustCompile(`\.([^./]+\.(?:md|yml))\.[0-9A-Za-z_]+\.part`)

type sceneStep struct {
	Name string `json:"name"`
	Out  string `json:"out"`
}

type planScene struct {
	Name  string            `json:"name"`
	Steps []sceneStep       `json:"steps"`
	Files map[string]string `json:"files"`
}

type sceneEnv struct {
	t    *testing.T
	root string
	rec  planScene
}

func newPlanScene(t *testing.T, name string, parents ...string) *sceneEnv {
	t.Helper()
	root := testkit.ProposalWorkspace(t, append([]string{"i0001"}, parents...)...)
	return &sceneEnv{t: t, root: root, rec: planScene{Name: name}}
}

func (e *sceneEnv) todo(id, text string) {
	e.t.Helper()
	testkit.Put(e.t, filepath.Join(testkit.TodoDir(e.root, "i0001"), id+".md"), text)
}

func (e *sceneEnv) source(name string) *Source {
	e.t.Helper()
	conf := testkit.Settings(e.t, e.root)
	proposals := approval.ReadProposals(conf, e.root, nil).Kept
	target, _ := Pick(proposals, name, e.root)
	s, why := Load(conf, e.root, target, proposals, CurrentParent(conf, e.root, name))
	if s == nil {
		e.t.Fatal(why)
	}
	return s
}

func (e *sceneEnv) say(name, out string) {
	out = partName.ReplaceAllString(strings.ReplaceAll(out, e.root, "<root>"), ".$1.<tmp>.part")
	e.rec.Steps = append(e.rec.Steps, sceneStep{name, out})
}

func (e *sceneEnv) dump(v any) string {
	e.t.Helper()
	text, err := pyjson.Dumps(v, pyjson.Options{RawUnicode: true})
	if err != nil {
		e.t.Fatal(err)
	}
	return text
}

// body は --plan-order <親> --json [--order <順序>] の答え。順序が読めなければその理由。
func (e *sceneEnv) body(name, order string) string {
	e.t.Helper()
	s := e.source("i0001")
	var parsed Order
	if order != "" {
		var why string
		if parsed, why = ParseOrder(order, s.Parent); parsed == nil {
			e.say(name, why)
			return why
		}
	}
	out := e.dump(Reorder(s, parsed).Body())
	e.say(name, out)
	return out
}

// save は --plan-order <親> --order <順序> --expect <今のダイジェスト> --write の結果。
func (e *sceneEnv) save(name, order string) {
	e.t.Helper()
	s := e.source("i0001")
	parsed, why := ParseOrder(order, s.Parent)
	if parsed == nil {
		e.say(name, why)
		return
	}
	r := Reorder(s, parsed).Save(s.sha())
	e.say(name, e.dump([]any{r.OK, r.Written, r.Messages, r.Details}))
}

// files は親のワークツリーの wip/proposals/ と承認済みの置き場の下の全ファイル。
func (e *sceneEnv) files() map[string]string {
	e.t.Helper()
	out := map[string]string{}
	base := testkit.Worktree(e.root, "i0001")
	for _, dir := range []string{filepath.Join(base, "wip", "proposals"), filepath.Join(base, ".ccnavi", "approved")} {
		_ = filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
			if err == nil && !info.IsDir() {
				data, _ := os.ReadFile(path)
				rel, _ := filepath.Rel(e.root, path)
				out[filepath.ToSlash(rel)] = string(data)
			}
			return nil
		})
	}
	return out
}

func (e *sceneEnv) finish() planScene {
	e.rec.Files = e.files()
	return e.rec
}

func sameFiles(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

const (
	reverse = `{"plan": {"1": [2], "2": [], "3": [1]}}`
	// planThree は research → design・implement（どちらも 1 の後）。
	feedbackDefs = "  implement-feedback:\n    kind: feedback\n    title: 実装の指摘\n    review: true\n    scope: inherit\n" +
		"  chores-feedback:\n    kind: feedback\n    title: 雑務の指摘\n    review: true\n    scope: inherit\n"
)

var planThree = []string{"  - research", "  - {type: design, after: [1]}", "  - {type: implement, after: [1]}"}

func withFeedbackDefs(text string) string {
	return strings.Replace(text, "plan:\n", feedbackDefs+"plan:\n", 1)
}

func TestPlanScenesMatchGolden(t *testing.T) {
	var got []planScene

	// 延期したステップを引き受けるステップが無い順序は、書けないと言う。
	e := newPlanScene(t, "延期の引き受け手が無い")
	e.todo("i0001", testkit.ProposalParent("i0001", []string{"  - research", "  - {type: design, deferred: true, after: [1]}", "  - {type: implement, after: [2]}"}))
	before := e.files()
	e.body("body", `{"plan": {"1": [], "2": [1, 3], "3": [1]}}`)
	if !sameFiles(before, e.files()) {
		t.Error("読むだけで提案が変わった")
	}
	got = append(got, e.finish())

	// feedback は計画と別の計画として振り直す。
	e = newPlanScene(t, "feedback の計画")
	e.todo("i0001", withFeedbackDefs(testkit.ProposalParent("i0001", []string{"  - design"},
		"feedback:\n  - {type: implement-feedback, after: []}\n  - {type: chores-feedback, after: []}\n  - {type: implement-feedback, after: [2, 3]}")))
	e.body("body", `{"feedback": {"2": [3], "3": [], "4": [2, 3]}}`)
	got = append(got, e.finish())

	// 提案に元からある after の誤りは、振り直しても検査から消えず、書かない。feedback の側の誤りも残る。
	e = newPlanScene(t, "元の after の誤り")
	text := testkit.ProposalParent("i0001", []string{"  - research", "  - {type: design, after: [1]}", "  - {type: implement, after: [2, x, 7]}"})
	e.todo("i0001", text)
	e.body("body", "")
	e.body("body-reordered", reverse)
	e.save("save", reverse)
	text = withFeedbackDefs(testkit.ProposalParent("i0001", planThree, "feedback:\n  - {type: implement-feedback, after: [zz]}"))
	e.todo("i0001", text)
	e.body("body-feedback", reverse)
	e.save("save-feedback", reverse)
	e.todo("i0001-05-01", testkit.ProposalChild("i0001-05-01", "i0001", "5", []string{"src/*"}))
	e.body("body-mismatched", "")
	got = append(got, e.finish())

	// 終端が 1 つでない順序・循環・アンカーを別の欄が指す提案は、書かずに止まる。
	e = newPlanScene(t, "書かずに止まる形")
	text = testkit.ProposalParent("i0001", planThree)
	e.todo("i0001", text)
	e.save("single-end", `{"plan": {"2": [1]}}`)
	e.save("cycle", `{"plan": {"1": [3], "2": [1], "3": [2]}}`)
	head, rest, _ := strings.Cut(text, "plan:\n")
	items, tail, _ := strings.Cut(rest, "human_review:")
	e.todo("i0001", head+"plan: &steps\n"+items+"rationale_extra: *steps\nhuman_review:"+tail)
	e.save("anchored-plan", reverse)
	got = append(got, e.finish())

	// ダイジェストは子の提案を覆う。
	e = newPlanScene(t, "source_sha")
	e.todo("i0001", testkit.ProposalParent("i0001", planThree))
	first := e.source("i0001").sha()
	e.todo("i0001-01-01", testkit.ProposalChild("i0001-01-01", "i0001", "1", []string{"wip/research/*"}))
	e.say("changes-with-a-child", e.dump(first != e.source("i0001").sha()))
	got = append(got, e.finish())

	// 付け替えを止める事情が 1 つでもあれば、理由を言い、書いても何も変わらない。
	e = newPlanScene(t, "付け替えを止める事情")
	e.todo("i0001", testkit.ProposalParent("i0001", planThree))
	a := testkit.ProposalChild("i0001-01-01", "i0001", "1", []string{"wip/research/*"})
	b := testkit.ProposalChild("i0001-02-01", "i0001", "2", []string{"wip/design/*"})
	c := testkit.ProposalChild("i0001-03-01", "i0001", "3", []string{"src/*"}, "predecessors: [i0001-01-01, i0001-02-01]")
	e.todo("i0001-01-01", a)
	e.todo("i0001-02-01", b)
	e.todo("i0001-03-01", c)
	blocked := func(name string) {
		snapshot := e.files()
		body := e.body(name+"-body", reverse)
		e.save(name+"-save", reverse)
		if !strings.Contains(body, `"writable": false`) || !sameFiles(snapshot, e.files()) {
			t.Errorf("%s: 付け替えを止めずに書いた", name)
		}
	}
	e.todo("i0001-02-01", "---\n: :\n")
	blocked("unreadable-target")
	e.todo("i0001-02-01", b)
	flow := filepath.Join(testkit.ApprovedDir(e.root, "i0001"), "flows", "i0001-01-01.yml")
	testkit.Put(t, flow, "nodes: []\n")
	blocked("approved-flow")
	if err := os.Remove(flow); err != nil {
		t.Fatal(err)
	}
	held := testkit.ProposalChild("i0002-01-01", "i0002", "1", []string{"src/*"}, "predecessors: [i0001-01-01]")
	approved := filepath.Join(testkit.ApprovedDir(e.root, "i0001"), "doing", "i0002-01-01.md")
	testkit.Put(t, approved, held)
	blocked("approved-ticket")
	if err := os.Remove(approved); err != nil {
		t.Fatal(err)
	}
	review := filepath.Join(testkit.Worktree(e.root, "i0001"), "wip", "proposals", "review", "i0002-01-01.md")
	testkit.Put(t, review, held)
	blocked("review-proposal")
	if err := os.Remove(review); err != nil {
		t.Fatal(err)
	}
	testkit.AddWorktree(t, e.root, "i0001-02-01", "main")
	blocked("worktree")
	got = append(got, e.finish())

	// 子の提案を書いたあとで落ちたら（下書きの付け替え先がディレクトリで塞がれている）、書いたものを全部戻す。
	e = newPlanScene(t, "途中で落ちたら全部戻す")
	e.todo("i0001", testkit.ProposalParent("i0001", planThree))
	e.todo("i0001-01-01", a)
	e.todo("i0001-02-01", b)
	e.todo("i0001-03-01", c)
	drafts := filepath.Join(testkit.Worktree(e.root, "i0001"), "wip", "proposals", "flows")
	testkit.Put(t, filepath.Join(drafts, "i0001-02-01.yml", "keep"), "k\n")
	testkit.Put(t, filepath.Join(drafts, "i0001-01-01.yml"), "nodes: [a]\n")
	snapshot := e.files()
	e.save("save", reverse)
	if !sameFiles(snapshot, e.files()) {
		t.Error("途中で落ちたのに書いたものが残った")
	}
	got = append(got, e.finish())

	// phase: の値のアンカーを別の欄が指していれば、その子は書かない。
	e = newPlanScene(t, "子のアンカー")
	e.todo("i0001", testkit.ProposalParent("i0001", planThree))
	e.todo("i0001-01-01", strings.Replace(a, "phase: 1\n", "phase: &p 1\nextra_b: *p\n", 1))
	e.todo("i0001-02-01", b)
	e.todo("i0001-03-01", c)
	snapshot = e.files()
	e.save("save", reverse)
	if !sameFiles(snapshot, e.files()) {
		t.Error("アンカーのある子を書いた")
	}
	got = append(got, e.finish())

	golden.Check(t, "plan-scenes", got)
}
