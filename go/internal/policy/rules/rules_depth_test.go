package rules

import (
	"strings"
	"testing"
)

// 照合を打ち切った式（数 MB のコマンドに繰り返しを含む式）は、止める側では当たり、allow では外れる。
func TestMatchesDepthLimit(t *testing.T) {
	long := strings.Repeat("ab", 2<<20) + "x"
	for _, tc := range []struct {
		decision Decision
		regex    string
		want     bool
	}{
		{Deny, `(?:ab)*c`, true},
		{Ask, `(?:ab)*c`, true},
		{Allow, `(?:ab)*x`, false},
	} {
		re, problem := CompileCondition("", tc.regex, "t")
		if re == nil {
			t.Fatalf("%s: %s", tc.regex, problem)
		}
		r := &Rule{ID: "t", Decision: tc.decision, Match: "Bash", Compiled: re}
		if got := r.Matches("Bash", long); got != tc.want {
			t.Errorf("%s %s: %v, want %v", tc.decision, tc.regex, got, tc.want)
		}
	}
}
