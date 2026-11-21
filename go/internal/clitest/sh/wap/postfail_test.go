package wap

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/env"
)

// 投稿 API の失敗と不正な応答、操作記録を失った後の打ち直し。GitLab の代わりの POST の答えをテストごとに変えて失敗させる。

// opsRecords は操作記録（logs/state/ops/*.json）を、名前の順に読む。
func (h *hostKit) opsRecords() []map[string]any {
	h.t.Helper()
	paths, _ := filepath.Glob(filepath.Join(h.state, "ops", "*.json"))
	var found []map[string]any
	for _, path := range paths {
		var record map[string]any
		if err := json.Unmarshal([]byte(shkit.Read(h.t, path)), &record); err != nil {
			h.t.Fatal(err)
		}
		found = append(found, record)
	}
	return found
}

// stageStates は kind の操作記録の段階の状態。
func (h *hostKit) stageStates(kind string) []string {
	var states []string
	for _, record := range h.opsRecords() {
		if record["kind"] != kind {
			continue
		}
		stages, _ := record["stages"].([]any)
		for _, s := range stages {
			states = append(states, s.(map[string]any)["state"].(string))
		}
	}
	return states
}

func (h *hostKit) commentArgs() []string {
	return []string{"comment", "--body-file", shkit.Write(h.t, filepath.Join(h.base, "comment.md"), "コメント\n")}
}

// readyExe は ready の補助コメントの下書きを返す実行ファイルの代わり（実際の ready は下書きを置く）。
func (h *hostKit) readyExe() string {
	note := shkit.Write(h.t, filepath.Join(h.base, "ready-note.md"), "Draft を外した\n")
	return shkit.WriteExec(h.t, filepath.Join(h.base, "bin", "readyhalf"), "#!/bin/sh\ncase \" $* \" in\n*\" review ready \"*)\n"+
		"  '"+h.half+"' \"$@\" || exit $?\n  printf '%s\\n' '"+shkit.Slash(note)+"'\n  exit 0 ;;\nesac\nexec '"+h.half+"' \"$@\"\n")
}

var badPosts = []struct {
	name string
	mode postMode
}{
	{"http-500", postHTTP500},
	{"not-json", postNotJSON},
	{"no-id", postNoID},
}

// request・comment・ready の投稿が失敗したとき、成功と言わず、操作記録を未完了（sent でない）で残す。
func TestAFailedOrMalformedPostIsNotReportedAsSuccess(t *testing.T) {
	h := newHostKit(t, hostHalf)
	cases := []struct {
		kind string
		args []string
		env  map[string]string
		dir  string
	}{
		{"request", h.requestArgs(), nil, ""},
		{"comment", h.commentArgs(), nil, ""},
		{"ready", h.readyArgs(), map[string]string{env.EntryPath: h.readyExe()}, h.ws},
	}
	for _, bad := range badPosts {
		for _, c := range cases {
			t.Run(bad.name+"/"+c.kind, func(t *testing.T) {
				h.gl.setMode(bad.mode)
				defer h.gl.setMode(postOK)
				// 前の回の記録を消し、各回を独立にする
				if err := os.RemoveAll(filepath.Join(h.state, "ops")); err != nil {
					t.Fatal(err)
				}
				h.gl.reset()
				done := h.review(opts{env: c.env, dir: c.dir}, c.args...)
				if done.Code == 0 {
					t.Errorf("終了コード 0\n%s%s", done.Stdout, done.Stderr)
				}
				lacks(t, done.Stdout, "OK: 記録した")
				lacks(t, done.Stdout, "同じ依頼は投稿済み")
				states := h.stageStates(c.kind)
				if len(states) == 0 {
					t.Fatalf("%s の操作記録が残っていない", c.kind)
				}
				if slices.Contains(states, "sent") {
					t.Errorf("sent がある: %v", states)
				}
				if c.kind == "ready" {
					has(t, done.Stderr, "補助のコメントは未完了")
				}
				if c.kind == "request" && shkit.Exists(filepath.Join(h.tree, parts(approved), "phases", parentID, "1.requested")) {
					t.Error("投稿が失敗したのに依頼済みのマーカーを置いた")
				}
			})
		}
	}
}

// 応答が JSON でも投稿の URL を作れない（id が無い）ときは、成功と言わない。
func TestAPostResponseWithoutAURLIsNotReportedAsSuccess(t *testing.T) {
	h := newHostKit(t, hostHalf)
	h.gl.setMode(postNoID)
	done := h.review(opts{}, h.commentArgs()...)
	if done.Code == 0 {
		t.Errorf("終了コード 0\n%s%s", done.Stdout, done.Stderr)
	}
	states := h.stageStates("comment")
	if slices.Contains(states, "sent") {
		t.Errorf("sent がある: %v", states)
	}
	lacks(t, done.Stdout+done.Stderr, "note_null")
	if !slices.Contains(states, "unknown") {
		t.Errorf("unknown が無い: %v", states)
	}
}

// ops begin が失敗したら、二重投稿を防ぐため、何も送らずに止める。
func TestAPostWithoutAnOpRecordIsStoppedBeforeSending(t *testing.T) {
	h := newHostKit(t, hostHalf)
	noBegin := h.wrapExe("nobegin", "case \" $* \" in\n*\" ops begin \"*) exit 1 ;;\nesac\n", h.half)
	done := h.review(opts{env: map[string]string{env.EntryPath: noBegin}}, h.commentArgs()...)
	if done.Code == 0 {
		t.Errorf("終了コード 0\n%s%s", done.Stdout, done.Stderr)
	}
	lacks(t, done.Stdout, "OK: 記録した")
	eq(t, "投稿", h.gl.noteCount(), 0)
}

// 別の PC や記録の消失の想定。操作記録を消して打ち直しても、本文の内容のキーで既存の投稿を見つけ、二重に投稿しない。
func TestARequestRetypedAfterLosingTheOpRecordIsNotPostedTwice(t *testing.T) {
	h := newHostKit(t, hostHalf)
	args := h.requestArgs()
	okRun(t, h.review(opts{}, args...))
	eq(t, "投稿", h.gl.noteCount(), 1)
	if err := os.RemoveAll(filepath.Join(h.state, "ops")); err != nil {
		t.Fatal(err)
	}
	eq(t, "記録", len(h.opsRecords()), 0)
	again := okRun(t, h.review(opts{}, args...))
	has(t, again.Stdout, "投稿済み")
	eq(t, "投稿", h.gl.noteCount(), 1)
	// 投稿し直さないまま、記録は送信済みに直る
	for _, s := range h.stageStates("request") {
		eq(t, "段階の状態", s, "sent")
	}
}

// ops の CLI は、スタブ（teststub oprecord）が書いた操作記録を読める。スタブの記録の形が実行ファイルの形と食い違えば落ちる。
// （decide の通しの half が使う --accept-plan のスタブと同じ記録）
func TestTheOpsCLIReadsTheRecordTheStubWrites(t *testing.T) {
	h := newHostKit(t, hostHalf)
	spec := shkit.Write(t, filepath.Join(h.base, "spec.json"), acceptPlanSpec)
	planOut := filepath.Join(h.base, "plan.json")
	okRun(t, run.Command(t, exe.TeststubPath(t), []string{"oprecord", h.state, planOut, spec}))
	var plan struct {
		Op string `json:"op"`
	}
	if err := json.Unmarshal([]byte(shkit.Read(t, planOut)), &plan); err != nil || plan.Op == "" {
		t.Fatalf("計画に op が無い: %v\n%s", err, shkit.Read(t, planOut))
	}
	shown := okRun(t, h.exe(opts{}, "ops", "show", plan.Op))
	var show struct {
		Kind   string `json:"kind"`
		Stages []struct {
			ID    string `json:"id"`
			State string `json:"state"`
		} `json:"stages"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &show); err != nil {
		t.Fatalf("ops show が JSON でない: %v\n%s", err, shown.Stdout)
	}
	eq(t, "種類", show.Kind, "decide")
	var ids []string
	for _, s := range show.Stages {
		ids = append(ids, s.ID)
	}
	eqList(t, "段階", ids, []string{"reply", "note"})
	pending := okRun(t, h.exe(opts{}, "ops", "pending"))
	for _, id := range ids {
		has(t, pending.Stdout, id)
	}
}
