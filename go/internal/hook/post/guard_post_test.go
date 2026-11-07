package post

import "testing"

// 「ccnavi 自身が書いたかもしれない」と添えるのは、承認済みの置き場の下の報告だけ（tests/guard/test_post.py から移した）。
func TestInApprovedArea(t *testing.T) {
	for path, want := range map[string]bool{
		".ccnavi/approved/doing/a.md":           true,
		"projects/x/.ccnavi/approved/done/a.md": true,
		"wip/proposals/todo/a.md":               false,
		"src/.ccnavi/approvedX/a.md":            false,
	} {
		if got := inApprovedArea(path); got != want {
			t.Errorf("%s: %v", path, got)
		}
	}
}
