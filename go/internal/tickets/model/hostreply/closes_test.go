package hostreply

import (
	"reflect"
	"testing"
)

// GitLab の既定の閉じる語の形。GitHub の書き方の表は start の testdata/readers.json（linked-body）が持つ。
func TestClosingRefsGitLab(t *testing.T) {
	for _, tc := range []struct {
		body string
		want []string
	}{
		{"Closing #1\nImplements #2\nfixing g/sub/p#3\nResolving https://gitlab.example/g/p/-/issues/4", []string{"g/p#1", "g/p#2", "g/sub/p#3", "g/p#4"}},
		{"Closes issue #5\nImplemented issues #6, #7 and other/repo#8", []string{"g/p#5", "g/p#6", "g/p#7", "other/repo#8"}},
		{"preclosing #9\n`Closing #10`\nImplementation #11\nsee #12", nil},
	} {
		if got := ClosingRefs("gitlab", "g/p", tc.body); !reflect.DeepEqual(got, tc.want) {
			t.Errorf("%q: got %v, want %v", tc.body, got, tc.want)
		}
	}
	if ClosesIssue("github", "g/p", 1, "Closing #1") || !ClosesIssue("gitlab", "G/P", 1, "Closing #1") {
		t.Error("Closing #1 は GitLab だけが閉じる")
	}
}
