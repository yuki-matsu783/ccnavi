package chrome

import (
	"testing"

	"ccnavi/internal/tickets/approval/wap"
)

// Chrome の入口が識別子から割り出す親は、hook の write-and-push（wap.FamilyOf）と同じ。
func TestFamilyOfAgreesWithTheHook(t *testing.T) {
	for _, id := range []string{"i0012-05-01", "web-i0012-05-01", "i0012", "web-i0012", "i0012-01", "abc-01-02-03"} {
		if got, want := familyOf(id), wap.FamilyOf(id); got != want {
			t.Errorf("%s: Chrome %q、hook %q", id, got, want)
		}
	}
	if got := familyOf("abc-01-02-03"); got != "abc-01" {
		t.Errorf("右から 2 段を剥がさない: %q", got)
	}
}
