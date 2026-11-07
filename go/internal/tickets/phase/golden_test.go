package phase

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase/scope"
	"ccnavi/internal/tickets/testkit"
)

// 決めたワークスペースを作って読ませ、フェーズの組み方・ゲート・止めたときの文・
// 局面・順序の苦情・手動の決着・範囲の当たり方・フェーズの終わりの知らせ（書いたマーカーのバイト列も）が
// 1 字も違わないことを見る。

const fixedClock = "2026-01-06T03:04:05+0900"

var oracleParents = []string{"i0001", "i0002", "i0003", "i0004", "i0005", "i0006", "i0007", "i0010", "i0011", "i0012"}

// goldenWorkspace は見本の答えを取ったワークスペース。親ごとに 1 つの形を置く。
//
//	i0001 並行する枝（2 と 3 が 1 の後、4 が 2 と 3 の後）。1 は閉じてレビュー不要、2 はレビュー待ちで依頼済み
//	      （子は依頼の後に閉じた）、実績のリスクが高い。子 i0001-02-01 のワークツリーに範囲の内外の変更を置く
//	i0002 1 を延期して 2 が引き受ける。子は 2 つとも手で done/ に動かし、コミットして origin に送った
//	i0003 計画の無い親。作業中の子のフェーズに reviewed が残る
//	i0004 全体計画が全部済み、フィードバック計画がまだ無い
//	i0005 手で done/ に動かした子が未コミット
//	i0006 依頼前に止まっている（レビュー準備中）。子は human_review を書かない
//	i0007 ユーザが早めに閉じた
//	i0010 done/ の子が動かした経路（finished_to）が無い
//	i0011 2 つの子のうち 1 つだけを手で done/ に動かした
//	i0012 手で done/ に動かしたが、依頼の記録が無い
func goldenWorkspace(t *testing.T) string {
	root := testkit.Workspace(t, oracleParents...)
	remote := filepath.Join(root, "remote.git")
	testkit.Git(t, root, "init", "-q", "--bare", remote)
	testkit.Git(t, root, "remote", "add", "origin", remote)
	done := `completed_at: "2026-01-01T00:00:00+0900"`
	write := func(parent, where, id, text string) {
		var dir string
		switch where {
		case "review":
			dir = filepath.Join(testkit.Worktree(root, parent), "wip", "proposals", "review")
		default:
			dir = filepath.Join(testkit.ApprovedDir(root, parent), where)
		}
		testkit.Put(t, filepath.Join(dir, id+".md"), text)
	}
	mark := func(parent, name, text string) {
		testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, parent), "phases", parent, name), text)
	}

	write("i0001", "doing", "i0001", parentText("i0001", "  - research", "  - {type: design, after: [1]}",
		"  - {type: implement, after: [1]}", "  - {type: implement, after: [2, 3]}"))
	write("i0001", "done", "i0001-01-01", childText("i0001-01-01", "i0001", "1", "false", []string{"wip/research/*"},
		done, `finished_to: "done"`))
	write("i0001", "review", "i0001-02-01", childText("i0001-02-01", "i0001", "2", "true",
		[]string{"wip/design/*", "src/*", "docs/*"}, `completed_at: "2026-01-03T00:00:00+0900"`, `finished_to: "review"`))
	mark("i0001", "2.requested", `{"at": "2026-01-02T00:00:00+0900", "by": "cli"}`)
	mark("i0001", "i0001-02-01.risk.json",
		`{"points": 58, "level": "HIGH", "hits": [{"detail": "行数が多い"}, "x", {"detail": null}, {"detail": 3}]}`)
	child := testkit.AddWorktree(t, root, "i0001-02-01", "i0001")
	for _, rel := range []string{"wip/design/a.md", "src/b.go", "docs/c.md", "wip/design/日本語 名.md",
		".ccnavi/approved/phases/i0001/1.reviewed", "wip/handoff/h.md", "wip/proposals/todo/x.md", `wip\handoff\y.md`} {
		testkit.Put(t, filepath.Join(child, filepath.FromSlash(rel)), "x\n")
	}
	testkit.Git(t, child, "add", "-A", "-f")
	testkit.Git(t, child, "commit", "-q", "-m", "child work")
	testkit.Put(t, filepath.Join(child, "other", "u.txt"), "u\n")
	testkit.Put(t, filepath.Join(child, "wip", "design", "b.md"), "b\n")

	write("i0002", "doing", "i0002", parentText("i0002", "  - {type: design, deferred: true}", "  - {type: implement, after: [1]}"))
	write("i0002", "done", "i0002-01-01", childText("i0002-01-01", "i0002", "1", "true", []string{"wip/design/*"},
		done, `finished_to: "review"`))
	write("i0002", "done", "i0002-02-01", childText("i0002-02-01", "i0002", "2", "", []string{"src/*"},
		`completed_at: "2026-01-04T00:00:00+0900"`, `finished_to: "review"`))
	mark("i0002", "2.requested", `{"at": "2026-01-02T00:00:00+0900"}`)
	testkit.Git(t, testkit.Worktree(root, "i0002"), "add", "-A", "-f")
	testkit.Git(t, testkit.Worktree(root, "i0002"), "commit", "-q", "-m", "move by hand")
	testkit.Git(t, testkit.Worktree(root, "i0002"), "push", "-q", "origin", "i0002")

	write("i0003", "doing", "i0003", parentText("i0003"))
	write("i0003", "doing", "i0003-01-01", childText("i0003-01-01", "i0003", "1", "true", []string{"src/*"},
		`started_at: "2026-01-01T00:00:00+0900"`))
	write("i0003", "done", "i0003-01-02", childText("i0003-01-02", "i0003", "1", "false", []string{"src/*"}, done))
	mark("i0003", "1.reviewed", `{"at": "2026-01-01T00:00:00+0900"}`)

	write("i0004", "doing", "i0004", parentText("i0004", "  - research", "  - {type: design, after: [1]}"))
	write("i0004", "done", "i0004-01-01", childText("i0004-01-01", "i0004", "1", "false", []string{"wip/research/*"}, done))
	write("i0004", "done", "i0004-02-01", childText("i0004-02-01", "i0004", "2", "true", []string{"wip/design/*"},
		done, `finished_to: "review"`, `settled_by: "confirm"`))
	mark("i0004", "1.skipped", `{"tickets": ["i0004-01-01"]}`)
	mark("i0004", "2.reviewed", `{"at": "2026-01-02T00:00:00+0900"}`)

	write("i0005", "doing", "i0005", parentText("i0005", "  - design"))
	write("i0005", "done", "i0005-01-01", childText("i0005-01-01", "i0005", "1", "true", []string{"wip/design/*"},
		done, `finished_to: "review"`))
	mark("i0005", "1.requested", `{"at": "2026-01-02T00:00:00+0900"}`)

	write("i0006", "doing", "i0006", parentText("i0006", "  - design", "  - {type: implement, after: [1]}"))
	write("i0006", "review", "i0006-01-01", childText("i0006-01-01", "i0006", "1", "", []string{"wip/design/*"},
		done, `finished_to: "review"`))

	write("i0007", "doing", "i0007", parentText("i0007", "  - research"))
	write("i0007", "doing", "i0007-01-01", childText("i0007-01-01", "i0007", "1", "false", []string{"wip/research/*"},
		`started_at: "2026-01-01T00:00:00+0900"`))
	mark("i0007", "close-early.json", `{"at": "2026-01-02T00:00:00+0900"}`)

	for _, id := range []string{"i0010", "i0011", "i0012"} {
		write(id, "doing", id, parentText(id, "  - design"))
	}
	write("i0010", "done", "i0010-01-01", childText("i0010-01-01", "i0010", "1", "true", []string{"wip/design/*"}, done))
	mark("i0010", "1.requested", `{"at": "2026-01-02T00:00:00+0900"}`)
	write("i0011", "done", "i0011-01-01", childText("i0011-01-01", "i0011", "1", "true", []string{"wip/design/*"},
		done, `finished_to: "review"`))
	write("i0011", "review", "i0011-01-02", childText("i0011-01-02", "i0011", "1", "true", []string{"wip/design/*"},
		done, `finished_to: "review"`))
	mark("i0011", "1.requested", `{"at": "2026-01-02T00:00:00+0900"}`)
	write("i0012", "done", "i0012-01-01", childText("i0012-01-01", "i0012", "1", "true", []string{"wip/design/*"},
		done, `finished_to: "review"`))
	return root
}

type orderCase struct {
	Child  string   `json:"child"`
	Parent string   `json:"parent"`
	Adding []string `json:"adding"`
}

func orderCases() []orderCase {
	research := []string{"wip/research/*"}
	design := []string{"wip/design/*"}
	return []orderCase{
		{Child: childText("i0001-02-02", "i0001", "2", "true", design), Parent: "i0001", Adding: []string{}},
		{Child: childText("i0001-02-02", "i0001", "2", "true", design), Parent: "i0001",
			Adding: []string{childText("i0001-01-02", "i0001", "1", "false", research)}},
		{Child: childText("i0001-04-01", "i0001", "4", "true", design), Parent: "i0001", Adding: []string{}},
		{Child: childText("i0001-04-01", "i0001", "4", "true", design), Parent: "i0001",
			Adding: []string{childText("i0001-03-01", "i0001", "3", "true", design)}},
		{Child: childText("i0001-09-01", "i0001", "9", "true", design), Parent: "i0001", Adding: []string{}},
		{Child: childText("i0002-02-02", "i0002", "2", "true", design), Parent: "i0002", Adding: []string{}},
		{Child: childText("i0006-02-01", "i0006", "2", "true", design), Parent: "i0006", Adding: []string{}},
		{Child: childText("i0003-02-01", "i0003", "2", "true", design), Parent: "i0003", Adding: []string{}},
		// 承認済みの親が無く、同じ承認で通る親の提案の計画で数える。
		{Child: childText("i0008-02-01", "i0008", "2", "true", design),
			Parent: parentText("i0008", "  - research", "  - {type: design, after: [1]}"), Adding: []string{}},
		// 壊れた計画（先行が自分より後）。
		{Child: childText("i0009-02-01", "i0009", "2", "true", design),
			Parent: parentText("i0009", "  - research", "  - {type: design, after: [3]}", "  - implement"), Adding: []string{}},
	}
}

var scopeCases = []map[string]any{
	{"child": "i0001-02-01", "paths": []string{"wip/design/a.md", "src/b.go", "docs/c.md", "other/x", "wip/research/r.md", "WIP/design/a.md"}},
	{"child": "i0002-02-01", "paths": []string{"src/a.go", "wip/design/a.md", "tests/a"}},
	{"child": "i0003-01-01", "paths": []string{"src/a.go", "docs/a.md"}},
}

func TestMatchesGolden(t *testing.T) {
	goRoot := goldenWorkspace(t)
	head := strings.TrimSpace(testkit.Git(t, testkit.Worktree(goRoot, "i0001-02-01"), "rev-parse", "HEAD"))
	cwds := func(root string) []string {
		return []string{root, testkit.Worktree(root, "i0001"), filepath.Join(testkit.Worktree(root, "i0001"), "src", "deep"),
			testkit.Worktree(root, "i0001-02-01"), filepath.Join(root, "elsewhere")}
	}
	findings := []string{"i0001-02-01", "i0002-01-01"}
	want := golden.Load[map[string]any](t, "phase")
	got := goAnswers(t, goRoot, cwds(goRoot), findings, head)
	for key, w := range want {
		if g := got[key]; !reflect.DeepEqual(normalize(t, g), w) {
			a, _ := json.MarshalIndent(normalize(t, g), "", " ")
			b, _ := json.MarshalIndent(w, "", " ")
			t.Errorf("%s が Python と違う\nGo:\n%s\nPython:\n%s", key, a, b)
		}
	}
}

func normalize(t *testing.T, v any) any {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func list[T any](xs []T) []T {
	if xs == nil {
		return []T{}
	}
	return xs
}

func goAnswers(t *testing.T, root string, cwds, findings []string, head string) map[string]any {
	rel := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
	conf := testkit.Settings(t, root)
	raw := approval.ReadRaw(conf, root)
	opened, _ := approval.Scan(conf, root, false, raw)
	closed, _ := approval.Scan(conf, root, true, raw)
	review, _ := approval.ScanReview(conf, root, raw)
	everything := slices.Concat(opened, closed, review)
	slices.SortStableFunc(everything, func(a, b *ticket.Ticket) int { return strings.Compare(a.ID, b.ID) })
	byID := approval.ByID(slices.Concat(opened, closed))
	summary := func(p *Phase) []any {
		var at any
		if n, ok := p.ReviewAt(); ok {
			at = n
		}
		var declared any
		if d := p.declaredReview(); d.Said() {
			declared = d == ticket.Needed
		}
		kinds := []string{}
		for k := range p.Marks {
			kinds = append(kinds, string(k))
		}
		slices.Sort(kinds)
		ids := []string{}
		for _, c := range p.Tickets {
			ids = append(ids, c.ID)
		}
		return []any{p.Number, p.Label(), p.Ended(), p.ReviewRequired(), p.GateClosed(), p.ReviewWaiting(),
			p.ReviewLabel(), p.Deferred(), at, list(p.Covers()), declared, list(p.coveredReviews), p.RiskLine(),
			p.RiskBody(), kinds, ids, p.ReviewedOrSkipped(), p.Planned(), p.Title()}
	}
	manual := func(m Manual) []any {
		paths := []string{}
		for _, p := range m.Paths {
			paths = append(paths, rel(p))
		}
		moved := 0.0
		if !m.MovedAt.IsZero() {
			moved = fsio.UnixSeconds(m.MovedAt)
		}
		return []any{string(m.State), list(m.Children), list(m.waiting), m.note, m.NeedTime, paths, moved, m.Line()}
	}
	at := func(f float64) ManualOptions { return ManualOptions{External: true, MovedAt: &f} }
	parents := map[string]any{}
	for _, pid := range oracleParents {
		parent := byID[pid]
		list0 := Phases(conf, root, pid, nil)
		held := HeldPhase(conf, root, pid, nil)
		e := map[string]any{}
		summaries := []any{}
		reopen := []any{}
		manuals := []any{}
		for _, p := range list0 {
			summaries = append(summaries, summary(p))
			reopen = append(reopen, list(ReopenImpact(conf, root, pid, p.Number)))
			if parent != nil {
				manuals = append(manuals, []any{
					manual(AssessManual(conf, root, parent, p, list0, ManualOptions{})),
					manual(AssessManual(conf, root, parent, p, nil, ManualOptions{External: true})),
					manual(AssessManual(conf, root, parent, p, nil, at(1700000000.5))),
					manual(AssessManual(conf, root, parent, p, nil, at(math.NaN()))),
					manual(AssessManual(conf, root, parent, p, nil, at(0))),
				})
			}
		}
		e["phases"], e["reopen"], e["manual"] = summaries, reopen, manuals
		e["held"], e["hold"] = nil, []string{}
		if held != nil {
			e["held"] = held.Number
			e["hold"] = []string{rel(HoldReason(held, "Agent", root)), rel(HoldReason(held, "Task", root)),
				rel(HoldReason(held, "Bash", root))}
		}
		e["stage"], e["planFinished"] = "", false
		if parent != nil {
			e["stage"], e["planFinished"] = Stage(conf, root, parent, nil), PlanFinished(conf, root, parent)
		}
		parents[pid] = e
	}
	children := []any{}
	for _, c := range everything {
		children = append(children, []any{c.ID, rel(ReviewNext(conf, root, c, nil)), rel(ResumedReview(conf, root, c, nil))})
	}
	order := []any{}
	for _, o := range orderCases() {
		parent := byID[o.Parent]
		if parent == nil {
			parent = parsed(t, o.Parent)
		}
		var adding []*ticket.Ticket
		for _, a := range o.Adding {
			adding = append(adding, parsed(t, a))
		}
		problems := []any{}
		for _, p := range OrderProblems(conf, root, parsed(t, o.Child), parent, adding, nil) {
			problems = append(problems, []any{string(p.Severity), p.Rule, p.Detail, p.Kind})
		}
		order = append(order, problems)
	}
	cwdAnswers := []any{}
	for _, cwd := range cwds {
		p, _ := ParentAt(conf, root, cwd, nil)
		here, _ := WorktreeAt(conf, root, cwd)
		id := ""
		if p != nil {
			id = p.ID
		}
		cwdAnswers = append(cwdAnswers, []any{id, here.Name})
	}
	verdict := func(v scope.Verdict) []any {
		id := ""
		if v.Type != nil {
			id = v.Type.ID
		}
		return []any{string(v.Decision), string(v.Limit), id}
	}
	byChild := func(id string) *ticket.Ticket {
		for _, c := range everything {
			if c.ID == id {
				return c
			}
		}
		t.Fatalf("%s が無い", id)
		return nil
	}
	verdicts := []any{}
	for _, s := range scopeCases {
		c := byChild(s["child"].(string))
		parent := byID[c.Parent]
		pt := scope.TypeFor(c, parent)
		row := []any{}
		for _, r := range s["paths"].([]string) {
			row = append(row, verdict(scope.Of(c, parent, pt, r)))
		}
		verdicts = append(verdicts, row)
	}
	why := func(err error) string {
		if err != nil {
			return err.Error()
		}
		return ""
	}
	rows := func(fs []scope.Finding) []any {
		out := []any{}
		for _, f := range fs {
			out = append(out, append([]any{f.Path}, verdict(f.Verdict)...))
		}
		return out
	}
	found := []any{}
	for _, id := range findings {
		c := byChild(id)
		parent := byID[c.Parent]
		fs, err := scope.Findings(conf, root, c, parent)
		places, merged, merr := scope.MergeFindings(conf, root, c, parent, head)
		found = append(found, []any{rows(fs), why(err), list(places), rows(merged), why(merr)})
	}
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	announced := []any{}
	for _, pid := range oracleParents {
		parent := byID[pid]
		if parent == nil {
			continue
		}
		var stderr, again bytes.Buffer
		said := Announce(&stderr, conf, root, parent, nil)
		second := Announce(&again, conf, root, parent, nil)
		announced = append(announced, []any{pid, rel(said), stderr.String(), second})
	}
	restore()
	return map[string]any{
		"parents": parents, "children": children, "order": order, "cwds": cwdAnswers, "verdicts": verdicts,
		"findings": found, "announce": announced, "files": markFiles(t, root),
	}
}

// markFiles は phases/ の下のファイルのバイト列（base64）。見本と同じ形。
func markFiles(t *testing.T, root string) map[string]string {
	files := map[string]string{}
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		if !strings.Contains(filepath.Dir(path)+string(filepath.Separator), string(filepath.Separator)+"phases"+string(filepath.Separator)) {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		key := filepath.ToSlash(strings.ReplaceAll(path, root, "<root>"))
		files[key] = base64.StdEncoding.EncodeToString(data)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return files
}
