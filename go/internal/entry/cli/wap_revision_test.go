package cli

import (
	"testing"

	"ccnavi/internal/tickets/model/ids"
)

// `write-and-push family`・`sort` の識別子は、親子のチケットの識別子の形だけ（`..` を含む名前は断る）。
func TestWapFamilyID(t *testing.T) {
	for id, want := range map[string]bool{"i0001": true, "i0001-01-01": true, "../x": false, "a..b": false} {
		if got := ids.IsValidFamily(id); got != want {
			t.Errorf("%q: %v", id, got)
		}
	}
}

// `write-and-push sort` の版は sha か refs/remotes/origin/<親のブランチ> だけ。git の引数として読まれる文字列は断る。
func TestRevisionOK(t *testing.T) {
	for since, want := range map[string]bool{
		"0123456789abcdef0123456789abcdef01234567": true,
		"refs/remotes/origin/i0001":                true,
		"HEAD --output=/tmp/x":                     false,
		"--output=/tmp/x":                          false,
		"HEAD":                                     false,
		"refs/remotes/origin/../x":                 false,
	} {
		if got := revisionOK(since); got != want {
			t.Errorf("%q: %v", since, got)
		}
	}
}
