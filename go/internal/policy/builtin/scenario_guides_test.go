package builtin

import (
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/policy/texts"
)

// 組み込みの deny の文は、どれも次の一手（`次:` の行）を持つ（tests/scenarios/test_runner.py の
// test_every_shipped_deny_message_has_a_next_step から移した）。受入シナリオは、案内の無い拒否を弱点として数える。
// git の入口のあるワークスペースと無いワークスペースの両方で見る。
func TestEveryDenyTextHasANextStep(t *testing.T) {
	for _, wrapper := range []bool{false, true} {
		root := workspace(t, wrapper)
		full, _ := Load(root, settingsFor(root, false))
		base, _ := LoadBase(root)
		all := slices.Concat(full.Deny, base.Deny)
		if len(all) < 10 {
			t.Fatalf("wrapper=%v: 組み込みの deny が %d 本しかない", wrapper, len(all))
		}
		for _, r := range all {
			said := texts.Spoken(r)
			if !strings.Contains(said, "\n次: ") && !strings.HasPrefix(said, "次: ") {
				t.Errorf("wrapper=%v %s: 次の一手が無い: %s", wrapper, r.ID, said)
			}
		}
	}
}
