package core

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/testkit"
)

// 承認の一覧（--preview）と絞った承認・途中で止まる承認のうち、TestApproveMatchesGolden の 1 本の流れに入らない場面。
// 超過のある子・計画に無い番号の子・読めない提案の載り方、表記の大文字小文字だけの違い、feedback の計画を持つ親の
// 範囲の警告、絞った承認の成功、1 件目で止まる承認、ダイジェストが超過の変化に左右されないこと。

const feedbackPhases = phasesCopy +
	"  design-feedback:\n    kind: feedback\n    title: 設計の指摘\n    review: true\n    scope: inherit\n"

// scene は 1 つのワークスペースで打った手の答え（名前・終了コード・標準出力・標準エラー）と、終わったあとの承認済みの置き場。
type scene struct {
	Name  string            `json:"name"`
	Steps []step            `json:"steps"`
	Files map[string]string `json:"files"`
}

type sceneRun struct {
	t     *testing.T
	root  string
	conf  *config.Settings
	steps []step
}

func newScene(t *testing.T) *sceneRun {
	t.Helper()
	root := workspace(t, "i0001")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "phases.yml"), "version: 1\n"+phasesCopy)
	conf, _ := config.Load(root)
	return &sceneRun{t: t, root: root, conf: conf}
}

func (s *sceneRun) todo(name, text string) {
	s.t.Helper()
	testkit.Put(s.t, filepath.Join(testkit.Worktree(s.root, "i0001"), "wip", "proposals", "todo", name+".md"), text)
	// 親のワークツリーに未コミットの変更があると承認が止まるので、提案はコミットする。
	testkit.Commit(s.t, testkit.Worktree(s.root, "i0001"), "propose "+name)
}

func (s *sceneRun) run(name string, f func(o, e *bytes.Buffer) int) string {
	s.t.Helper()
	var o, e bytes.Buffer
	code := f(&o, &e)
	s.steps = append(s.steps, step{name, float64(code), testkit.Scrub(s.root, o.String()), testkit.Scrub(s.root, e.String())})
	return o.String()
}

func (s *sceneRun) preview(name string, asJSON bool, only ...string) string {
	return s.run(name, func(o, e *bytes.Buffer) int { return Preview(o, e, s.conf, s.root, asJSON, only) })
}

func (s *sceneRun) yes(name string, ids []string, digest string, only ...string) {
	s.run(name, func(o, e *bytes.Buffer) int {
		code, err := ApproveYes(o, e, s.conf, s.root, ids, true, only, digest)
		if err != nil {
			s.t.Fatal(err)
		}
		return code
	})
}

func digestOf(t *testing.T, shown string) string {
	t.Helper()
	v, err := pyjson.Loads(shown)
	if err != nil {
		t.Fatal(err)
	}
	return v.(*pyjson.Object).String("digest")
}

func (s *sceneRun) done(name string) scene {
	return scene{name, s.steps, filesUnder(s.root, testkit.ApprovedDir(s.root, "i0001"))}
}

func withGlob(text, glob string) string {
	return strings.Replace(text, `glob: "wip/design/*"`, `glob: "`+glob+`"`, 1)
}

func TestApproveScenesMatchGolden(t *testing.T) {
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	defer via.Session(via.Board, "")()
	var got []scene

	// 超過のある子は一覧に載って overflow を持つ。計画に無い番号の子は rejected、読めない提案は problems に載る。
	s := newScene(t)
	s.todo("i0001", parentText("i0001"))
	s.todo("i0001-01-01", childText("i0001-01-01", "i0001"))
	s.todo("i0001-01-02", withGlob(childText("i0001-01-02", "i0001"), "docs/*"))
	s.todo("i0001-05-05", strings.Replace(childText("i0001-05-05", "i0001"), "phase: 1", "phase: 5", 1))
	s.todo("broken", "---\n: :\n")
	s.preview("preview-json", true)
	s.preview("preview-text", false)
	got = append(got, s.done("超過・計画に無い番号・読めない提案"))

	// 表記の大文字小文字だけが定義の範囲と違う子は、超過にならない。
	s = newScene(t)
	s.todo("i0001", parentText("i0001"))
	s.todo("i0001-01-01", withGlob(childText("i0001-01-01", "i0001"), "WIP/Design/*"))
	s.preview("preview-json", true)
	got = append(got, s.done("大文字小文字だけの違い"))

	// feedback の計画があっても、計画の子が書けない場所を親の allow に持てば警告する。
	s = newScene(t)
	testkit.Put(t, filepath.Join(s.root, ".ccnavi", "config", "common", "phases.yml"), "version: 1\n"+feedbackPhases)
	s.conf, _ = config.Load(s.root)
	text := strings.Replace(parentText("i0001", "feedback:\n  - design-feedback"), phasesCopy, feedbackPhases, 1)
	s.todo("i0001", strings.Replace(text, `glob: "src/*"`, `glob: "docs/**"`, 1))
	s.todo("i0001-01-01", childText("i0001-01-01", "i0001"))
	s.preview("preview-json", true)
	got = append(got, s.done("feedback の計画と範囲外の allow"))

	// 承認待ちが無ければ空の一覧で成功する。
	s = newScene(t)
	s.preview("preview-json", true)
	s.preview("preview-text", false)
	got = append(got, s.done("承認待ちが無い"))

	// 絞った一覧は絞った分だけを承認する。絞らない一覧は、残った子だけになる。
	s = newScene(t)
	s.todo("i0001", parentText("i0001"))
	s.todo("i0001-01-01", childText("i0001-01-01", "i0001"))
	shown := s.preview("preview-only", true, "i0001")
	s.yes("yes-only", []string{"i0001"}, digestOf(t, shown), "i0001")
	s.preview("preview-after", true)
	got = append(got, s.done("絞った承認"))

	// 1 件目で止まったら placed は空で、2 件目は置かない。
	s = newScene(t)
	s.todo("i0001", parentText("i0001"))
	s.todo("i0001-01-01", childText("i0001-01-01", "i0001"))
	shown = s.preview("preview-json", true)
	testkit.Put(t, filepath.Join(testkit.Worktree(s.root, "i0001"), ".ccnavi", "approved", "doing"), "x\n")
	s.yes("yes-first-fails", []string{"i0001", "i0001-01-01"}, digestOf(t, shown))
	got = append(got, s.done("1 件目で止まる承認"))

	// 親を承認したあとで、固定した定義の範囲が変わって子の超過が増えても、ダイジェストは変わらない。
	s = newScene(t)
	s.todo("i0001", parentText("i0001"))
	s.todo("i0001-01-01", childText("i0001-01-01", "i0001"))
	shown = s.preview("preview-parent", true, "i0001")
	s.yes("yes-parent", []string{"i0001"}, digestOf(t, shown), "i0001")
	before := s.preview("preview-before", true)
	copyPath := filepath.Join(testkit.Worktree(s.root, "i0001"), ".ccnavi", "approved", "doing", "i0001.md")
	raw := readRel(t, s.root, copyPath)
	testkit.Put(t, copyPath, strings.Replace(raw, "    - wip/design/*\n", "    - wip/elsewhere/*\n", 1))
	after := s.preview("preview-after", true)
	s.steps = append(s.steps, step{"same-digest", float64(0), digestOf(t, before) == digestOf(t, after), ""})
	got = append(got, s.done("超過が増えてもダイジェストは同じ"))

	// ターミナルの承認は、親を置けて子だけ失敗したら、終了コード 4 と、置けたもの・失敗・書けなかったものを placed-out に返す。
	terminal := via.Session(via.Terminal, "")
	s = newScene(t)
	s.todo("i0001", parentText("i0001"))
	s.todo("i0001-01-01", childText("i0001-01-01", "i0001"))
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(s.root, "i0001"), "doing", "i0001-01-01.md", "x"), "x\n")
	placed := filepath.Join(s.root, "placed.json")
	s.run("agree-partial", func(o, e *bytes.Buffer) int {
		code, err := Approve(strings.NewReader("y\n"), o, e, s.conf, s.root, nil, placed)
		if err != nil {
			s.t.Fatal(err)
		}
		return code
	})
	s.steps = append(s.steps, step{"placed-out", float64(0), testkit.Scrub(s.root, readRel(t, s.root, placed)), ""})
	got = append(got, s.done("ターミナル承認の部分失敗"))
	terminal()

	golden.Check(t, "approve-scenes", got)
}
