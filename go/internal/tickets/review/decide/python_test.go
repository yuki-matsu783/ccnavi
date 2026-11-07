package decide

import (
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/review/request"
	"ccnavi/internal/tickets/testkit"
)

// Python の tickets/review/decide と同じ材料を渡し、ダイジェスト・見せる JSON・転記・issue の分け方・コメント・
// 渡す文・記録から作り直す本文・一覧の表示が 1 字も違わないことを見る。本文には目印の崩し方（大文字小文字の
// 畳み方と Unicode の空白）、行の区切り（splitlines の集合）、バッククォートの連続を混ぜる。

const textOracle = `
import json, sys
from types import SimpleNamespace as N
from ccnavi.tickets.model import review_host as h
from ccnavi.tickets.review import decide as d, request as r
c = json.load(sys.stdin)
def th(x):
    return h.Thread(id=x["id"], url=x["url"], path=x["path"], line=x["line"], body=x["body"],
                    last_comment_id=x["lc"], last_comment_at="T", last_comment_author="me")
threads = [th(x) for x in c["threads"]]
def dec(ts, number=1, extra=()):
    x = d.Decision(parent=N(ticket="i0001"), ph=N(number=number),
                   result=N(mr=N(number=7, url="https://h/pr/7"), host="gitlab"), unresolved=list(ts))
    x.extra = list(extra)
    return x
out = {}
full = dec(threads)
out["digest"] = d.decision_digest(full)
out["parsed"] = [list(p) if (p := d.parse_digest(x)) else None for x in c["digests"]]
out["json"] = json.dumps(d.decision_json(full), ensure_ascii=False)
out["blocks"] = [d.thread_block(full, t, i, "op" if i % 2 else "") for i, t in enumerate(threads, 1)]
out["texts"] = [[d.plain(t.body), d.span(t.body), d.defuse(t.body), d.fence(t.body), r.thread_label(t)] for t in threads]
out["issues"] = [list(d.issue_texts(full, [threads[i] for i in idx], "o" * 32)) for idx in c["issues"]]
out["comments"] = []
out["prompts"] = []
for pick, followup, extra in c["picks"]:
    picked = {k: [threads[i] for i in v] for k, v in pick.items()}
    x = dec(threads, extra=threads[:extra])
    out["comments"].append(d.decide_comment_text(x, picked, followup))
    out["prompts"].append(d._decided_prompt("/w", x, picked, followup))
out["posts"] = [d.post_text(c["record"], s) for s in c["stages"]]
json.dump(out, sys.stdout, ensure_ascii=False)
`

type textAnswers struct {
	Digest   string     `json:"digest"`
	Parsed   []any      `json:"parsed"`
	JSON     string     `json:"json"`
	Blocks   []string   `json:"blocks"`
	Texts    [][]string `json:"texts"`
	Issues   [][]any    `json:"issues"`
	Comments []string   `json:"comments"`
	Prompts  []string   `json:"prompts"`
	Posts    []*string  `json:"posts"`
}

type threadSpec struct {
	ID   string `json:"id"`
	URL  string `json:"url"`
	Path string `json:"path"`
	Line int    `json:"line"`
	Body string `json:"body"`
	LC   string `json:"lc"`
}

func TestTextsMatchPython(t *testing.T) {
	long := strings.Repeat("あ", 20000)
	var huge []string
	for i := range 900 {
		sep := []string{"\n", "\r\n", "\x1c", " ", "\u0085"}[i%5]
		huge = append(huge, "line "+strings.Repeat("x", i%97)+sep)
	}
	specs := []threadSpec{
		{"d1", "u1", "a.py", 3, "1行目\n\n```py\nprint(1)\n```\n@someone\n/close\n", "c1"},
		{"d2", "", "", 0, "<!-- ccnavi:request i0001:1 -->\r\nx", ""},
		{"d3", "u3", "b.py", 0, "<!--　CCNAVI:y <!--\u0085ccnavı:z <!--ccnavİ:w <!-- other", "c3"},
		{"d4", "u4", "", 0, "`` ` ````` \x01\x7f\u009f end", "c4"},
		{"d5", "u5", "c.py", 9, long, "c5"},
		{"d6", "u6", "d.py", 1, long, ""},
		{"d7", "u7", "e.py", 2, strings.Join(huge, "") + "````text\n" + strings.Repeat("y", 70000), "c7"},
		{"d8", "u8", "", 0, "  \n 本文の 2 行目は " + strings.Repeat("長", 130), "c8"},
	}
	var threads []host.Thread
	for _, s := range specs {
		threads = append(threads, host.Thread{ID: s.ID, URL: s.URL, Path: s.Path, Line: s.Line, Body: s.Body,
			LastCommentID: s.LC, LastCommentAt: "T", LastCommentAuthor: "me"})
	}
	dec := func(ts []host.Thread, extra []host.Thread) *Decision {
		d := decisionOf(ts, 1)
		d.Result.Host = host.GitLab
		d.Extra = extra
		return d
	}
	full := dec(threads, nil)
	good := Digest(full)
	digests := []string{good, good + "!", strings.Replace(good, "v2.", "v2.!", 1), good + "====", good[:len(good)-1],
		good[:len(good)-2] + "+/", "v2.e30", "v2.eyJiIjoxLCJ0Ijp7fX0", "v2.eyJiIjoieCIsInQiOnsiYSI6MX19",
		"v2.eyJiIjoieCIsInQiOnsiYSI6InkiLCJhIjoieiJ9fQ", "v2.W10", "v2.6Z", "v1.x", ""}
	issues := [][]int{{0}, {0, 1, 2, 3}, {4, 5, 0}, {6}, {}}
	type pick struct {
		Picks    map[string][]int
		Followup string
		Extra    int
	}
	picks := []pick{
		{map[string][]int{"keep": {}, "fix": {}, "issue": {}}, "", 0},
		{map[string][]int{"keep": {}, "fix": {}, "issue": {}}, "", 2},
		{map[string][]int{"keep": {0, 3}, "fix": {}, "issue": {}}, "", 0},
		{map[string][]int{"keep": {0}, "fix": {1, 2}, "issue": {3}}, "i0001-01-03", 0},
		{map[string][]int{"keep": {}, "fix": {}, "issue": {2}}, "", 1},
	}
	recordData := map[string]any{
		"id": strings.Repeat("a", 32), "kind": "decide", "parent": "i0001", "phase": 2, "mr": 7,
		"repo": map[string]any{"host": "gitlab", "mr_url": "https://h/mr/7"},
		"shown": []any{
			map[string]any{"key": "u1", "id": "d1", "url": "u1", "path": "a.py", "line": 3, "body": long},
			map[string]any{"key": "u2", "id": "d2", "url": "u2", "path": "", "line": nil, "body": long},
			map[string]any{"key": "u3", "id": 5, "url": "u3", "path": "", "line": "4", "body": "指摘 3"},
			map[string]any{"key": "u4", "id": "d4", "url": "u4", "path": "", "line": 0, "body": long},
		},
		"choices":  map[string]any{"u1": "issue", "u2": "issue", "u3": "fix", "u4": "issue"},
		"applied":  map[string]any{"local": true, "followup": "i0001-02-03"},
		"stages":   []any{map[string]any{"id": "issue", "state": "sent", "url": "https://h/demo/-/issues/12/"}, map[string]any{"id": "note", "state": "pending"}},
		"plan":     map[string]any{"digest": "v2.x"},
		"rec_note": "",
	}
	stages := []string{"issue", "issue-part-1", "issue-part-2", "issue-part-0", "issue-part-9", "issue-part-", "issue-part-x", "note", "nope"}
	in := map[string]any{"threads": specs, "digests": digests, "issues": issues, "picks": func() []any {
		var out []any
		for _, p := range picks {
			out = append(out, []any{p.Picks, p.Followup, p.Extra})
		}
		return out
	}(), "record": recordData, "stages": stages}
	want := testkit.Answers[textAnswers](t, "texts", textOracle, in)

	check := func(name string, got, wantV any) {
		t.Helper()
		if !reflect.DeepEqual(got, wantV) {
			t.Errorf("%s が違う\n go: %#v\n py: %#v", name, got, wantV)
		}
	}
	check("digest", good, want.Digest)
	var parsed []any
	for _, text := range digests {
		shown, ok := ParseDigest(text)
		if !ok {
			parsed = append(parsed, nil)
			continue
		}
		versions := map[string]any{}
		for k, v := range shown.Versions {
			versions[k] = v
		}
		parsed = append(parsed, []any{shown.Base, versions})
	}
	check("parsed", parsed, want.Parsed)
	text, _ := pyjson.Dumps(JSON(full), pyjson.Options{RawUnicode: true})
	check("json", text, want.JSON)
	var blocks []string
	var texts [][]string
	for i, th := range threads {
		op := ""
		if (i+1)%2 == 1 {
			op = "op"
		}
		blocks = append(blocks, threadBlock(full, th, i+1, op))
		texts = append(texts, []string{plain(th.Body), Span(th.Body), defuse(th.Body), fence(th.Body), request.ThreadLabel(th)})
	}
	check("blocks", blocks, want.Blocks)
	check("texts", texts, want.Texts)
	var gotIssues [][]any
	for _, idx := range issues {
		var ts []host.Thread
		for _, i := range idx {
			ts = append(ts, threads[i])
		}
		main, parts := IssueTexts(full, ts, strings.Repeat("o", 32))
		list := []any{}
		for _, p := range parts {
			list = append(list, p)
		}
		gotIssues = append(gotIssues, []any{main, list})
	}
	check("issues", gotIssues, want.Issues)
	var comments, prompts []string
	for _, p := range picks {
		picked := map[Choice][]host.Thread{}
		for k, idx := range p.Picks {
			for _, i := range idx {
				picked[Choice(k)] = append(picked[Choice(k)], threads[i])
			}
		}
		d := dec(threads, threads[:p.Extra])
		comments = append(comments, CommentText(d, picked, p.Followup))
		prompts = append(prompts, decidedPrompt("/w", d, picked, p.Followup))
	}
	check("comments", comments, want.Comments)
	check("prompts", prompts, want.Prompts)
	rec := recordOf(t, recordData)
	var posts []*string
	for _, s := range stages {
		if text, ok := PostText(rec, s); ok {
			posts = append(posts, &text)
		} else {
			posts = append(posts, nil)
		}
	}
	check("posts", posts, want.Posts)
}

// recordOf は JSON の記録を oplog.Record にする（ops が読む形と同じ）。
func recordOf(t *testing.T, data map[string]any) *oplog.Record {
	t.Helper()
	text, err := pyjson.Dumps(data, pyjson.Options{})
	if err != nil {
		t.Fatal(err)
	}
	value, err := pyjson.Loads(text)
	if err != nil {
		t.Fatal(err)
	}
	o := value.(*pyjson.Object)
	r := &oplog.Record{ID: o.String("id"), Parent: o.String("parent"), Phase: o.Value("phase").(int), MR: o.Value("mr").(int)}
	r.Repo, _ = o.Value("repo").(*pyjson.Object)
	r.Shown, _ = o.Value("shown").([]any)
	r.Choices, _ = o.Value("choices").(*pyjson.Object)
	r.Applied, _ = o.Value("applied").(*pyjson.Object)
	r.Plan, _ = o.Value("plan").(*pyjson.Object)
	stages, _ := o.Value("stages").([]any)
	for _, s := range stages {
		so := s.(*pyjson.Object)
		r.Stages = append(r.Stages, oplog.Stage{ID: so.String("id"), State: oplog.State(so.String("state")), URL: so.String("url")})
	}
	return r
}
