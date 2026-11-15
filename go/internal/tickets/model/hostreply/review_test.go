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
