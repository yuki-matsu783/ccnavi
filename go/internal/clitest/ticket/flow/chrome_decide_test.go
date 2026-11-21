package flow

// Chrome の decide（残った指摘の対応方針を決める）の入口（go/cmd/ccnavi-chrome）の受入テスト。
// 元: tests/ticket/test_chrome_decide.py。
//
//  1. 見せる指摘とダイジェストが、手元の --preview --json と同じ（版 2 の形）
//  2. keep・issue・fix が混ざった選択、全部 keep、全部 fix、指摘が 0 件の書くものとホストの計画
//  3. 直す指摘（fix）には、返信も解決も付かない（計画で見る）
//  4. 変更要求のレビューは通さない。見せた指摘と今の指摘が違えば、何も積まない
//  5. 見せたあとに増えた指摘は、食い違いに数えず未対応として残す。受け入れ済み（carry）の扱い
//  6. issue の操作ID の印が、同じ指摘なら同じ、指摘が変われば違う
//  7. 続きの子の識別子が、親のブランチ上の既存のチケットと重ならない
//  8. 手元の decide と同じ書くものになる（レビュー済みのマーカー・続きの子）
import (
	"ccnavi/internal/harness/ticketkit"
	"encoding/json"
	"fmt"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"
	"unicode/utf8"
)

// 見せる形の版（decide の版 2）と、ホストへ書く本文の目印・差し込み口・上限。
const (
	decideVersion  = 2
	markerDecide   = "<!-- ccnavi:decide -->"
	markerAccept   = "<!-- ccnavi:accept -->"
	issueURLSlot   = "{issue_url}"
	issueLinksSlot = "{issue_links}"
	issueBodyLimit = 59000
	maxIssues      = 40
)

// dthread は指摘 1 件。
func dthread(id, body string) map[string]any {
	return map[string]any{"id": id, "resolved": false, "url": "u-" + id, "path": "src/a/x.py", "line": 3, "body": body}
}

func resolvedThread(id, body string) map[string]any {
	t := dthread(id, body)
	t["resolved"] = true
	return t
}

// decideKit は フェーズ 1 を依頼まで進めた木と、その時点の compare。
type decideKit struct {
	t       *testing.T
	tr      *Tree
	fixture ticketkit.Fixture
	compare map[string]any
}

func newDecideKit(t *testing.T) *decideKit {
	t.Helper()
	tr := chromeTree(t)
	f := tr.reviewedPhaseOne()
	tr.CommitParent("requested")
	return &decideKit{t: t, tr: tr, fixture: f, compare: tr.hostCompare()}
}

func (k *decideKit) result(threads []map[string]any, reviews ...map[string]any) map[string]any {
	ts := make([]any, len(threads))
	for i, th := range threads {
		ts[i] = th
	}
	rs := make([]any, len(reviews))
	for i, r := range reviews {
		rs[i] = r
	}
	return map[string]any{"host": "fixture", "mr": map[string]any{"number": 7, "url": "https://example/mr/7"}, "threads": ts, "reviews": rs}
}

func (k *decideKit) request(mode string, result any, withCompare bool, extra map[string]any) map[string]any {
	e := map[string]any{"phase": 1, "result": result, "mode": mode, "actor": map[string]any{"account": "alice"}}
	if withCompare {
		e["compare"] = k.compare
	}
	for key, v := range extra {
		e[key] = v
	}
	return k.tr.chromeRequest("decide", "i0001", map[string]string{"i0001": js(k.compare, "head")}, e)
}

func (k *decideKit) ask(mode string, result any, extra map[string]any) map[string]any {
	k.t.Helper()
	return k.tr.askChrome(k.request(mode, result, true, extra))
}

func noProblems(t testing.TB, a map[string]any) {
	t.Helper()
	if p, ok := a["problems"].([]any); !ok || len(p) != 0 {
		t.Fatalf("problems = %v\n%v", a["problems"], a)
	}
}

func (k *decideKit) preview(threads []map[string]any, extra map[string]any) map[string]any {
	k.t.Helper()
	a := k.ask("preview", k.result(threads), extra)
	noProblems(k.t, a)
	return jm(a, "preview")
}

// apply は見せた指摘（shown。nil なら今の指摘）のダイジェストで、選択を置く答え。
func (k *decideKit) apply(threads []map[string]any, choices map[string]string, shown []map[string]any, result map[string]any, extra map[string]any) map[string]any {
	k.t.Helper()
	if shown == nil {
		shown = threads
	}
	digest := js(k.preview(shown, nil), "digest")
	if result == nil {
		result = k.result(threads)
	}
	e := map[string]any{"choices": choices, "shown_digest": digest}
	for key, v := range extra {
		e[key] = v
	}
	return k.ask("apply", result, e)
}

func (k *decideKit) rows(a map[string]any) map[[2]string]map[string]any {
	out := map[[2]string]map[string]any{}
	for _, r := range jl(a, "changes", "i0001") {
		out[[2]string{js(r, "op"), js(r, "path")}] = jm(r)
	}
	return out
}

func (k *decideKit) rowPaths(a map[string]any) []string {
	var paths []string
	for key := range k.rows(a) {
		paths = append(paths, key[1])
	}
	return paths
}

func (k *decideKit) mark(a map[string]any) map[string]any {
	k.t.Helper()
	row := changeRow(jl(a, "changes", "i0001"), "1.reviewed")
	if row == nil {
		k.t.Fatalf("1.reviewed の行が無い: %v", a["changes"])
	}
	return ticketkit.ParseJSON(k.t, js(row, "content"))
}

func allChoices(value string, keys ...string) map[string]string {
	m := map[string]string{}
	for _, k := range keys {
		m[k] = value
	}
	return m
}

func choiceCode(t testing.TB, a map[string]any, code string) {
	t.Helper()
	if js(a, "code") != code {
		t.Errorf("code = %v, want %s", a["code"], code)
	}
	if !isNull(a, "changes") {
		t.Errorf("changes = %v", a["changes"])
	}
	if !isNull(a, "host_plan") {
		t.Errorf("host_plan = %v", a["host_plan"])
	}
}

var threeThreads = []map[string]any{dthread("t1", "命名"), dthread("t2", "テスト"), dthread("t3", "性能")}

// ---- 1. preview

func TestTheDecidePreviewIsTheSameAsTheTerminalOne(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	threads := []map[string]any{dthread("t1", "命名"), dthread("t2", "テスト")}
	shown := k.preview(threads, nil)
	k.fixture.Update(func(d map[string]any) {
		var ts []any
		for _, th := range threads {
			ts = append(ts, map[string]any{"id": th["id"], "resolved": false, "url": th["url"], "path": th["path"], "line": 3, "body": th["body"]})
		}
		d["threads"] = ts
		d["mr"] = map[string]any{"number": 7, "url": "https://example/mr/7"}
	})
	local := ticketkit.OK(t, k.tr.Ccnavi("--cwd", k.tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--result", k.fixture.Path, "--preview", "--json"))
	if !reflect.DeepEqual(shown, ticketkit.ParseJSON(t, local.Stdout)) {
		t.Errorf("Chrome の preview と手元が違う:\nchrome: %v\nlocal: %s", shown, local.Stdout)
	}
	if !regexp.MustCompile(`^v2\.[A-Za-z0-9_-]+$`).MatchString(js(shown, "digest")) || jf(shown, "version") != decideVersion {
		t.Errorf("digest/version = %v / %v", shown["digest"], shown["version"])
	}
	threadsOut := jl(shown, "threads")
	if len(threadsOut) != 2 || js(threadsOut[0], "key") != "u-t1" || js(threadsOut[1], "key") != "u-t2" {
		t.Errorf("threads = %v", threadsOut)
	}
	if got := sortedKeys(jm(threadsOut[0])); !slices.Equal(got, []string{"body", "key", "last_comment", "line", "path", "url", "version"}) {
		t.Errorf("thread keys = %v", got)
	}
}

func TestTheDecidePreviewWritesNothingAndHasNoHostPlan(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.ask("preview", k.result([]map[string]any{dthread("t1", "命名")}), nil)
	if !isNull(a, "changes") || !isNull(a, "code") {
		t.Errorf("answer = %v", a)
	}
	if _, ok := a["host_plan"]; ok {
		t.Error("host_plan がある")
	}
}

func TestADecidePreviewRefusesAChangesRequestedReview(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.ask("preview", k.result([]map[string]any{dthread("t1", "命名")}, map[string]any{"state": "CHANGES_REQUESTED", "url": "r/1"}), nil)
	if js(a, "code") != "CORE_CONFIRM_REVIEW_OPEN" || !isNull(a, "preview") {
		t.Errorf("answer = %v", a)
	}
}

func TestAMovedHeadAsksForTheCompare(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	need := k.tr.askChrome(k.request("preview", k.result(nil), false, nil))
	if js(need, "need_compare", "base") != js(k.compare, "base") || js(need, "need_compare", "head") != js(k.compare, "head") || !isNull(need, "changes") {
		t.Errorf("need = %v", need)
	}
}

func TestABadDecideRequestIsAnError(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	for _, extra := range []map[string]any{{"mode": "other"}, {"mode": "apply"}, {"mode": "preview", "carry": "x"}} {
		e := map[string]any{"phase": 1, "result": k.result(nil)}
		for key, v := range extra {
			e[key] = v
		}
		a := k.tr.answer(k.tr.chromeRequest("decide", "i0001", nil, e))
		if _, ok := a["error"]; !ok {
			t.Errorf("%v: error が無い: %v", extra, a)
		}
	}
}

// ---- 2〜4. apply

func TestAMixOfKeepIssueAndFix(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.apply(threeThreads, map[string]string{"u-t1": "keep", "u-t2": "issue", "u-t3": "fix"}, nil, nil, nil)
	noProblems(t, a)
	if !isNull(a, "code") {
		t.Errorf("code = %v", a["code"])
	}
	plan := jm(a, "host_plan")
	// issue は issue を選んだ指摘 1 件につき 1 つ。本文の頭に操作ID の印が入る。
	issues := jl(plan, "issues")
	if len(issues) != 1 || js(issues[0], "key") != "u-t2" {
		t.Fatalf("issues = %v", issues)
	}
	issue := issues[0]
	if !strings.HasPrefix(js(issue, "body"), fmt.Sprintf("<!-- ccnavi:issue op=%s -->\n", js(issue, "op"))) {
		t.Errorf("body = %q", js(issue, "body"))
	}
	ticketkit.Has(t, js(issue, "body"), "テスト")
	ticketkit.Has(t, js(issue, "title"), "src/a/x.py:3")
	// 返信と解決は keep と issue だけ。fix の指摘（t3）は入らない。
	replies := jl(plan, "replies")
	var keys, threadIDs []string
	for _, r := range replies {
		keys, threadIDs = append(keys, js(r, "key")), append(threadIDs, js(r, "thread_id"))
	}
	if !slices.Equal(keys, []string{"u-t1", "u-t2"}) || !slices.Equal(threadIDs, []string{"t1", "t2"}) {
		t.Errorf("replies = %v", replies)
	}
	if want := []any{map[string]any{"key": "u-t1", "thread_id": "t1"}, map[string]any{"key": "u-t2", "thread_id": "t2"}}; !reflect.DeepEqual(plan["resolves"], want) {
		t.Errorf("resolves = %v", plan["resolves"])
	}
	ticketkit.Lacks(t, js(replies[0], "body"), "<!-- ccnavi:issue op=")
	ticketkit.Has(t, js(replies[0], "body"), markerAccept)
	ticketkit.Lacks(t, js(replies[0], "body"), issueURLSlot)
	ticketkit.Has(t, js(replies[1], "body"), issueURLSlot)
	ticketkit.Has(t, js(plan, "comment"), issueLinksSlot)
	ticketkit.Has(t, js(plan, "comment"), "u-t3")
	// 続きの子を起こし、マーカーは置かない。レビュー待ちの子は done/ へ。
	rows := k.rows(a)
	if jb(a, "reviewed") || js(a, "followup") != "i0001-01-02" {
		t.Errorf("reviewed/followup = %v / %v", a["reviewed"], a["followup"])
	}
	for _, want := range [][2]string{{"create", ".ccnavi/approved/doing/i0001-01-02.md"}, {"delete", ".ccnavi/approved/phases/i0001/1.requested"}, {"create", ".ccnavi/approved/done/i0001-01-01.md"}} {
		if _, ok := rows[want]; !ok {
			t.Errorf("行 %v が無い", want)
		}
	}
	if _, ok := rows[[2]string{"create", ".ccnavi/approved/phases/i0001/1.reviewed"}]; ok {
		t.Error("マーカーを置いた")
	}
	child := js(rows[[2]string{"create", ".ccnavi/approved/doing/i0001-01-02.md"}], "content")
	ticketkit.Has(t, child, "性能")
	ticketkit.Lacks(t, child, "命名")
	if len(jl(a, "pending_extra")) != 0 {
		t.Errorf("pending_extra = %v", a["pending_extra"])
	}
}

func TestAllKeepPlacesTheReviewedMark(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.apply(threeThreads, allChoices("keep", "u-t1", "u-t2", "u-t3"), nil, nil, nil)
	noProblems(t, a)
	if !jb(a, "reviewed") || js(a, "followup") != "" {
		t.Errorf("answer = %v", a)
	}
	mark := k.mark(a)
	if !slices.Equal(strs(jl(mark, "accepted")), []string{"u-t1", "u-t2", "u-t3"}) || js(mark, "via") != "chrome" {
		t.Errorf("mark = %v", mark)
	}
	plan := jm(a, "host_plan")
	if len(jl(plan, "issues")) != 0 || len(jl(plan, "replies")) != 3 || len(jl(plan, "resolves")) != 3 {
		t.Errorf("plan = %v", plan)
	}
	ticketkit.Lacks(t, js(plan, "comment"), issueLinksSlot)
	ticketkit.Has(t, js(plan, "comment"), markerDecide)
}

func TestAllFixRepliesAndResolvesNothing(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.apply(threeThreads, allChoices("fix", "u-t1", "u-t2", "u-t3"), nil, nil, nil)
	noProblems(t, a)
	plan := jm(a, "host_plan")
	if len(jl(plan, "issues")) != 0 || len(jl(plan, "replies")) != 0 || len(jl(plan, "resolves")) != 0 || plan["comment"] == nil {
		t.Errorf("plan = %v", plan)
	}
	if js(a, "followup") != "i0001-01-02" || jb(a, "reviewed") {
		t.Errorf("answer = %v", a)
	}
	child := js(k.rows(a)[[2]string{"create", ".ccnavi/approved/doing/i0001-01-02.md"}], "content")
	for _, body := range []string{"命名", "テスト", "性能"} {
		ticketkit.Has(t, child, body)
	}
}

func TestNoThreadsPlacesTheReviewedMark(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.apply(nil, map[string]string{}, nil, nil, nil)
	noProblems(t, a)
	if !jb(a, "reviewed") || len(jl(k.mark(a), "accepted")) != 0 {
		t.Errorf("answer = %v", a)
	}
	plan := jm(a, "host_plan")
	if len(jl(plan, "issues")) != 0 || len(jl(plan, "replies")) != 0 || len(jl(plan, "resolves")) != 0 {
		t.Errorf("plan = %v", plan)
	}
	ticketkit.Has(t, js(plan, "comment"), "未解決（Unresolved）の指摘なし")
}

// fix の指摘は、どんな組み合わせでも計画（issue・返信・解決）に入らない。
func TestAFixNeverAppearsInThePlanWhateverTheMix(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	keys := []string{"u-t1", "u-t2", "u-t3"}
	for _, fixed := range keys {
		var others []string
		for _, key := range keys {
			if key != fixed {
				others = append(others, key)
			}
		}
		for _, pair := range [][2]string{{"keep", "keep"}, {"keep", "issue"}, {"issue", "issue"}} {
			choices := map[string]string{fixed: "fix", others[0]: pair[0], others[1]: pair[1]}
			a := k.apply(threeThreads, choices, nil, nil, nil)
			noProblems(t, a)
			for _, part := range []string{"issues", "replies", "resolves"} {
				for _, row := range jl(jm(a, "host_plan"), part) {
					if js(row, "key") == fixed {
						t.Errorf("%v: %s に fix の %s が入った", choices, part, fixed)
					}
				}
			}
		}
	}
}

func TestAThreadWithoutAnIDCanNotBeAccepted(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	threads := []map[string]any{dthread("", "命名")}
	threads[0]["url"] = "u-1"
	a := k.apply(threads, map[string]string{"u-1": "keep"}, nil, nil, nil)
	choiceCode(t, a, "CORE_DECIDE_THREAD_ID")
	if !isNull(a, "host_plan") {
		t.Errorf("host_plan = %v", a["host_plan"])
	}
	// 直すだけなら id は要らない。
	noProblems(t, k.apply(threads, map[string]string{"u-1": "fix"}, nil, nil, nil))
}

func TestAChangesRequestedReviewIsRefusedOnApply(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	threads := []map[string]any{dthread("t1", "命名")}
	digest := js(k.preview(threads, nil), "digest")
	result := k.result(threads, map[string]any{"state": "CHANGES_REQUESTED", "url": "r/1"})
	a := k.ask("apply", result, map[string]any{"choices": map[string]string{"u-t1": "keep"}, "shown_digest": digest})
	choiceCode(t, a, "CORE_CONFIRM_REVIEW_OPEN")
	if !isNull(a, "host_plan") {
		t.Errorf("host_plan = %v", a["host_plan"])
	}
}

func TestChoicesMustCoverTheShownThreads(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	for _, choices := range []map[string]string{{"u-t1": "keep"}, {"u-t1": "keep", "u-t2": "keep", "u-t3": "later"}} {
		a := k.apply(threeThreads, choices, nil, nil, nil)
		choiceCode(t, a, "CORE_DECIDE_CHOICES")
		if !isNull(a, "host_plan") {
			t.Errorf("%v: host_plan = %v", choices, a["host_plan"])
		}
	}
}

func TestAMissingOrOldDigestPlacesNothing(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	for _, digest := range []string{"", strings.Repeat("0", 64), "v1.abc"} {
		a := k.ask("apply", k.result(threeThreads), map[string]any{"choices": allChoices("keep", "u-t1", "u-t2", "u-t3"), "shown_digest": digest})
		choiceCode(t, a, "CORE_DECIDE_NO_DIGEST")
	}
}

// ---- 4. 見せた指摘と今の指摘が違えば、何も積まない。見せたあとに増えた指摘は未対応として残す。

func TestADecideMismatchPlacesNothing(t *testing.T) {
	cases := []struct {
		name    string
		shown   []map[string]any
		now     []map[string]any
		changed string
	}{
		{"an edited thread", []map[string]any{dthread("t1", "命名"), dthread("t2", "テスト")}, []map[string]any{dthread("t1", "命名（書き換えた）"), dthread("t2", "テスト")}, "u-t1"},
		{"a thread resolved by someone", []map[string]any{dthread("t1", "命名"), dthread("t2", "テスト")}, []map[string]any{dthread("t1", "命名"), resolvedThread("t2", "テスト")}, "u-t2"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			k := newDecideKit(t)
			a := k.apply(c.now, allChoices("keep", "u-t1", "u-t2"), c.shown, nil, nil)
			if js(a, "code") != "CORE_DECIDE_MISMATCH" || !slices.Equal(strs(jl(a, "mismatch", "changed")), []string{c.changed}) {
				t.Errorf("answer = %v", a)
			}
			if !isNull(a, "changes") || !isNull(a, "host_plan") {
				t.Errorf("changes/host_plan = %v / %v", a["changes"], a["host_plan"])
			}
			if c.changed == "u-t1" {
				ticketkit.Has(t, js(a, "mismatch", "digest", "expected"), "v2.")
			}
		})
	}
}

func TestADifferentTargetPlacesNothing(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	shown := []map[string]any{dthread("t1", "命名")}
	digest := js(k.preview(shown, nil), "digest")
	result := k.result(shown)
	result["mr"] = map[string]any{"number": 7, "url": "https://example/mr/7-other"}
	a := k.ask("apply", result, map[string]any{"choices": map[string]string{"u-t1": "keep"}, "shown_digest": digest})
	if !slices.Equal(strs(jl(a, "mismatch", "changed")), []string{"target"}) {
		t.Errorf("answer = %v", a)
	}
}

// 見せたあとに増えた指摘は、食い違いに数えず未対応として残す。
func TestAThreadAddedAfterShowingIsLeftPending(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	shown := []map[string]any{dthread("t1", "命名")}
	now := []map[string]any{dthread("t1", "命名"), dthread("t9", "あとから")}
	a := k.apply(now, map[string]string{"u-t1": "keep"}, shown, nil, nil)
	noProblems(t, a)
	if !slices.Equal(strs(jl(a, "pending_extra")), []string{"u-t9"}) || jb(a, "reviewed") {
		t.Errorf("answer = %v", a)
	}
	// 直す指摘が無く追加分があるので、マーカーも置かず、子も動かさない。
	for _, p := range k.rowPaths(a) {
		if strings.HasSuffix(p, "1.reviewed") || strings.Contains(p, "/done/") {
			t.Errorf("置いてはいけない行: %s", p)
		}
	}
	if replies := jl(jm(a, "host_plan"), "replies"); len(replies) != 1 || js(replies[0], "key") != "u-t1" {
		t.Errorf("replies = %v", replies)
	}
	// 直す指摘があれば、追加分があっても子を動かして続きの子を起こす。
	fixed := k.apply(now, map[string]string{"u-t1": "fix"}, shown, nil, nil)
	if js(fixed, "followup") != "i0001-01-02" || !slices.Equal(strs(jl(fixed, "pending_extra")), []string{"u-t9"}) {
		t.Errorf("fixed = %v", fixed)
	}
	raw, _ := json.Marshal(jm(fixed, "host_plan")["replies"])
	ticketkit.Lacks(t, string(raw), "u-t9")
}

// ---- 5. 受け入れ済み（carry）の扱い

var carryShown = threeThreads
var carryChoices = map[string]string{"u-t1": "keep", "u-t2": "issue", "u-t3": "fix"}

func carryResolved(ids ...string) []map[string]any {
	var out []map[string]any
	for _, th := range carryShown {
		t := dthread(th["id"].(string), th["body"].(string))
		t["resolved"] = slices.Contains(ids, th["id"].(string))
		out = append(out, t)
	}
	return out
}

func carryOf(keys ...string) map[string]any {
	l := make([]any, len(keys))
	for i, k := range keys {
		l[i] = k
	}
	return map[string]any{"carry": l}
}

func TestTheHostWriteIsNotAMismatchWithCarryAndIsNotWrittenAgain(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.apply(carryResolved("t1", "t2"), carryChoices, carryShown, nil, carryOf("u-t1", "u-t2"))
	noProblems(t, a)
	plan := jm(a, "host_plan")
	if len(jl(plan, "issues")) != 0 || len(jl(plan, "replies")) != 0 || len(jl(plan, "resolves")) != 0 || js(a, "followup") != "i0001-01-02" {
		t.Errorf("answer = %v", a)
	}
	ticketkit.Has(t, js(plan, "comment"), "u-t1")
	// 今回作る issue が無いので、作った issue の一覧の枠は MR に出さない。
	ticketkit.Lacks(t, js(plan, "comment"), issueLinksSlot)
	// 受け入れ済みの 2 件は、見せた指摘の一部として数える。
	if len(jl(a, "pending_extra")) != 0 {
		t.Errorf("pending_extra = %v", a["pending_extra"])
	}
}

func TestTheSameResultWithoutCarryIsAMismatch(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.apply(carryResolved("t1", "t2"), carryChoices, carryShown, nil, nil)
	if js(a, "code") != "CORE_DECIDE_MISMATCH" || !slices.Equal(strs(jl(a, "mismatch", "changed")), []string{"u-t1", "u-t2"}) {
		t.Errorf("answer = %v", a)
	}
}

func TestAllAcceptedAndCarriedPlacesTheMarkWithTheKeys(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	now := []map[string]any{resolvedThread("t1", "命名"), resolvedThread("t2", "テスト")}
	a := k.apply(now, map[string]string{"u-t1": "keep", "u-t2": "issue"}, carryShown[:2], nil, carryOf("u-t1", "u-t2"))
	noProblems(t, a)
	if !jb(a, "reviewed") || !slices.Equal(strs(jl(k.mark(a), "accepted")), []string{"u-t1", "u-t2"}) {
		t.Errorf("answer = %v", a)
	}
}

// carry の約束を破る形（消えた・ホストで解決していない・fix・本文が変わった）。
func TestCarryRefusesWhatTheHostDoesNotBackUp(t *testing.T) {
	cases := []struct {
		name    string
		now     []map[string]any
		shown   []map[string]any
		choices map[string]string
		carry   []string
		code    string
		changed []string
	}{
		{"a carried thread that vanished", []map[string]any{resolvedThread("t2", "テスト")}, carryShown[:2], map[string]string{"u-t1": "keep", "u-t2": "keep"}, []string{"u-t1", "u-t2"}, "CORE_DECIDE_CHOICES", nil},
		{"a carried thread not resolved on the host", carryShown[:1], carryShown[:1], map[string]string{"u-t1": "keep"}, []string{"u-t1"}, "CORE_DECIDE_CHOICES", nil},
		{"a fix can not be carried", []map[string]any{resolvedThread("t1", "命名")}, []map[string]any{dthread("t1", "命名")}, map[string]string{"u-t1": "fix"}, []string{"u-t1"}, "CORE_DECIDE_CHOICES", nil},
		{"an edited carried thread", []map[string]any{resolvedThread("t1", "命名（書き換えた）")}, []map[string]any{dthread("t1", "命名")}, map[string]string{"u-t1": "keep"}, []string{"u-t1"}, "CORE_DECIDE_MISMATCH", []string{"u-t1"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			k := newDecideKit(t)
			a := k.apply(c.now, c.choices, c.shown, nil, carryOf(c.carry...))
			if js(a, "code") != c.code {
				t.Errorf("code = %v, want %s", a["code"], c.code)
			}
			if c.changed != nil && !slices.Equal(strs(jl(a, "mismatch", "changed")), c.changed) {
				t.Errorf("changed = %v", a["mismatch"])
			}
			if c.code == "CORE_DECIDE_CHOICES" {
				choiceCode(t, a, c.code)
			}
		})
	}
}

// ---- 6. issue の操作ID の印

func (k *decideKit) issueOf(threads []map[string]any, key string) map[string]any {
	k.t.Helper()
	choices := map[string]string{}
	for _, th := range threads {
		choices[th["url"].(string)] = "issue"
	}
	a := k.apply(threads, choices, nil, nil, nil)
	noProblems(k.t, a)
	for _, i := range jl(jm(a, "host_plan"), "issues") {
		if js(i, "key") == key {
			return jm(i)
		}
	}
	k.t.Fatalf("issue %s が無い", key)
	return nil
}

func TestTheOpIsTheSameForTheSameThread(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	threads := []map[string]any{dthread("t1", "命名"), dthread("t2", "テスト")}
	first, again := k.issueOf(threads, "u-t2"), k.issueOf(threads, "u-t2")
	if !reflect.DeepEqual(first, again) {
		t.Errorf("同じ指摘で issue が違う:\n%v\n%v", first, again)
	}
	if !regexp.MustCompile(`^[0-9a-f]{32}$`).MatchString(js(first, "op")) {
		t.Errorf("op = %v", first["op"])
	}
	ticketkit.Has(t, js(first, "body"), fmt.Sprintf("<!-- ccnavi:issue op=%s -->", js(first, "op")))
	// 指摘ごとに違う。
	if other := k.issueOf(threads, "u-t1"); js(first, "op") == js(other, "op") {
		t.Error("別の指摘で op が同じ")
	}
}

func TestTheOpChangesWithTheThreadVersion(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	before := k.issueOf([]map[string]any{dthread("t1", "命名")}, "u-t1")
	after := k.issueOf([]map[string]any{dthread("t1", "命名を直して")}, "u-t1")
	if js(before, "op") == js(after, "op") {
		t.Error("指摘が変わっても op が同じ")
	}
}

func TestALongBodyGoesToFollowUpComments(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	issue := k.issueOf([]map[string]any{dthread("t1", strings.Repeat("あ", issueBodyLimit+10))}, "u-t1")
	if utf8.RuneCountInString(js(issue, "body")) > issueBodyLimit || len(jl(issue, "comments")) == 0 || !strings.HasPrefix(js(issue, "body"), "<!-- ccnavi:issue op=") {
		t.Errorf("body の長さ = %d, comments = %d", utf8.RuneCountInString(js(issue, "body")), len(jl(issue, "comments")))
	}
}

// 拡張は 1 回に 60,000 字まで書く。続きのコメントには印の行（約 80 字）を足す。
func TestEveryPieceLeavesRoomForTheExtensionToAddItsMarker(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	const extensionCap, markerRoom = 60000, 200
	for _, size := range []int{issueBodyLimit - 5, issueBodyLimit + 5, 150000} {
		issue := k.issueOf([]map[string]any{dthread("t1", strings.Repeat("あ", size))}, "u-t1")
		pieces := append([]any{issue["body"]}, jl(issue, "comments")...)
		for _, p := range pieces {
			if n := utf8.RuneCountInString(p.(string)); n+markerRoom > extensionCap {
				t.Errorf("size %d: 断片が %d 字", size, n)
			}
		}
	}
}

// ---- 7. 続きの子の識別子

func TestTheNextIDSkipsTheExistingChildrenOfThePhase(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	existing := strings.Replace(childNoReview("i0001-01-03", 1, "wip/design/*"), `completed_at: ""`, `completed_at: "2026-01-01T00:00:00+0000"`, 1)
	ticketkit.Write(t, k.tr.ApprovedPath("done", "i0001-01-03"), existing)
	a := k.apply([]map[string]any{dthread("t1", "命名")}, map[string]string{"u-t1": "fix"}, nil, nil, nil)
	noProblems(t, a)
	if js(a, "followup") != "i0001-01-04" {
		t.Errorf("followup = %v", a["followup"])
	}
	paths := k.rowPaths(a)
	if !slices.Contains(paths, ".ccnavi/approved/doing/i0001-01-04.md") || slices.Contains(paths, ".ccnavi/approved/doing/i0001-01-03.md") {
		t.Errorf("paths = %v", paths)
	}
}

func TestTheSameChoiceGivesTheSameOpInTheChild(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	ops := map[string]bool{}
	re := regexp.MustCompile(`ccnavi_op: "?([0-9a-f]{32})`)
	for range 2 {
		a := k.apply([]map[string]any{dthread("t1", "命名")}, map[string]string{"u-t1": "fix"}, nil, nil, nil)
		child := js(k.rows(a)[[2]string{"create", ".ccnavi/approved/doing/" + js(a, "followup") + ".md"}], "content")
		m := re.FindStringSubmatch(child)
		if m == nil {
			t.Fatalf("ccnavi_op が無い: %s", child)
		}
		ops[m[1]] = true
	}
	if len(ops) != 1 {
		t.Errorf("op = %v", ops)
	}
}

// ---- 8. 手元の decide が書いたものと、同じ書くものになる。

func (k *decideKit) put(threads []map[string]any) {
	k.fixture.Update(func(d map[string]any) {
		var ts []any
		for _, th := range threads {
			ts = append(ts, map[string]any{"id": th["id"], "resolved": false, "url": th["url"], "path": th["path"], "line": 3, "body": th["body"]})
		}
		d["threads"] = ts
		d["mr"] = map[string]any{"number": 7, "url": "https://example/mr/7"}
	})
}

// local は手元の decide を回して、親のツリーに書かれた変更を返す。
func (k *decideKit) local(threads []map[string]any, choices map[string]string, shown []map[string]any) []diffRow {
	k.t.Helper()
	if shown == nil {
		shown = threads
	}
	k.put(shown)
	base := []string{"--cwd", k.tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--result", k.fixture.Path}
	digest := js(ticketkit.ParseJSON(k.t, ticketkit.OK(k.t, k.tr.Ccnavi(append(append([]string{}, base...), "--preview", "--json")...)).Stdout), "digest")
	k.put(threads)
	before := k.tr.disk()
	raw, _ := json.Marshal(choices)
	ticketkit.OK(k.t, k.tr.Ccnavi(append(append([]string{}, base...), "--yes", string(raw), "--digest", digest, "--json", "--actor", "alice")...))
	return diffDisk(before, k.tr.disk(), "i0001")
}

var (
	settledRe = regexp.MustCompile(`(?m)^settled_by: "[a-z-]+"\n`)
	opLineRe  = regexp.MustCompile(`(?m)^ccnavi_op: .*\n`)
	opTextRe  = regexp.MustCompile(` / 操作: ` + "`" + `[0-9a-f]+` + "`")
)

// normalized は経路・時刻・操作ID の違いを均して、(op, path) → 中身の表にする。
func normalized(t testing.TB, rows []diffRow) map[[2]string]any {
	t.Helper()
	out := map[[2]string]any{}
	for _, r := range rows {
		content := strings.ReplaceAll(r.Content, "\r\n", "\n")
		content = settledRe.ReplaceAllString(content, "")
		content = opLineRe.ReplaceAllString(content, "")
		content = opTextRe.ReplaceAllString(content, "")
		var value any = content
		if strings.HasSuffix(r.Path, ".reviewed") {
			mark := ticketkit.ParseJSON(t, content)
			delete(mark, "via")
			delete(mark, "at")
			value = mark
		}
		out[[2]string{r.Op, r.Path}] = value
	}
	return out
}

func chromeRows(t testing.TB, a map[string]any) []diffRow {
	var rows []diffRow
	for _, r := range jl(a, "changes", "i0001") {
		rows = append(rows, diffRow{js(r, "op"), js(r, "path"), js(r, "content")})
	}
	return rows
}

// same は Chrome の書くものと手元の decide の書くものが同じことを見る。
func (k *decideKit) same(threads []map[string]any, choices map[string]string, shown []map[string]any) map[string]any {
	k.t.Helper()
	chrome := k.apply(threads, choices, shown, nil, nil)
	noProblems(k.t, chrome)
	local := k.local(threads, choices, shown)
	if got, want := normalized(k.t, chromeRows(k.t, chrome)), normalized(k.t, local); !reflect.DeepEqual(got, want) {
		k.t.Errorf("Chrome と手元で書くものが違う:\nchrome: %v\nlocal: %v", got, want)
	}
	return chrome
}

func TestChromeWritesWhatTheTerminalDecideWrites(t *testing.T) {
	two := []map[string]any{dthread("t1", "命名"), dthread("t2", "テスト")}
	cases := []struct {
		name    string
		threads []map[string]any
		choices map[string]string
	}{
		{"keep and issue", two, map[string]string{"u-t1": "keep", "u-t2": "issue"}},
		{"fix", two, map[string]string{"u-t1": "keep", "u-t2": "fix"}},
		{"all keep", two, map[string]string{"u-t1": "keep", "u-t2": "keep"}},
		{"all fix", two, map[string]string{"u-t1": "fix", "u-t2": "fix"}},
		{"no threads", nil, map[string]string{}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			newDecideKit(t).same(c.threads, c.choices, nil)
		})
	}
	t.Run("a thread added after showing", func(t *testing.T) {
		t.Parallel()
		k := newDecideKit(t)
		shown := []map[string]any{dthread("t1", "命名")}
		a := k.same([]map[string]any{dthread("t1", "命名"), dthread("t9", "あとから")}, map[string]string{"u-t1": "keep"}, shown)
		if !slices.Equal(strs(jl(a, "pending_extra")), []string{"u-t9"}) {
			t.Errorf("pending_extra = %v", a["pending_extra"])
		}
		// 追加分だけが残るなら、書くものは無い。
		b := newDecideKit(t).same([]map[string]any{dthread("t9", "あとから")}, map[string]string{}, []map[string]any{})
		if c, ok := b["changes"].(map[string]any); !ok || len(c) != 0 {
			t.Errorf("changes = %v", b["changes"])
		}
	})
}

// ---- 境界

func TestNothingDecidedAndAThreadAddedSaysSoInTheComment(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	a := k.apply([]map[string]any{dthread("t9", "あとから")}, map[string]string{}, []map[string]any{}, nil, nil)
	noProblems(t, a)
	comment := js(jm(a, "host_plan"), "comment")
	ticketkit.Has(t, comment, "今回の判断なし。追加分 1 件は判断待ち")
	ticketkit.Lacks(t, comment, "レビュー済みにした")
	if jb(a, "reviewed") {
		t.Error("reviewed")
	}
	if c, ok := a["changes"].(map[string]any); !ok || len(c) != 0 {
		t.Errorf("changes = %v", a["changes"])
	}
}

func TestMoreThanTheLimitOfIssuesPlacesNothing(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	var many []map[string]any
	choices := map[string]string{}
	for i := 0; i <= maxIssues; i++ {
		th := dthread(fmt.Sprintf("t%d", i), fmt.Sprintf("指摘%d", i))
		many = append(many, th)
		choices[th["url"].(string)] = "issue"
	}
	a := k.apply(many, choices, nil, nil, nil)
	choiceCode(t, a, "CORE_DECIDE_CHOICES")
	ticketkit.Has(t, strs(jl(a, "problems"))[0], fmt.Sprint(maxIssues+1))
	fewer := map[string]string{}
	for _, th := range many[:maxIssues] {
		fewer[th["url"].(string)] = "issue"
	}
	ok := k.apply(many[:maxIssues], fewer, nil, nil, nil)
	noProblems(t, ok)
	if n := len(jl(jm(ok, "host_plan"), "issues")); n != maxIssues {
		t.Errorf("issues = %d", n)
	}
}

// 答えのキーは、通った経路に依らない（need_compare のときも host_plan・preview の欄がある）。
func TestTheKeysOfTheAnswersDoNotDependOnThePath(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	req := k.request("apply", k.result(nil), false, map[string]any{"choices": map[string]string{}, "shown_digest": "x"})
	need := k.tr.askChrome(req)
	if _, ok := need["need_compare"]; !ok || !isNull(need, "host_plan") {
		t.Errorf("need = %v", need)
	}
	if _, ok := need["preview"]; ok {
		t.Error("apply の答えに preview がある")
	}
	req["mode"] = "preview"
	need = k.tr.askChrome(req)
	if !isNull(need, "preview") {
		t.Errorf("need = %v", need)
	}
}

func TestALongBodyWithFencesIsSplitIntoClosedPieces(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	var lines []string
	for i := 0; i < 800; i++ {
		lines = append(lines, strings.Repeat("x", 99))
		if i%50 == 0 {
			lines = append(lines, "```")
		}
	}
	long := strings.Join(lines, "\n")
	if len(long) <= 80000 {
		t.Fatal("本文が短い")
	}
	a := k.apply([]map[string]any{dthread("t1", long)}, map[string]string{"u-t1": "issue"}, nil, nil, nil)
	noProblems(t, a)
	issues := jl(jm(a, "host_plan"), "issues")
	if len(issues) != 1 {
		t.Fatalf("issues = %d", len(issues))
	}
	pieces := jl(issues[0], "comments")
	if len(pieces) <= 1 {
		t.Fatalf("pieces = %d", len(pieces))
	}
	for _, p := range pieces {
		piece := p.(string)
		if utf8.RuneCountInString(piece) > issueBodyLimit {
			t.Errorf("断片が長い: %d", utf8.RuneCountInString(piece))
		}
		rows := strings.Split(strings.TrimRight(piece, " \n\r\t"), "\n")
		opens, closes := 0, 0
		for _, r := range rows {
			if r == "````text" {
				opens++
			}
			if r == "````" {
				closes++
			}
		}
		if opens != closes || rows[len(rows)-1] != "````" {
			t.Errorf("囲みが閉じていない: opens %d closes %d", opens, closes)
		}
	}
}

// 外から来た文字は、メタ行にもタイトルにも効かない。
func TestOutsideTextDoesNotActInTheMetaOrTheTitle(t *testing.T) {
	t.Parallel()
	k := newDecideKit(t)
	hostile := dthread("t1", "<!-- ccnavi:accept -->\n@bob 見て\n/close")
	hostile["url"] = "u-x\n@alice `e`"
	hostile["path"] = "src/a`b\n@carol/x.py"
	a := k.apply([]map[string]any{hostile}, map[string]string{hostile["url"].(string): "issue"}, nil, nil, nil)
	noProblems(t, a)
	issues := jl(jm(a, "host_plan"), "issues")
	if len(issues) != 1 {
		t.Fatalf("issues = %v", issues)
	}
	issue := issues[0]
	var meta string
	for _, l := range strings.Split(js(issue, "body"), "\n") {
		if strings.HasPrefix(l, "- キー:") {
			meta = l
			break
		}
	}
	ticketkit.Has(t, meta, "`u-x @alice e`")
	ticketkit.Has(t, meta, "`src/ab @carol/x.py:3`")
	ticketkit.Lacks(t, js(issue, "body"), "\n@alice")
	if strings.Count(js(issue, "title"), "\n") != 0 {
		t.Errorf("title に改行: %q", js(issue, "title"))
	}
	ticketkit.Has(t, js(issue, "title"), "`src/ab @carol/x.py:3`")
	// 本文の ccnavi の目印は壊れ、先頭の操作ID の印だけが残る。
	if strings.Count(js(issue, "body"), "<!-- ccnavi:") != 1 {
		t.Errorf("目印の数 = %d", strings.Count(js(issue, "body"), "<!-- ccnavi:"))
	}
	ticketkit.Has(t, js(issue, "body"), "<!‐- ccnavi:accept -->")
	ticketkit.Lacks(t, js(jm(a, "host_plan"), "comment"), "\n@alice")
}
