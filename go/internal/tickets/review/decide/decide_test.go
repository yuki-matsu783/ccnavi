package decide

import (
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

func thread(key, body string) host.Thread {
	return host.Thread{ID: "id-" + key, URL: key, Path: "a.py", Line: 3, Body: body}
}

func decisionOf(threads []host.Thread, number int) *Decision {
	return &Decision{
		Parent:     &ticket.Ticket{ID: "i0001"},
		Phase:      &phase.Phase{Number: number},
		Result:     host.Result{Host: host.GitHub, MR: &host.MergeRequest{Number: 7, URL: "https://h/pr/7"}},
		Unresolved: threads,
	}
}

// 版はキーと本文（改行コードと Unicode の正規化を除いたもの）で決まる。位置だけでは変わらない。
func TestThreadVersion(t *testing.T) {
	v := host.ThreadVersion(thread("u1", "本文"))
	if len(v) != 32 || strings.Trim(v, "0123456789abcdef") != "" {
		t.Fatalf("形: %q", v)
	}
	if v == host.ThreadVersion(thread("u2", "本文")) || v == host.ThreadVersion(thread("u1", "本文2")) {
		t.Error("キーか本文で変わらない")
	}
	base := host.ThreadVersion(thread("u1", "a\nb\nガ"))
	if base != host.ThreadVersion(thread("u1", "a\r\nb\rガ")) || base != host.ThreadVersion(thread("u1", "a\nb\nカ\u3099")) {
		t.Error("改行か正規化で変わった")
	}
	moved := thread("u1", "x")
	moved.Line = 9
	if host.ThreadVersion(thread("u1", "x")) != host.ThreadVersion(moved) {
		t.Error("位置で変わった")
	}
}

func TestDigest(t *testing.T) {
	a, b := thread("u1", "x"), thread("u2", "y")
	if Digest(decisionOf([]host.Thread{a, b}, 1)) != Digest(decisionOf([]host.Thread{b, a}, 1)) {
		t.Error("並びで変わった")
	}
	shown, ok := ParseDigest(Digest(decisionOf([]host.Thread{a, b}, 1)))
	if !ok || !reflect.DeepEqual(shown.Keys, []string{"u1", "u2"}) {
		t.Fatalf("読み戻し: %+v %v", shown, ok)
	}
	// 版 1 のダイジェストと壊れた値は読まない。
	for _, text := range []string{strings.Repeat("a", 64), "", "v2.", "v2.!!!", "v2.QQ", "v2.é"} {
		if _, ok := ParseDigest(text); ok {
			t.Errorf("%q を読んだ", text)
		}
	}
	base, _ := ParseDigest(Digest(decisionOf([]host.Thread{a}, 1)))
	cases := []struct {
		name string
		d    *Decision
		want []string
	}{
		{"追加だけ", decisionOf([]host.Thread{a, b}, 1), nil},
		{"本文が変わった", decisionOf([]host.Thread{thread("u1", "changed")}, 1), []string{"u1"}},
		{"無くなった", decisionOf([]host.Thread{b}, 1), []string{"u1"}},
		{"フェーズが違う", decisionOf([]host.Thread{a}, 2), []string{target}},
	}
	for _, c := range cases {
		if got := ChangedSinceShown(c.d, base); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: %v", c.name, got)
		}
	}
	other := decisionOf([]host.Thread{a}, 1)
	other.Result.MR.Number = 8
	if got := ChangedSinceShown(other, base); !reflect.DeepEqual(got, []string{target}) {
		t.Errorf("MR が違う: %v", got)
	}
}

func TestJSON(t *testing.T) {
	th := thread("u1", "x")
	th.LastCommentID, th.LastCommentAt, th.LastCommentAuthor = "c9", "T", "me"
	data := JSON(decisionOf([]host.Thread{th}, 1))
	text, _ := pyjson.Dumps(data, pyjson.Options{RawUnicode: true})
	want := `{"version": 2, "parent": "i0001", "phase": 1, "mr": {"number": 7, "url": "https://h/pr/7"}, "threads": [{"key": "u1", "url": "u1", "path": "a.py", "line": 3, "body": "x", "version": "` +
		host.ThreadVersion(th) + `", "last_comment": {"id": "c9", "at": "T", "author": "me"}}], "digest": "`
	if !strings.HasPrefix(text, want) || !strings.HasPrefix(data.String("digest"), "v2.") {
		t.Errorf("%s", text)
	}
}

func TestTranscribe(t *testing.T) {
	body := "1行目\n\n```py\nprint(1)\n```\n@someone\n/close\n"
	block := threadBlock(decisionOf(nil, 1), thread("u1", body), 1, strings.Repeat("o", 32))
	for _, want := range []string{"### 指摘 1", "キー: `u1`", "位置: `a.py:3`", "版: " + host.ThreadVersion(thread("u1", body)),
		"取得元: `github`", "操作: `" + strings.Repeat("o", 32) + "`", "````text\n", strings.TrimRight(body, "\n")} {
		if !strings.Contains(block, want) {
			t.Errorf("%q が無い", want)
		}
	}
	if !strings.HasSuffix(strings.TrimSpace(block), "````") {
		t.Error("閉じていない")
	}
	for in, want := range map[string]string{"plain": "```", "a ``` b": "````", "`````` x": "```````"} {
		if got := fence(in); got != want {
			t.Errorf("%q: %q", in, got)
		}
	}
	bare := thread("u1", "x")
	bare.Path, bare.Line = "", 0
	if block := threadBlock(decisionOf(nil, 1), bare, 1, ""); strings.Contains(block, "位置:") || strings.Contains(block, "操作:") {
		t.Error(block)
	}
	// 転記した本文の中の目印は壊す（機構の投稿に見えないように）。大文字小文字と空白を問わない。
	if got := defuse("a <!--  CCnavi:request x --> b <!-- other -->"); got != "a <!‐-  CCnavi:request x --> b <!-- other -->" {
		t.Errorf("%q", got)
	}
	if got := Span("a`b\nc "); got != "`ab c `" {
		t.Errorf("%q", got)
	}
}

func TestIssueTexts(t *testing.T) {
	d := decisionOf([]host.Thread{thread("u1", "x")}, 1)
	main, parts := IssueTexts(d, d.Unresolved, "")
	if parts != nil || !strings.HasPrefix(main, "レビューで残った指摘（i0001 のフェーズ 1）") ||
		!strings.Contains(main, copyHeading) || !strings.Contains(main, "### 指摘 1") {
		t.Errorf("短い: %q %v", main, parts)
	}
	var threads []host.Thread
	for _, k := range []string{"u1", "u2", "u3", "u4", "u5"} {
		threads = append(threads, thread(k, strings.Repeat("あ", 20000)))
	}
	main, parts = IssueTexts(decisionOf(threads, 1), threads, "")
	if chars(main) > issueBodyLimit || !strings.Contains(main, "続きのコメント") || len(parts) < 2 {
		t.Fatalf("長い: %d %d", chars(main), len(parts))
	}
	total := 0
	for _, p := range parts {
		if chars(p) > issueBodyLimit {
			t.Errorf("上限を超えた: %d", chars(p))
		}
		total += strings.Count(p, "あ")
	}
	if total != 5*20000 {
		t.Errorf("欠けた: %d", total)
	}
	var lines []string
	for i := range 1200 {
		lines = append(lines, "line "+strconv.Itoa(i)+" "+strings.Repeat("x", 80))
	}
	huge := thread("u1", strings.Join(lines, "\n"))
	_, parts = IssueTexts(decisionOf([]host.Thread{huge}, 1), []host.Thread{huge}, "")
	if len(parts) < 2 {
		t.Fatal("分かれない")
	}
	for _, p := range parts {
		if chars(p) > issueBodyLimit || strings.Count(p, "```")%2 != 0 {
			t.Errorf("片が上限を超えたかフェンスが閉じていない")
		}
	}
	joined := strings.Join(parts, "\n")
	if !strings.Contains(joined, "line 0 ") || !strings.Contains(joined, "line 1199 ") {
		t.Error("欠けた")
	}
}

func record(choices *pyjson.Object, stages []oplog.Stage) *oplog.Record {
	return &oplog.Record{
		ID: strings.Repeat("a", 32), Kind: oplog.KindDecide, Parent: "i0001", Phase: 2, MR: 7,
		Repo: pyjson.NewObject("host", "gitlab", "mr_url", "https://h/mr/7"),
		Shown: []any{
			pyjson.NewObject("key", "u1", "id", "d1", "url", "u1", "path", "a.py", "line", 3, "body", "指摘 1"),
			pyjson.NewObject("key", "u2", "id", "d2", "url", "u2", "path", "", "line", 0, "body", "指摘 2"),
			pyjson.NewObject("key", "u3", "id", "d3", "url", "u3", "path", "", "line", 0, "body", "指摘 3"),
		},
		Choices: choices,
		Applied: pyjson.NewObject("local", true, "followup", "i0001-02-03"),
		Stages:  stages,
	}
}

var allChoices = pyjson.NewObject("u1", "issue", "u2", "keep", "u3", "fix")

// 保存した判断から、投稿の本文を作り直す（記録は本文を持たない）。
func TestPostText(t *testing.T) {
	pending := []oplog.Stage{{ID: "issue", State: oplog.StatePending}, {ID: "note", State: oplog.StatePending}}
	text, ok := PostText(record(allChoices, pending), "issue")
	if !ok || !strings.Contains(text, "### 指摘 1") || !strings.Contains(text, "指摘 1") || strings.Contains(text, "指摘 2") ||
		strings.Contains(text, "指摘 3") || !strings.Contains(text, "操作: `"+strings.Repeat("a", 32)+"`") || !strings.Contains(text, "取得元: `gitlab`") {
		t.Errorf("issue: %q", text)
	}
	text, _ = PostText(record(allChoices, pending), "note")
	lines := strings.Split(text, "\n")
	if !strings.HasPrefix(text, host.MarkerDecide) || !strings.Contains(lines[1], "ユーザの判断（経路: ") ||
		!strings.Contains(text, "issue に回す:\n- u1") || !strings.Contains(text, "対応しない（受け入れて進む）:\n- u2") ||
		!strings.Contains(text, "続きの子チケット `i0001-02-03`") {
		t.Errorf("note: %q", text)
	}
	sent := []oplog.Stage{{ID: "issue", State: oplog.StateSent, URL: "https://h/demo/-/issues/12"}, {ID: "note", State: oplog.StatePending}}
	if text, _ := PostText(record(allChoices, sent), "note"); !strings.Contains(text, "issue に回した分: #12 https://h/demo/-/issues/12") {
		t.Errorf("作った issue: %q", text)
	}
	for _, c := range []struct {
		choices *pyjson.Object
		stage   string
	}{{allChoices, "nope"}, {allChoices, "issue-part-1"}, {allChoices, "issue-part-x"}, {pyjson.NewObject("u1", "keep"), "issue"}} {
		if _, ok := PostText(record(c.choices, pending), c.stage); ok {
			t.Errorf("%s: 本文を作った", c.stage)
		}
	}
}

func TestResumePlan(t *testing.T) {
	path := filepath.Join(t.TempDir(), "plan.json")
	if ResumePlan(record(allChoices, nil), path) != 0 {
		t.Fatal("書けない")
	}
	plan := host.ReadAcceptPlan(path)
	if plan == nil || !reflect.DeepEqual(plan.IDs, []string{"d1", "d2"}) || plan.Op != strings.Repeat("a", 32) || plan.Digest != "" {
		t.Errorf("%+v", plan)
	}
}

// 決めたあとに渡す文。0 件のときに 0 件の内訳や選び方を並べない。fix のときだけ resolve はユーザがすると言う。
func TestDecidedPrompt(t *testing.T) {
	d := &Decision{Parent: &ticket.Ticket{ID: "i0064"}, Phase: &phase.Phase{Number: 1}}
	th := host.Thread{URL: "u1", Path: "a.py", Line: 1, Body: "x"}
	none := decidedPrompt("/w", d, map[Choice][]host.Thread{}, "")
	if !strings.Contains(none, "フェーズ 1 をレビュー済みにした") || !strings.Contains(none, "未解決（Unresolved）の指摘なし") ||
		strings.Contains(none, "0 件") || strings.Contains(none, "対応方針を決めた") {
		t.Errorf("0 件: %q", none)
	}
	keep := decidedPrompt("/w", d, map[Choice][]host.Thread{Keep: {th}}, "")
	if !strings.Contains(keep, "未解決（Unresolved）の指摘の対応方針を決めた（対応しない 1 件）") || strings.Contains(keep, "0 件") ||
		!strings.Contains(keep, "ccnavi-ticket.sh status") || !strings.Contains(keep, "置き場のファイルを自分で探さない") ||
		strings.Contains(keep, "fix を理由に resolve しない") {
		t.Errorf("keep: %q", keep)
	}
	fix := decidedPrompt("/w", d, map[Choice][]host.Thread{Fix: {th}}, "i0064-01-02")
	if !strings.Contains(fix, "修正を確認したら、ユーザがホスト上で該当スレッドを resolve する。") ||
		!strings.Contains(fix, "エージェントは fix を理由に resolve しない。") || !strings.Contains(fix, "- u1 a.py:1 x") {
		t.Errorf("fix: %q", fix)
	}
}
