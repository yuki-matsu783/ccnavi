package host

import (
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/gitlabapi"
)

// snapState は MR が 3 件（下書き・開いた・マージ済み）と issue が 2 件ある、模擬の初期状態。
func snapState() map[string]any {
	state := seeded()
	first := state["mrs"].([]any)[0].(map[string]any)
	first["title"] = "Draft: i0001"
	clone := func(over map[string]any) map[string]any {
		out := map[string]any{}
		for k, v := range first {
			out[k] = v
		}
		for k, v := range over {
			out[k] = v
		}
		return out
	}
	second := clone(map[string]any{"iid": 2, "title": "i0002", "source_branch": "i0002", "discussions": []any{}})
	third := clone(map[string]any{"iid": 3, "title": "i0003", "source_branch": "i0002", "discussions": []any{}, "state": "merged", "merged_at": "2026-10-26"})
	state["mrs"] = []any{first, second, third}
	state["issues"] = []any{
		map[string]any{"iid": 1, "title": "開いた issue", "state": "opened", "description": "本文"},
		map[string]any{"iid": 2, "title": "閉じた issue", "state": "closed", "description": ""},
	}
	return state
}

type snapEnv struct {
	*bareHost
	out string
}

func newSnapEnv(t *testing.T, state map[string]any) snapEnv {
	t.Helper()
	return snapEnv{prepareHost(t, FakeGitLab, state), base.TempDir(t)}
}

// take は step の記録を取り、(要約, 読み直した記録, 本文) を返す。
func (e snapEnv) take(step int) (SnapResult, map[string]any, string) {
	e.t.Helper()
	res, err := e.m.Take(e.w.root, e.h, step, e.out)
	if err != nil {
		e.t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(e.out, SnapFile(step)))
	if err != nil {
		e.t.Fatal(err)
	}
	return res, decode(e.t, string(data)), string(data)
}

func at(v any, path ...any) any {
	for _, p := range path {
		switch k := p.(type) {
		case string:
			v, _ = v.(map[string]any)[k]
		case int:
			list, _ := v.([]any)
			if k >= len(list) {
				return nil
			}
			v = list[k]
		}
	}
	return v
}

func mrByIID(rec map[string]any) map[int]map[string]any {
	out := map[int]map[string]any{}
	for _, mr := range rec["mrs"].([]any) {
		m := mr.(map[string]any)
		n, _ := strconv.Atoi(m["iid"].(json.Number).String())
		out[n] = m
	}
	return out
}

func decode(t *testing.T, text string) map[string]any {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(text))
	dec.UseNumber()
	var rec map[string]any
	if err := dec.Decode(&rec); err != nil {
		t.Fatal(err)
	}
	return rec
}

func TestSnapshotRecordsMRsThreadsAndIssues(t *testing.T) {
	t.Parallel()
	e := newSnapEnv(t, snapState())
	res, _, text := e.take(1)
	rec := decode(t, text)
	if res != (SnapResult{Step: 1, File: "host-1.json", OK: true, Errors: 0}) {
		t.Errorf("result = %+v", res)
	}
	if rec["host"] != "fake-gitlab" || rec["project"] != fakeProject || len(rec["errors"].([]any)) != 0 {
		t.Errorf("record = %v", rec)
	}
	mrs := mrByIID(rec)
	if len(mrs) != 3 || mrs[1]["draft"] != true || mrs[2]["draft"] != false || mrs[3]["state"] != "merged" || mrs[3]["merged_at"] != "2026-10-26" {
		t.Errorf("mrs = %v", mrs)
	}
	thread := at(mrs[1], "threads", 0).(map[string]any)
	note := at(thread, "notes", 0).(map[string]any)
	if thread["id"] != "d101" || thread["resolved"] != false || note["author"] != "reviewer" ||
		note["body"] != "ここを直してください" || note["path"] != "a.txt" {
		t.Errorf("thread = %v", thread)
	}
	if len(mrs[2]["threads"].([]any)) != 0 {
		t.Errorf("MR 2 のスレッド = %v", mrs[2]["threads"])
	}
	want := map[string]float64{"mrs": 3, "mrs_opened": 2, "mrs_merged": 1, "mrs_draft": 1, "mrs_approved": 0,
		"threads": 1, "threads_unresolved": 1, "threads_system": 0, "issues": 2, "issues_opened": 1}
	for k, v := range want {
		if got, _ := strconv.ParseFloat(rec["summary"].(map[string]any)[k].(json.Number).String(), 64); got != v {
			t.Errorf("summary.%s = %v, want %v", k, got, v)
		}
	}
}

func TestSnapshotApprovalsArePerMRAndFollowTheOtherSide(t *testing.T) {
	t.Parallel()
	e := newSnapEnv(t, snapState())
	_, before, _ := e.take(1)
	for iid, mr := range mrByIID(before) {
		a := mr["approvals"].(map[string]any)
		if a["taken"] != true || a["approved"] != false || len(a["approved_by"].([]any)) != 0 {
			t.Errorf("MR %d: approvals = %v", iid, a)
		}
	}
	if _, top := before["approvals"]; top {
		t.Error("承認は最上位に置かない")
	}
	access, err := e.m.APIAccess(e.h)
	if err != nil {
		t.Fatal(err)
	}
	if status, _ := access.Client.Approve(access.Token, access.Project, 2); status != 201 {
		t.Fatalf("approve = %d", status)
	}
	_, after, _ := e.take(2)
	a := mrByIID(after)[2]["approvals"].(map[string]any)
	by := a["approved_by"].([]any)[0].(map[string]any)
	if a["approved"] != true || by["username"] != "reviewer" || len(by) != 2 || len(a) != 3 {
		t.Errorf("approvals = %v", a)
	}
	if mrByIID(after)[1]["approvals"].(map[string]any)["approved"] != false {
		t.Error("承認していない MR が承認済みになった")
	}
	if got := after["summary"].(map[string]any)["mrs_approved"].(json.Number).String(); got != "1" {
		t.Errorf("mrs_approved = %s", got)
	}
}

func TestSnapshotFollowsTheStateOfTheOtherSide(t *testing.T) {
	t.Parallel()
	e := newSnapEnv(t, snapState())
	_, before, _ := e.take(1)
	access, _ := e.m.APIAccess(e.h)
	if status, _ := access.Client.ResolveDiscussion(access.Token, access.Project, 1, "d101", true); status != 200 {
		t.Fatalf("resolve = %d", status)
	}
	client := access.Client.(*gitlabapi.Client)
	if status, _ := client.MergeMR(access.Token, access.Project, 1, 1); status != 200 {
		t.Fatalf("merge = %d", status)
	}
	_, after, _ := e.take(2)
	b, a := mrByIID(before)[1], mrByIID(after)[1]
	if at(b, "threads", 0, "resolved") != false || at(a, "threads", 0, "resolved") != true || b["state"] != "opened" || a["state"] != "merged" {
		t.Errorf("before = %v\nafter = %v", b, a)
	}
	if got := after["summary"].(map[string]any)["threads_unresolved"].(json.Number).String(); got != "0" {
		t.Errorf("threads_unresolved = %s", got)
	}
}

func TestSnapshotOnlyReads(t *testing.T) {
	t.Parallel()
	e := newSnapEnv(t, snapState())
	e.take(1)
	requests := e.m.Server(e.h).Requests()
	if len(requests) == 0 {
		t.Fatal("要求が無い")
	}
	sawApprovals := false
	for _, r := range requests {
		if !strings.HasPrefix(r, "GET ") {
			t.Errorf("読むだけのはずが %q", r)
		}
		sawApprovals = sawApprovals || strings.Contains(r, "/approvals")
	}
	if !sawApprovals {
		t.Error("承認を読んでいない")
	}
}

func TestSnapshotMasksTheTokenEvenWhenTheOtherSideReturnsIt(t *testing.T) {
	t.Parallel()
	state := snapState()
	mr := state["mrs"].([]any)[0].(map[string]any)
	mr["discussions"].([]any)[0].(map[string]any)["notes"].([]any)[0].(map[string]any)["body"] = "鍵は " + "fake-gitlab-token-0001"
	mr["description"] = "Bearer fake-gitlab-token-0001"
	e := newSnapEnv(t, state)
	_, rec, text := e.take(1)
	if strings.Contains(text, e.h.Token) {
		t.Error("トークンが記録に残っている")
	}
	m := mrByIID(rec)[1]
	if at(m, "threads", 0, "notes", 0, "body") != "鍵は ***" || m["description"] != "Bearer ***" {
		t.Errorf("mr = %v", m)
	}
}

// 相手に接続できなくても記録は書き、残りの API は読まない。
func TestSnapshotRecordsAConnectionFailure(t *testing.T) {
	t.Parallel()
	e := newSnapEnv(t, snapState())
	e.m.Hold(e.w.root, e.h)
	res, rec, _ := e.take(1)
	calls := rec["calls"].([]any)
	if res.OK || res.Errors == 0 || len(rec["mrs"].([]any)) != 0 || len(calls) != 1 || at(calls[0], "api") != "list_mrs" {
		t.Errorf("result = %+v, record = %v", res, rec)
	}
}

// 本物の GitLab は承認などのシステムのメモもスレッドとして返す。残すが、スレッドの件数とは分けて数える。
func TestSnapshotCountsSystemNotesApart(t *testing.T) {
	t.Parallel()
	rd := &fakeReader{
		mrs:       []any{map[string]any{"iid": "1", "title": "t", "state": "opened"}},
		mr:        map[string]any{"iid": "1", "title": "t", "state": "opened"},
		approvals: map[string]any{"approved": false, "approved_by": []any{}},
		discussions: []any{
			map[string]any{"id": "d1", "individual_note": false, "notes": []any{map[string]any{"id": "1", "body": "直して", "resolvable": true, "resolved": false}}},
			map[string]any{"id": "s1", "individual_note": true, "notes": []any{map[string]any{"id": "9", "system": true, "body": "approved this merge request"}}},
		},
	}
	rec := Collect(rd, "t", "p")
	threads := rec.MRs[0]["threads"].([]map[string]any)
	if threads[0]["system"] != false || threads[1]["system"] != true ||
		rec.Summary.Threads != 1 || rec.Summary.ThreadsUnresolved != 1 || rec.Summary.ThreadsSystem != 1 {
		t.Errorf("threads = %v, summary = %+v", threads, rec.Summary)
	}
}

// 1 件が取れなくても一覧の分は残し、API ごとの失敗を errors に残す。承認が読めなければ taken は false。
func TestSnapshotRecordsEachFailedCall(t *testing.T) {
	t.Parallel()
	rd := &fakeReader{
		mrs:         []any{map[string]any{"iid": "1", "title": "t", "state": "opened"}},
		statuses:    map[string]int{"get_mr": 404, "list_discussions": 500, "list_approvals": 500},
		discussions: []any{}, approvals: map[string]any{},
	}
	rec := Collect(rd, "t", "p")
	mr := rec.MRs[0]
	apis := map[string]bool{}
	for _, e := range rec.Errors {
		apis[e.API] = true
	}
	if mr["title"] != "t" || mr["threads_unavailable"] != true || !apis["get_mr 1"] || !apis["list_discussions 1"] || !apis["list_approvals 1"] || len(rec.Errors) != 3 {
		t.Errorf("mr = %v, errors = %+v", mr, rec.Errors)
	}
	if a := mr["approvals"].(map[string]any); a["taken"] != false || len(a) != 1 {
		t.Errorf("approvals = %v", a)
	}
	if rec.Summary.MRsApproved != 0 {
		t.Error("取れなかった承認を承認済みに数えた")
	}
}

// 一覧が 1 ページ（100 件）ちょうど返ったら、切れた可能性を errors に残す。
func TestSnapshotMarksAFullPageAsPossiblyCut(t *testing.T) {
	t.Parallel()
	issues := make([]any, 100)
	for i := range issues {
		issues[i] = map[string]any{"iid": i, "title": "x", "state": "opened"}
	}
	rec := Collect(&fakeReader{issues: issues}, "t", 1)
	cut := false
	for _, e := range rec.Errors {
		cut = cut || e.API == "list_issues"
	}
	if len(rec.Issues) != 100 || !cut {
		t.Errorf("issues = %d, errors = %+v", len(rec.Issues), rec.Errors)
	}
}

func TestSnapshotNeedsAFakeOrDockerHost(t *testing.T) {
	t.Parallel()
	m, _ := manager(t)
	if _, err := m.Take("", &Host{Kind: Bare}, 1, base.TempDir(t)); err == nil {
		t.Error("bare の相手側は読めない")
	}
}

// 止めてある間に状態ファイルを直接変えても、再開は同じ状態から続く。
func TestHoldStopsTheServerAndResumeKeepsTheState(t *testing.T) {
	// ポートが空いた隙に別のテストの模擬が取らないよう、並行にしない（並行のテストは、直列のテストが終わるまで始まらない）。
	b := prepareHost(t, FakeGitLab, seeded())
	port := b.h.Port
	b.m.Hold(b.w.root, b.h)
	if _, err := os.Stat(b.h.StatePath); err != nil {
		t.Fatal("状態ファイルが残っていない")
	}
	if c, err := net.Dial("tcp", "127.0.0.1:"+strconv.Itoa(port)); err == nil {
		c.Close()
		t.Fatal("止めたのに接続できる")
	}
	data, _ := os.ReadFile(b.h.StatePath)
	state := decode(t, string(data))
	state["seq"] = 777
	out, _ := json.Marshal(state)
	if err := os.WriteFile(b.h.StatePath, out, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := b.m.Resume(b.w.root, b.h); err != nil || b.h.Port != port {
		t.Fatalf("Resume = %v, port = %d, want %d", err, b.h.Port, port)
	}
	access, _ := b.m.APIAccess(b.h)
	if status, body := access.Client.ListMRs(access.Token, access.Project, "all"); status != 200 || len(gitlabapi.AsList(body)) != 1 {
		t.Errorf("再開後の MR: status = %d, body = %v", status, body)
	}
	if got := decode(t, readFile(t, b.h.StatePath))["seq"].(json.Number).String(); got != "777" {
		t.Errorf("seq = %s（初期状態で上書きした）", got)
	}
}

// 止めた模擬のポートが取られていたら、別のポートで続けず、空けて打ち直すよう言って止まる。
func TestResumeStopsWhenThePortIsTaken(t *testing.T) {
	// ポートが空いた隙に別のテストの模擬が取らないよう、並行にしない（並行のテストは、直列のテストが終わるまで始まらない）。
	b := prepareHost(t, FakeGitLab, nil)
	b.m.Hold(b.w.root, b.h)
	taken, err := net.Listen("tcp", "127.0.0.1:"+strconv.Itoa(b.h.Port))
	if err != nil {
		t.Skip("同じポートを取り直せなかった")
	}
	defer taken.Close()
	err = b.m.Resume(b.w.root, b.h)
	if err == nil || !strings.Contains(err.Error(), "next を打ち直す") {
		t.Errorf("Resume = %v", err)
	}
}

func TestTeardownStopsTheServer(t *testing.T) {
	// ポートが空いた隙に別のテストの模擬が取らないよう、並行にしない（並行のテストは、直列のテストが終わるまで始まらない）。
	m, _ := manager(t)
	w := newWorkspace(t)
	h, err := m.Prepare(w.root, w.target, Setup{Kind: FakeGitLab}, "")
	if err != nil {
		t.Fatal(err)
	}
	m.Teardown(w.root, h)
	if c, err := net.Dial("tcp", "127.0.0.1:"+strconv.Itoa(h.Port)); err == nil {
		c.Close()
		t.Error("後片付けのあとも接続できる")
	}
	if _, err := os.Stat(h.Dir); !os.IsNotExist(err) {
		t.Error("置き場が残っている")
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// fakeReader は step の終わりの読み取り（Reader）の偽の相手。statuses で API ごとの状態を差し替える。
type fakeReader struct {
	mrs, discussions, issues []any
	mr, approvals            map[string]any
	statuses                 map[string]int
}

func (f *fakeReader) status(name string) int {
	if s, ok := f.statuses[name]; ok {
		return s
	}
	return 200
}

func (f *fakeReader) ListMRs(string, any, string) (int, any) { return f.status("list_mrs"), f.mrs }
func (f *fakeReader) GetMR(string, any, any) (int, any)      { return f.status("get_mr"), f.mr }
func (f *fakeReader) ListDiscussions(string, any, any) (int, any) {
	return f.status("list_discussions"), f.discussions
}
func (f *fakeReader) ListApprovals(string, any, any) (int, any) {
	return f.status("list_approvals"), f.approvals
}
func (f *fakeReader) ListIssues(string, any, string, int) (int, any) {
	return f.status("list_issues"), f.issues
}
