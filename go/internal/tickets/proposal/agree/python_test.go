package agree

import (
	"encoding/base64"
	"encoding/json"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/pyoracle"
	"ccnavi/internal/tickets/model/ticket"
)

// Python の tickets/proposal（agree・agree_candidates・agree_digest・agree_screen・plan_order の preview）と同じ
// ワークスペースを 2 つ作って読ませ、承認の対象の分け方・苦情・画面・指紋・確かめの本文・preview の JSON・
// 承認の書き込みの並び・承認の事実の文が 1 字も違わないことを見る。

const agreeOracle = `
import base64, io, json, sys
from ccnavi.infra import fsio, settings
from ccnavi.tickets.proposal import agree, agree_digest, agree_screen
c = json.load(sys.stdin)
root = c["root"]
def rel(s):
    return s.replace(root, "<root>") if isinstance(s, str) else s
conf, _ = settings.load(root)
def gathered(only):
    buf = io.StringIO()
    g = agree.gather(buf, conf, root, only)
    d = agree_digest.proposals_digest(g.batch)
    v = agree.verify_verdict(root, g, conf.tickets)
    h = agree.verify_verdict(root, g, conf.tickets, ("held", "止めた理由\n"))
    with fsio.clock(c["stamp"]):
        body = json.dumps(agree.preview_body(conf, root, g, d), ensure_ascii=False)
    return g, {"stderr": rel(buf.getvalue()), "ids": g.identifiers,
        "rejected": [[t.ticket, [rel(str(p)) for p in ps]] for t, ps in g.rejected],
        "problems": [rel(p) for p in g.problems], "note": g.note, "refused": g.refused,
        "nothing": g.nothing_pending, "broken": g.broken, "elsewhere": [rel(e) for e in g.elsewhere],
        "text": rel(g.text), "digest": d, "verify": [v.ok, v.reason, rel(v.text)], "held": [h.ok, h.reason, rel(h.text)],
        "body": rel(body)}
out = {"gather": {}}
for name, only in c["only"].items():
    out["gather"][name] = gathered(only)[1]
g, _ = gathered(None)
planned = agree.plan_batch(root, conf, g.batch, c["stamp"])
items = []
for it in planned.stage.items:
    if isinstance(it, fsio.Line):
        items.append(["line", rel(it.text), it.group, it.stream])
    elif isinstance(it, fsio.Call):
        items.append(["call", it.group, [rel(x) for x in it.lines]])
    else:
        p = it.policy
        content = None if it.content is None else base64.b64encode(it.content).decode()
        items.append(["op", rel(it.path), content, rel(it.source), p.on_fail, rel(p.message), p.prefix,
                      [rel(u) for u in p.undo], p.places, p.group, p.ticket, p.tag])
out["plan"] = {"stopped": list(planned.stopped) if planned.stopped else None, "items": items}
tickets = [cand.ticket for cand in g.batch]
out["approved"] = [rel(agree_screen.approved_text(tickets, {"i0002"}, root, w, n)) for w, n in c["approved"]]
out["approvedChild"] = rel(agree_screen.approved_text([cand.ticket for cand in g.batch if cand.ticket.is_child], set(), root))
json.dump(out, sys.stdout, ensure_ascii=False)
`

const fixedStamp = "2026-01-06T03:04:05+0900"

var onlyCases = map[string][]string{
	"all": nil, "parent": {"i0001"}, "unknown": {"nope", "i0001"}, "empty": {""}, "child": {"i0001-01-01"},
	"revisionChild": {"i0002-02-01"},
}

var approvedCases = [][2]any{
	{nil, []string{}},
	{[]map[string]string{
		{"ticket": "i0001-01-01", "path": "/w/a", "state": "create", "base": "i0001"},
		{"ticket": "i0001-01-02", "path": "/w/b", "state": "exists", "base": "i0001"},
	}, []string{"親のワークツリーに変更が残る"}},
}

// agreeWorkspace は Python と Go に読ませるワークスペース。
//
//	i0001 新規の親（計画 3 つ）。1 番の子は通り、範囲が親の外に出る子もある。2 番の子は 1 番が閉じていないので外れる
//	i0002 承認済みの親の改版（ステップと範囲を足す）。1 番の子は閉じている。同じ承認に 2 番の子と、閉じた 1 番に足す子が入る
//	i0003 欄の誤り（workflow・スクリプトだけが書く欄・続きの子の目印・human_review の短縮形）で外れる
//	i0004 計画の無い親（warn だけで通る）
func agreeWorkspace(t *testing.T) string {
	root := workspace(t, "i0001", "i0002", "i0003", "i0004")
	todo := func(parent, id, text string) { put(t, filepath.Join(todoDir(root, parent), id+".md"), text) }
	plan3 := []string{"  - research", "  - {type: design, after: [1]}", "  - {type: implement, after: [2]}"}
	todo("i0001", "i0001", parentText("i0001", plan3))
	todo("i0001", "i0001-01-01", childText("i0001-01-01", "i0001", "1", []string{"wip/research/*"}))
	todo("i0001", "i0001-01-02", childText("i0001-01-02", "i0001", "1", []string{"wip/research/a/*", "docs/*"}))
	todo("i0001", "i0001-02-01", childText("i0001-02-01", "i0001", "2", []string{"wip/design/*"}))

	approved := parentText("i0002", plan3[:2])
	put(t, filepath.Join(approvedDir(root, "i0002"), "doing", "i0002.md"), approved)
	put(t, filepath.Join(approvedDir(root, "i0002"), "done", "i0002-01-01.md"),
		childText("i0002-01-01", "i0002", "1", []string{"wip/research/*"}, `completed_at: "2026-01-01T00:00:00+0900"`))
	put(t, filepath.Join(approvedDir(root, "i0002"), "phases", "i0002", "1.skipped"), `{"tickets": ["i0002-01-01"]}`)
	revised := strings.Replace(parentText("i0002", plan3), "allow:\n", "allow:\n  - match: Write|Edit\n    glob: \"tests/*\"\n", 1)
	todo("i0002", "i0002", revised)
	todo("i0002", "i0002-02-01", childText("i0002-02-01", "i0002", "2", []string{"wip/design/*"}))
	todo("i0002", "i0002-01-02", childText("i0002-01-02", "i0002", "1", []string{"wip/research/*"}))

	todo("i0003", "i0003", parentText("i0003", plan3[:1], "workflow: dag", `started_at: "2026-01-01"`, "followup_of: x",
		"phase_note: [1, 2]"))
	todo("i0004", "i0004", strings.Replace(parentText("i0004", nil), "human_review:\n  required: true\n  reason: t\n",
		"human_review: true\n", 1))
	return root
}

func TestMatchesPython(t *testing.T) {
	pyRoot, goRoot := agreeWorkspace(t), agreeWorkspace(t)
	want := pyoracle.Answers[map[string]any](t, "agree", agreeOracle, map[string]any{
		"root": pyRoot, "stamp": fixedStamp, "only": onlyCases, "approved": approvedCases,
	})
	got := goAnswers(t, goRoot)
	for key, w := range want {
		if g := normalize(t, got[key]); !reflect.DeepEqual(g, w) {
			a, _ := json.MarshalIndent(g, "", " ")
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

func goAnswers(t *testing.T, root string) map[string]any {
	rel := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
	rels := func(ss []string) []string {
		out := []string{}
		for _, s := range ss {
			out = append(out, rel(s))
		}
		return out
	}
	conf := settingsOf(t, root)
	gathered := func(only []string) (*Gathered, map[string]any) {
		var buf strings.Builder
		g := Gather(&buf, conf, root, only)
		d := ProposalsDigest(g.Batch)
		v := VerifyVerdict(root, g, conf.Tickets, nil)
		h := VerifyVerdict(root, g, conf.Tickets, &Held{"held", "止めた理由\n"})
		restore, err := fsio.FixClock(fixedStamp)
		if err != nil {
			t.Fatal(err)
		}
		body, err := pyjson.Dumps(PreviewBody(conf, root, g, d), pyjson.Options{RawUnicode: true})
		restore()
		if err != nil {
			t.Fatal(err)
		}
		rejected := []any{}
		for _, r := range g.Rejected {
			ps := []string{}
			for _, p := range r.Problems {
				ps = append(ps, rel(p.String()))
			}
			rejected = append(rejected, []any{r.Ticket.ID, ps})
		}
		return g, map[string]any{"stderr": rel(buf.String()), "ids": list(g.Identifiers()), "rejected": rejected,
			"problems": rels(g.Problems), "note": g.Note, "refused": g.Refused, "nothing": g.NothingPending,
			"broken": g.Broken, "elsewhere": rels(g.Elsewhere), "text": rel(g.Text()), "digest": d,
			"verify": []any{v.OK, v.Reason, rel(v.Text)}, "held": []any{h.OK, h.Reason, rel(h.Text)}, "body": rel(body)}
	}
	out := map[string]any{}
	all := map[string]any{}
	for name, only := range onlyCases {
		_, all[name] = gathered(only)
	}
	out["gather"] = all
	g, _ := gathered(nil)
	planned, err := PlanBatch(conf, root, g.Batch, fixedStamp)
	if err != nil {
		t.Fatal(err)
	}
	items := []any{}
	for _, it := range planned.Stage.Items {
		switch x := it.(type) {
		case fsio.Line:
			items = append(items, []any{"line", rel(x.Text), x.Group, x.Stream})
		case fsio.Call:
			items = append(items, []any{"call", x.Group, rels(x.Lines)})
		case *fsio.Op:
			var content any
			if !x.Gone {
				content = base64.StdEncoding.EncodeToString(x.Content)
			}
			p := x.Policy
			items = append(items, []any{"op", rel(x.Path), content, rel(x.Source), p.OnFail, rel(p.Message), p.Prefix,
				rels(p.Undo), p.Places, p.Group, p.Ticket, p.Tag})
		}
	}
	var stopped any
	if planned.Stopped != nil {
		stopped = []string{planned.Stopped.Ticket, planned.Stopped.Reason}
	}
	out["plan"] = map[string]any{"stopped": stopped, "items": items}
	var tickets, children []*ticket.Ticket
	for _, c := range g.Batch {
		tickets = append(tickets, c.Ticket)
		if c.Ticket.IsChild() {
			children = append(children, c.Ticket)
		}
	}
	texts := []string{}
	texts = append(texts, rel(ApprovedText(tickets, map[string]bool{"i0002": true}, root, nil, nil)))
	texts = append(texts, rel(ApprovedText(tickets, map[string]bool{"i0002": true}, root, []ChildWorktree{
		{"i0001-01-01", "/w/a", WorktreeCreate, "i0001"}, {"i0001-01-02", "/w/b", WorktreeExists, "i0001"},
	}, []string{"親のワークツリーに変更が残る"})))
	out["approved"] = texts
	out["approvedChild"] = rel(ApprovedText(children, map[string]bool{}, root, nil, nil))
	return out
}
