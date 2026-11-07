package approval

import (
	"bytes"
	"encoding/base64"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// Python の tickets/approval と同じワークスペースを読ませ、置き場の読み・信頼できない理由・先行・続きの子・
// write-and-push の答えが 1 字も違わないことを見る。マーカーと記録は両方が同じ時刻で書き、バイト列が同じで、
// 相手の書いたものを読めることを見る（Python が書いた記録を Go で、Go が書いた記録を Python で読む）。

const approvalOracle = `
import base64, io, json, os, sys
from ccnavi.infra import fsio, settings
from ccnavi.tickets.approval import checks, marks, ops, store as approval, write_and_push as wap
from ccnavi.tickets.model import ticket as tm
c = json.load(sys.stdin)
root = c["root"]
def rel(s):
    return s.replace(root, "<root>")
conf, _ = settings.load(root)
out = {}
raw = approval.read_raw(conf, root)
opened, notes = approval.scan(conf, root, raw=raw)
closed, cnotes = approval.scan(conf, root, closed=True, raw=raw)
review, rnotes = approval.scan_review(conf, root, raw=raw)
row = lambda ts: [[t.ticket, t.tree, t.state, rel(t.blocked)] for t in ts]
out["open"], out["closed"], out["review"] = row(opened), row(closed), row(review)
out["notes"] = [rel(n) for n in notes + cnotes + rnotes]
kept, problems, stale = approval.read_proposals(conf, root, raw.everything)
out["proposals"] = [[t.ticket, t.tree, t.state] for t in kept]
current = checks.by_id(opened)
out["stale"] = [[t.ticket, w, rel(approval.revision_elsewhere_text(conf, root, t, w)),
                 approval.revision_elsewhere(t, current.get(t.ticket))] for t, w in stale]
out["family"] = [[t.ticket, [rel(p.detail) for p in checks.family_problems(conf, root, t)]] for t in kept]
out["home"] = rel(approval.home_dir(conf, root, "i0001-01-01", "i0001", project=""))
out["nowhere"] = rel(approval.home_dir_read(conf, root, "i0099", "", project=""))
out["nowhereAny"] = rel(approval.home_dir_read(conf, root, "i0001", ""))
try:
    approval.home_dir(conf, root, "i0099", "", project="")
except approval.NoParentWorktree as exc:
    out["noParent"] = rel(str(exc))
pool = ops.predecessor_pool(conf, root)
out["unmet"] = [[t.ticket, [[p.ticket, p.state, rel(p.where), rel(p.label)] for p in checks.unmet_predecessors(t, pool)],
                 [[p.kind, rel(p.detail)] for p in checks.predecessor_problems(t, pool, ".ccnavi/approved")]] for t in opened]
parent = current["i0001"]
children = sorted([t for t in opened + closed if t.parent == "i0001" and t.phase == 1], key=lambda t: t.ticket)
ident, t, why = ops._followup_ticket(conf, root, parent, 1, children, ["1 行の指摘", "### 見出し\n本文"], "op-1")
out["followup"] = [ident, tm.render(t) if t else "", why]
out["followupProblem"] = ops.followup_problem(conf, root, parent, 1, children, [])
out["next"] = [ops.next_child_id(conf, root, "i0001", 1), ops.next_child_id(conf, root, "i0001", 2)]
try:
    ops.next_child_id(conf, root, "i0001", 100)
except ValueError as exc:
    out["nextBad"] = str(exc)
out["existing"] = rel(ops.existing_ticket_file(conf, root, "I0001-01-01"))
for name, ident in (("wapFamily", "i0001-01-01"), ("wapStop", "i0099")):
    buf = io.StringIO()
    wap.family(buf, conf, root, ident)
    out[name] = rel(buf.getvalue())
buf, err = io.StringIO(), io.StringIO()
out["wapSortCode"] = wap.sort(buf, err, conf, root, "i0001")
out["wapSort"] = buf.getvalue() + err.getvalue()
py, go = c["py"], c["go"]
with fsio.clock(c["clock"]):
    marks.write_mark(py, "i0001", 1, marks.MARK_REQUESTED, {"by": "cli", "名": "値"})
    marks.write_mark(py, "i0001", 1, marks.MARK_REVIEWED, {"at": "given", "by": "decide"})
    marks.write_mark(py, "i0001", 2, marks.MARK_PENDING, {"review": True})
    marks.write_parent_mark(py, "i0001", marks.PARENT_MARK_READY, {"mr": 7})
    marks.write_child_record(py, "i0001", "i0001-01-01", marks.CHILD_RECORD_RISK, {"points": 3, "level": "中"})
    marks.add_ledger(py, "i0001", [{"key": "u/7#t1", "version": 1}, {"key": "u/7#t2", "at": "kept"}])
    marks.add_ledger(py, "i0001", [{"key": "u/7#t1", "version": 2}])
    cleared = marks.clear_marks(py, "i0001", 2)
out["cleared"] = [cleared.kinds, cleared.failed, cleared.warnings]
files = {}
for base, _, names in os.walk(py):
    for n in names:
        p = os.path.join(base, n)
        with open(p, "rb") as f:
            files[os.path.relpath(p, py).replace(os.sep, "/")] = base64.b64encode(f.read()).decode()
out["files"] = files
out["readGo"] = {
    "marks": {k: v for k, v in marks.marks(go, "i0001", 1).items()},
    "ready": marks.read_parent_mark(go, "i0001", marks.PARENT_MARK_READY),
    "risk": marks.read_child_record(go, "i0001", "i0001-01-01", marks.CHILD_RECORD_RISK),
    "ledger": list(marks.read_ledger(go, "i0001")),
}
json.dump(out, sys.stdout, ensure_ascii=False)
`

type approvalAnswers struct {
	Open            [][]string        `json:"open"`
	Closed          [][]string        `json:"closed"`
	Review          [][]string        `json:"review"`
	Notes           []string          `json:"notes"`
	Proposals       [][]string        `json:"proposals"`
	Stale           [][]any           `json:"stale"`
	Family          [][]any           `json:"family"`
	Home            string            `json:"home"`
	Nowhere         string            `json:"nowhere"`
	NowhereAny      string            `json:"nowhereAny"`
	NoParent        string            `json:"noParent"`
	Unmet           [][]any           `json:"unmet"`
	Followup        []string          `json:"followup"`
	FollowupProblem string            `json:"followupProblem"`
	Next            []string          `json:"next"`
	NextBad         string            `json:"nextBad"`
	Existing        string            `json:"existing"`
	WapFamily       string            `json:"wapFamily"`
	WapStop         string            `json:"wapStop"`
	WapSortCode     int               `json:"wapSortCode"`
	WapSort         string            `json:"wapSort"`
	Cleared         []any             `json:"cleared"`
	Files           map[string]string `json:"files"`
	ReadGo          map[string]any    `json:"readGo"`
}

const fixedClock = "2026-01-02T03:04:05+0900"

// oracleWorkspace は Python と Go に読ませるワークスペース。親 i0001 のワークツリーに承認済みチケットを並べ、
// 信頼できない形（中身の矛盾・計画に無い番号・親のワークツリーの外・読めない・識別子違い）と、先行の状態
// （作業中・取り消し・自分・親・循環・無い）、正とするツリーの外に残った提案を作る。
func oracleWorkspace(t *testing.T) string {
	root := testkit.Workspace(t, "i0001", "i0001-01-01")
	testkit.Git(t, root, "remote", "add", "origin", filepath.Join(root, "nowhere.git"))
	home := testkit.Worktree(root, "i0001")
	doing := filepath.Join(home, ".ccnavi", "approved", "doing")
	done := filepath.Join(home, ".ccnavi", "approved", "done")
	testkit.Put(t, filepath.Join(doing, "i0001.md"), parentText("i0001", "  - research", "  - {type: design, after: [1]}"))
	testkit.Put(t, filepath.Join(doing, "i0001-01-01.md"), childText("i0001-01-01", "i0001", "1", "wip/research/*",
		`started_at: "2026-01-01T00:00:00+0900"`, `base_sha: "abc"`))
	testkit.Put(t, filepath.Join(doing, "i0001-02-01.md"), childText("i0001-02-01", "i0001", "2", "wip/design/*",
		"predecessors: [i0001-01-01]"))
	testkit.Put(t, filepath.Join(doing, "i0001-01-02.md"), childText("i0001-01-02", "i0001", "1", "wip/research/a/*",
		`base_sha: "abc"`))
	testkit.Put(t, filepath.Join(doing, "i0001-05-01.md"), childText("i0001-05-01", "i0001", "5", "wip/design/*"))
	testkit.Put(t, filepath.Join(doing, "i0001-02-02.md"), childText("i0001-02-02", "i0001", "2", "wip/design/*",
		"predecessors: [i0001-01-03, i0001-02-02, i0001, i0001-09-01, i0001-01-03]"))
	testkit.Put(t, filepath.Join(doing, "i0001-02-03.md"), childText("i0001-02-03", "i0001", "2", "wip/design/*",
		"predecessors: [i0001-02-04]"))
	testkit.Put(t, filepath.Join(doing, "i0001-02-04.md"), childText("i0001-02-04", "i0001", "2", "wip/design/*",
		"predecessors: [i0001-02-03]"))
	testkit.Put(t, filepath.Join(doing, "bad.md"), "not a ticket\n")
	testkit.Put(t, filepath.Join(doing, "i0001-01-07.md"), childText("i0001-01-08", "i0001", "1", "wip/research/*"))
	testkit.Put(t, filepath.Join(doing, ".i0001.md.abc.part"), "x")
	testkit.Put(t, filepath.Join(done, "i0001-01-03.md"), childText("i0001-01-03", "i0001", "1", "wip/research/*",
		`completed_at: "2026-01-01T00:00:00+0900"`, `cancelled_at: "2026-01-01T00:00:00+0900"`, "cancel_reason: r"))
	testkit.Put(t, filepath.Join(home, "wip", "proposals", "review", "i0001-01-04.md"), childText("i0001-01-04", "i0001", "1",
		"wip/research/*", `completed_at: "2026-01-01T00:00:00+0900"`))
	testkit.Put(t, filepath.Join(home, "wip", "proposals", "review", "i0001-01-05.md"), childText("i0001-01-05", "i0001", "1",
		"wip/research/*"))
	testkit.Put(t, filepath.Join(home, ".ccnavi", "approved", "phases", "i0001", "2.pending"), `{"review": true, "at": "t"}`)
	testkit.Put(t, filepath.Join(home, ".ccnavi", "approved", "phases", "i0001", "i0001-01-01.judge.json"), "{}")
	// 正とするツリー（親のワークツリー）の外のコピー。
	testkit.Put(t, filepath.Join(root, ".ccnavi", "approved", "doing", "i0001-01-01.md"), childText("i0001-01-01", "i0001", "1", "src/*"))
	testkit.Put(t, filepath.Join(root, ".ccnavi", "approved", "doing", "i0001-01-09.md"), childText("i0001-01-09", "i0001", "1", "wip/research/*"))
	// 提案。親のワークツリーの改版と、ワークスペースルートに残った計画の違う版。
	testkit.Put(t, filepath.Join(home, "wip", "proposals", "todo", "i0001.md"), parentText("i0001", "  - research"))
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0001.md"), parentText("i0001", "  - design"))
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0002.md"), parentText("i0002", "  - research"))
	return root
}

func TestMatchesPython(t *testing.T) {
	root := oracleWorkspace(t)
	scratch := t.TempDir()
	pyDir, goDir := filepath.Join(scratch, "py"), filepath.Join(scratch, "go")
	restore, err := fsio.FixClock(fixedClock)
	if err != nil {
		t.Fatal(err)
	}
	writeMarks(t, goDir)
	cleared := marks.Clear(goDir, "i0001", 2, nil)
	restore()

	want := testkit.Answers[approvalAnswers](t, "approval", approvalOracle,
		map[string]string{"root": root, "py": pyDir, "go": goDir, "clock": fixedClock})
	rel := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
	conf := testkit.Settings(t, root)
	raw := ReadRaw(conf, root)
	opened, notes := Scan(conf, root, false, raw)
	closed, cnotes := Scan(conf, root, true, raw)
	review, rnotes := ScanReview(conf, root, raw)
	row := func(ts []*ticket.Ticket) [][]string {
		out := [][]string{}
		for _, x := range ts {
			out = append(out, []string{x.ID, x.Tree, string(x.State), rel(x.Blocked)})
		}
		return out
	}
	check := func(name string, got, want any) {
		t.Helper()
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s:\n got %#v\nwant %#v", name, got, want)
		}
	}
	check("open", row(opened), want.Open)
	check("closed", row(closed), want.Closed)
	check("review", row(review), want.Review)
	var allNotes []string
	for _, n := range append(append(notes, cnotes...), rnotes...) {
		allNotes = append(allNotes, rel(n))
	}
	check("notes", allNotes, want.Notes)

	props := ReadProposals(conf, root, raw)
	var kept [][]string
	var family [][]any
	for _, x := range props.Kept {
		kept = append(kept, []string{x.ID, x.Tree, string(x.State)})
		details := []any{}
		for _, p := range FamilyProblems(conf, root, x, nil) {
			details = append(details, rel(p.Detail))
		}
		family = append(family, []any{x.ID, details})
	}
	check("proposals", kept, want.Proposals)
	check("family", family, want.Family)
	current := ByID(opened)
	var stale [][]any
	for _, s := range props.Stale {
		stale = append(stale, []any{s.Ticket.ID, s.Where, rel(RevisionElsewhereText(conf, root, s.Ticket, s.Where, nil)),
			RevisionElsewhere(s.Ticket, current[s.Ticket.ID])})
	}
	check("stale", stale, want.Stale)

	homeDir, err := HomeDir(conf, root, "i0001-01-01", "i0001", "")
	if err != nil {
		t.Fatal(err)
	}
	check("home", rel(homeDir), want.Home)
	check("nowhere", rel(HomeDirRead(conf, root, "i0099", "", "")), want.Nowhere)
	check("nowhereAny", rel(HomeDirReadAny(conf, root, "i0001", "")), want.NowhereAny)
	_, err = HomeDir(conf, root, "i0099", "", "")
	if err == nil || rel(err.Error()) != want.NoParent {
		t.Errorf("noParent: %v, want %q", err, want.NoParent)
	}

	pool := PredecessorPool(conf, root)
	var unmet [][]any
	for _, x := range opened {
		states, problems := []any{}, []any{}
		for _, p := range UnmetPredecessors(x, pool) {
			states = append(states, []any{p.Ticket, string(p.State), rel(p.Where), rel(p.Label())})
		}
		for _, p := range PredecessorProblems(x, pool, ".ccnavi/approved") {
			problems = append(problems, []any{p.Kind, rel(p.Detail)})
		}
		unmet = append(unmet, []any{x.ID, states, problems})
	}
	check("unmet", unmet, want.Unmet)

	parent := current["i0001"]
	var children []*ticket.Ticket
	for _, x := range append(append([]*ticket.Ticket(nil), opened...), closed...) {
		if x.Parent == "i0001" && x.Phase == 1 {
			children = append(children, x)
		}
	}
	sort.Slice(children, func(i, j int) bool { return children[i].ID < children[j].ID })
	ident, ft, why := followupTicket(conf, root, parent, 1, children, []string{"1 行の指摘", "### 見出し\n本文"}, "op-1")
	text := ""
	if ft != nil {
		text, _ = ticket.Render(ft)
	}
	check("followup", []string{ident, text, why}, want.Followup)
	check("followupProblem", FollowupProblem(conf, root, parent, 1, children, nil), want.FollowupProblem)
	n1, _ := NextChildID(conf, root, "i0001", 1)
	n2, _ := NextChildID(conf, root, "i0001", 2)
	check("next", []string{n1, n2}, want.Next)
	if _, err := NextChildID(conf, root, "i0001", 100); err == nil || err.Error() != want.NextBad {
		t.Errorf("nextBad: %v, want %q", err, want.NextBad)
	}
	check("existing", rel(ExistingTicketFile(conf, root, "I0001-01-01")), want.Existing)

	for name, c := range map[string]struct{ id, want string }{
		"wapFamily": {"i0001-01-01", want.WapFamily}, "wapStop": {"i0099", want.WapStop},
	} {
		var buf bytes.Buffer
		wap.Family(&buf, conf, root, c.id)
		check(name, rel(buf.String()), c.want)
	}
	var out, errOut bytes.Buffer
	code := wap.Sort(&out, &errOut, conf, root, "i0001", "")
	check("wapSort", []any{code, out.String() + errOut.String()}, []any{want.WapSortCode, want.WapSort})

	// 記録: 同じ時刻で書いたバイト列が同じで、相手の書いたものを読める。
	check("cleared", []any{kindsAny(cleared.Kinds), cleared.Failed, stringsAny(cleared.Warnings)}, want.Cleared)
	mine := map[string]string{}
	_ = filepath.WalkDir(goDir, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			data, _ := os.ReadFile(p)
			r, _ := filepath.Rel(goDir, p)
			mine[filepath.ToSlash(r)] = base64.StdEncoding.EncodeToString(data)
		}
		return nil
	})
	check("files", mine, want.Files)
	gotRead := map[string]any{}
	read := map[string]any{}
	for k, v := range marks.Marks(pyDir, "i0001", 1) {
		read[string(k)] = toPlain(v)
	}
	gotRead["marks"] = read
	ready, _ := marks.ReadParent(pyDir, "i0001", marks.Ready)
	gotRead["ready"] = toPlain(ready)
	risk, _ := marks.ReadRecord(pyDir, "i0001", "i0001-01-01", marks.Risk)
	gotRead["risk"] = toPlain(risk)
	entries, problem := marks.ReadLedger(pyDir, "i0001")
	list := []any{}
	for _, e := range entries {
		list = append(list, toPlain(e))
	}
	gotRead["ledger"] = []any{list, problem}
	check("readPython", gotRead, want.ReadGo)
}

func writeMarks(t *testing.T, dir string) {
	t.Helper()
	for _, failed := range []string{
		marks.WriteMark(dir, "i0001", 1, marks.Requested, pyjson.NewObject("by", "cli", "名", "値")),
		marks.WriteMark(dir, "i0001", 1, marks.Reviewed, pyjson.NewObject("at", "given", "by", "decide")),
		marks.WriteMark(dir, "i0001", 2, marks.Pending, pyjson.NewObject("review", true)),
		marks.WriteParent(dir, "i0001", marks.Ready, pyjson.NewObject("mr", 7)),
		marks.WriteRecord(dir, "i0001", "i0001-01-01", marks.Risk, pyjson.NewObject("points", 3, "level", "中")),
		marks.AddLedger(dir, "i0001", []*pyjson.Object{pyjson.NewObject("key", "u/7#t1", "version", 1), pyjson.NewObject("key", "u/7#t2", "at", "kept")}),
		marks.AddLedger(dir, "i0001", []*pyjson.Object{pyjson.NewObject("key", "u/7#t1", "version", 2)}),
	} {
		if failed != "" {
			t.Fatal(failed)
		}
	}
}

// toPlain は JSON の値を encoding/json で読んだ形（数は float64、マップは map[string]any）にする。
func toPlain(v any) any {
	switch x := v.(type) {
	case *pyjson.Object:
		if x == nil {
			return nil
		}
		out := map[string]any{}
		for k, val := range x.All() {
			out[k.(string)] = toPlain(val)
		}
		return out
	case []any:
		out := []any{}
		for _, e := range x {
			out = append(out, toPlain(e))
		}
		return out
	case int:
		return float64(x)
	}
	return v
}

func kindsAny(kinds []marks.Kind) []any {
	out := []any{}
	for _, k := range kinds {
		out = append(out, string(k))
	}
	return out
}

func stringsAny(list []string) []any {
	out := []any{}
	for _, s := range list {
		out = append(out, s)
	}
	return out
}
