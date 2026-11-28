package hostreply

import "testing"

// GitLab の discussions の読み方（Chrome 拡張も WASM の copy で同じものを使う）。system のノートを除き、解決できるノートの無い
// discussion は除く。代表は目印で始まらない最初のノートで、own は全部が目印で始まり、書き手が全部代表と同じときだけ真。
func TestDiscussions(t *testing.T) {
	data := `[
	 {"id": 1, "notes": [{"id": 10, "system": true, "resolvable": true}]},
	 {"id": "a", "notes": [{"id": 11, "body": "<!-- ccnavi:request -->", "resolvable": true, "resolved": true, "author": {"id": 7}},
	                       {"id": 12, "body": "<!-- ccnavi:x -->", "resolvable": true, "resolved": false, "author": {"id": 8, "username": "eve"}}]},
	 {"id": "b", "notes": [{"id": 13, "body": "<!-- ccnavi:request -->", "resolvable": true, "resolved": true, "author": {"id": 7}},
	                       {"id": 14, "body": "直して", "resolvable": true, "resolved": true, "author": {"id": 8, "username": "bob"},
	                        "position": {"new_path": "a.go", "new_line": 3}, "created_at": "t"}]},
	 {"id": "c", "notes": [{"id": 15, "body": "<!-- ccnavi:request -->", "resolvable": true, "author": {"id": 7}}]},
	 {"id": "d", "notes": [{"id": 16, "body": "x", "resolvable": false}]},
	 {"id": "e", "notes": [{"id": 17, "body": "<!-- ccnavi:request -->", "resolvable": true, "author": null}]}
	]`
	got, err := Threads(FormDiscussions, "https://h/mr/1", [][]byte{[]byte(data)})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		`{"id":"a","resolved":false,"url":"https://h/mr/1#note_11","path":"","line":0,"body":"<!-- ccnavi:request -->","created_at":"","author":"7","own":false,"last_id":"12","last_at":"","last_author":"eve"}`,
		`{"id":"b","resolved":true,"url":"https://h/mr/1#note_14","path":"a.go","line":3,"body":"直して","created_at":"t","author":"8","own":false,"last_id":"14","last_at":"t","last_author":"bob"}`,
		`{"id":"c","resolved":false,"url":"https://h/mr/1#note_15","path":"","line":0,"body":"<!-- ccnavi:request -->","created_at":"","author":"7","own":true,"last_id":"15","last_at":"","last_author":""}`,
		// 書き手が分からない目印の投稿は機構自身と見ない
		`{"id":"e","resolved":false,"url":"https://h/mr/1#note_17","path":"","line":0,"body":"<!-- ccnavi:request -->","created_at":"","author":"","own":false,"last_id":"17","last_at":"","last_author":""}`,
	}
	if len(got) != len(want) {
		t.Fatalf("%d 件: %v", len(got), got)
	}
	for i, th := range got {
		if s := JSONText(th); s != want[i] {
			t.Errorf("%d:\n got %s\nwant %s", i, s, want[i])
		}
	}
}

// MR の選び方・マージ済みの読み・目印の照合・Draft の印の除き方。
func TestReaders(t *testing.T) {
	mrs := `[{"iid": 3, "source_project_id": 99, "sha": "x"}, {"iid": 4, "source_project_id": 42, "sha": "s", "source_branch": "b",
	  "web_url": "u", "target_branch": "main", "work_in_progress": true}]`
	if got, _ := FindMR("gitlab", "42", []byte(mrs)); got != "number 4\nurl u\nsource b\ntarget main\ndraft true\n" {
		t.Errorf("FindMR = %q", got)
	}
	if got, _ := Merged("gitlab", "", "42", []byte(mrs)); got != "merged 4 s b\n" {
		t.Errorf("Merged(list) = %q", got)
	}
	if got, _ := Merged("github", "5", "", []byte(`{"number": 5, "merged_at": null}`)); got != "none\n" {
		t.Errorf("Merged(one) = %q", got)
	}
	if _, err := Merged("github", "", "", []byte(`{}`)); err == nil {
		t.Error("Merged は一覧でない応答を読めないとする")
	}
	notes := `[{"id": 1, "body": "a <!-- m -->", "author": {"username": "me"}}, {"id": 2, "body": "<!-- m -->", "author": {"username": "you"}},
	  {"id": 3, "description": "<!-- m -->", "web_url": "w", "author": {"username": "me"}}]`
	if got, _ := Marked("gitlab", "<!-- m -->", "me", "https://h/1", []byte(notes)); got != "https://h/1#note_1,w" {
		t.Errorf("Marked = %q", got)
	}
	issues := `[{"html_url": "p", "pull_request": {}, "body": "<!-- m -->"}, {"html_url": "i", "body": "<!-- m -->", "user": {"login": "me"}}]`
	if got, _ := Marked("github", "<!-- m -->", "", "", []byte(issues)); got != "i" {
		t.Errorf("Marked(github) = %q", got)
	}
	for title, want := range map[string]string{"Draft: x": "x", "[WIP] [draft]: y": "y", "Fix draft": "Fix draft"} {
		if got := StripDraft(title); got != want {
			t.Errorf("StripDraft(%q) = %q, want %q", title, got, want)
		}
	}
	if got := UndraftTitle("github", []byte(`{"node_id": "N", "title": "Draft: "}`)); got != "node N\ntitle empty\n" {
		t.Errorf("UndraftTitle = %q", got)
	}
	if got := UndraftDone("gitlab", []byte(`{"draft": false, "work_in_progress": true}`)); got != "true" {
		t.Errorf("UndraftDone = %q", got)
	}
}

// MR がマージ済みか（`ccnavi-review.sh merged`）。一覧と 1 本の応答、GitLab はこのプロジェクト（42）から出たものだけ。
func TestMergedTable(t *testing.T) {
	cases := []struct {
		kind, number, pid, data, want string
	}{
		{"github", "", "", `[{"number": 7, "merged_at": null}, {"number": 9, "merged_at": "x", "head": {"sha": "abc123"}}]`, "merged 9 abc123 \n"},
		{"github", "", "", `[]`, "none\n"},
		{"github", "9", "", `{"number": 9, "merged_at": "x", "head": {"sha": "abc123", "ref": "feature/12-x"}}`, "merged 9 abc123 feature/12-x\n"},
		{"github", "9", "", `{"number": 9, "merged_at": null, "head": {"sha": "abc123"}}`, "none\n"},
		{"gitlab", "", "42", `[{"iid": 9, "source_project_id": 42, "sha": "abc123"}]`, "merged 9 abc123 \n"},
		{"gitlab", "", "42", `[{"iid": 5, "source_project_id": 99}]`, "none\n"},
		{"gitlab", "9", "42", `{"iid": 9, "state": "merged", "source_project_id": 42, "sha": "abc123", "source_branch": "feature/12-x"}`, "merged 9 abc123 feature/12-x\n"},
		{"gitlab", "9", "42", `{"iid": 9, "state": "opened", "source_project_id": 42, "sha": "abc123"}`, "none\n"},
		{"gitlab", "9", "42", `{"iid": 9, "state": "merged", "source_project_id": 99, "sha": "abc123"}`, "none\n"},
		{"github", "9", "", `{"number": 9, "merged_at": "x", "head": {"sha": "abc123", "repo": {"full_name": "fork/r"}}, "base": {"repo": {"full_name": "o/r"}}}`, "none\n"},
		{"github", "", "", `[{"number": 9, "merged_at": "x", "head": {"sha": "abc123", "repo": {"full_name": "fork/r"}}, "base": {"repo": {"full_name": "o/r"}}}]`, "none\n"},
	}
	for _, c := range cases {
		got, err := Merged(c.kind, c.number, c.pid, []byte(c.data))
		if err != nil || got != c.want {
			t.Errorf("Merged(%s, %q, %s) = %q, %v; want %q", c.kind, c.number, c.data, got, err, c.want)
		}
	}
	if _, err := Merged("github", "", "", []byte(`not json`)); err == nil {
		t.Error("読めない応答は error")
	}
}

// フォークから出た MR は、`merged` の問い合わせと `mr-state` の答えの両方で、マージ済みと数えない。
func TestForkMRIsNeverMerged(t *testing.T) {
	gitlab := `{"iid": 9, "state": "merged", "source_project_id": 99, "target_project_id": 42, "sha": "abc123", "web_url": "https://u/9"}`
	github := `{"number": 9, "state": "closed", "merged_at": "x", "head": {"sha": "abc123", "repo": {"full_name": "fork/r"}}, "base": {"repo": {"full_name": "o/r"}}, "html_url": "https://u/9"}`
	for _, c := range []struct{ kind, pid, data string }{{"gitlab", "42", gitlab}, {"github", "", github}} {
		merged, err := Merged(c.kind, "9", c.pid, []byte(c.data))
		if err != nil || merged != "none\n" {
			t.Errorf("Merged(%s) = %q, %v, want none", c.kind, merged, err)
		}
		if state, answer := MRState(c.kind, "https", "h", "g/p", "9", false, []byte(c.data)); state == "merged" {
			t.Errorf("MRState(%s) = %q %q, want not merged", c.kind, state, answer)
		}
	}
}
