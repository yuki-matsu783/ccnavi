package difflib

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

// testdata/unified.json は Python 3.13 の difflib.unified_diff(a, b, fromfile="a/x", tofile="b/x", n=n, lineterm="")
// の答え。200 行以上でよく出る行を手がかりから外す（autojunk）形と、前後の行数（n）の違いを含む。
func TestUnifiedDiffMatchesPython(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("testdata", "unified.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		A, B []string
		N    int
		Want []string
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		if got := UnifiedDiff(c.A, c.B, "a/x", "b/x", c.N); !slices.Equal(got, c.Want) {
			t.Errorf("%d (n=%d): %q\nwant %q", i, c.N, got, c.Want)
		}
	}
}
