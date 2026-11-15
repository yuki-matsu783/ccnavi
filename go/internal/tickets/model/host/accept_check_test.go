package host

import "testing"

// 受け入れの計画を sh がホストに書く前の確かめ（AcceptCheck）と、試した印だけ残った返信の見分け（RepliedByHost）。

func TestAcceptCheck(t *testing.T) {
	result := Result{Threads: []Thread{{ID: "abc123"}, {ID: "PRRT_kw1"}}}
	for _, c := range []struct {
		name string
		kind Kind
		id   string
		want string
	}{
		{"GitLab の 16 進で結果に在る", GitLab, "abc123", ""},
		{"GitHub の node id", GitHub, "PRRT_kw1", ""},
		{"経路に使えない字", GitHub, "a/b", msgAcceptBadID},
		{"空", GitLab, "", msgAcceptBadID},
		{"GitLab で 16 進でない", GitLab, "PRRT_kw1", msgAcceptNotGitLab},
		{"取り直した結果に無い", GitLab, "def456", msgAcceptGone},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := AcceptCheck(c.kind, []AcceptThread{{ID: c.id}}, result); got != c.want {
				t.Errorf("AcceptCheck = %q, want %q", got, c.want)
			}
		})
	}
}

func TestRepliedByHost(t *testing.T) {
	for _, c := range []struct {
		name   string
		thread Thread
		who    string
		want   bool
	}{
		{"最後が自分の返信", Thread{ID: "d1", CreatedAt: "t1", LastCommentAt: "t2", LastCommentAuthor: "me"}, "me", true},
		{"最後が他人", Thread{ID: "d1", CreatedAt: "t1", LastCommentAt: "t2", LastCommentAuthor: "you"}, "me", false},
		{"自分が立てた最初のコメント", Thread{ID: "d1", CreatedAt: "t1", LastCommentAt: "t1", LastCommentAuthor: "me"}, "me", false},
		{"時刻が無い", Thread{ID: "d1", CreatedAt: "t1", LastCommentAuthor: "me"}, "me", false},
		{"アカウントが分からない", Thread{ID: "d1", CreatedAt: "t1", LastCommentAt: "t2"}, "", false},
		{"別のスレッド", Thread{ID: "d2", CreatedAt: "t1", LastCommentAt: "t2", LastCommentAuthor: "me"}, "me", false},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := RepliedByHost(Result{Threads: []Thread{c.thread}}, "d1", c.who); got != c.want {
				t.Errorf("RepliedByHost = %v, want %v", got, c.want)
			}
		})
	}
}
