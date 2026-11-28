package host

import (
	"slices"
	"testing"
)

func TestWriteOrder(t *testing.T) {
	got := WriteOrder([]IssueWrite{{Key: "a", Parts: 2}, {Key: "b"}}, []string{"a", "b", "c"}, true)
	want := []WriteStep{
		{Kind: WriteIssue, Key: "a"}, {Kind: WriteIssuePart, Key: "a", Part: 1}, {Kind: WriteIssuePart, Key: "a", Part: 2},
		{Kind: WriteIssue, Key: "b"},
		{Kind: WriteReply, Key: "a"}, {Kind: WriteReply, Key: "b"}, {Kind: WriteReply, Key: "c"},
		{Kind: WriteResolve, Key: "a"}, {Kind: WriteResolve, Key: "b"}, {Kind: WriteResolve, Key: "c"},
		{Kind: WriteNote},
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v\nwant %v", got, want)
	}
	for _, s := range got[:len(got)-1] {
		if s.AfterCommit() {
			t.Errorf("%v はコミットの前", s)
		}
	}
	if !got[len(got)-1].AfterCommit() {
		t.Error("note はコミットのあと")
	}
	if got := WriteOrder(nil, nil, false); len(got) != 0 {
		t.Errorf("何も無ければ空: %v", got)
	}
}
