package hostreply

import (
	"reflect"
	"testing"
)

// 書く前の確かめの材料の読み（GitLab）と、計画との突き合わせ。細かい場面は拡張の試験（decide-host.test.ts）が WASM で通す。
func TestWriteInfoAndPrecheck(t *testing.T) {
	op := "0123456789abcdef0123456789abcdef"
	replies := `{"host": "gitlab",
	 "discussions": [
	  {"id": "d1", "notes": [{"body": "x <!-- ccnavi:reply op=` + op + ` key=k -->", "resolvable": true, "type": "DiffNote"}]},
	  {"id": "d2", "notes": [{"body": "y", "resolvable": true}]},
	  {"id": "d3", "notes": [{"body": "z", "resolvable": false}, {"system": true}]}
	 ],
	 "token": {"status": 200, "body": {"scopes": ["read_api"]}},
	 "project": {"permissions": {"project_access": {"access_level": 20}}, "issues_enabled": false}}`
	info, err := WriteInfoOf([]byte(replies))
	if err != nil {
		t.Fatal(err)
	}
	want := []ThreadAbility{
		{ID: "d1", Kind: "ok", CanReply: true, ReplyOps: []string{op}},
		{ID: "d2", Kind: "non-diff", CanReply: true, ReplyOps: []string{}, Unsupported: "差分以外の discussion"},
		{ID: "d3", Kind: "not-resolvable", CanReply: true, ReplyOps: []string{}, Unsupported: "解決できない種類のスレッド"},
	}
	if !reflect.DeepEqual(info.Threads, want) {
		t.Errorf("threads = %+v", info.Threads)
	}
	wantMissing := []Missing{{"api スコープ（read_api では書けません）", "all"}, {"プロジェクトのロール（スレッドの解決は Developer 以上）", "resolve"}, {"プロジェクトの Issues の有効化", "issue"}}
	if !reflect.DeepEqual(info.Missing, wantMissing) {
		t.Errorf("missing = %+v", info.Missing)
	}
	for _, tc := range []struct {
		name string
		plan WritePlan
		kind string
	}{
		{"扱えない種類が先", WritePlan{Replies: []PlanTarget{{"k1", "d1"}, {"k2", "d2"}}}, "unsupported"},
		{"無いスレッド", WritePlan{Resolves: []PlanTarget{{"k", "gone"}}}, "unreadable"},
		{"権限が足りない", WritePlan{Resolves: []PlanTarget{{"k1", "d1"}}}, "permission"},
	} {
		if got := Precheck(tc.plan, info); got.Ok || got.Kind != tc.kind {
			t.Errorf("%s: %+v", tc.name, got)
		}
	}
}
