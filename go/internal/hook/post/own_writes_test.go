package post

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/testkit"
)

// ccnavi 自身が書く場所は、綴りの大文字小文字が違っても自分の書き込みとして除く（パスは大文字小文字を区別せずに比べる）。
func TestOwnWritesAreLeftOutWhateverTheCase(t *testing.T) {
	root := testkit.TempRoot(t)
	state := filepath.Join(root, ".ccnavi", "state")
	deny, problems := rules.Builtin(rules.Deny, []rules.Spec{{ID: "builtin:everything", Match: "Write", Glob: "*", Text: rules.TextRef{ID: "RULE_GUARD_RECORDS"}}}, root)
	if len(problems) > 0 || len(deny) != 1 {
		t.Fatal(problems)
	}
	all := &rules.RuleSet{Deny: deny}
	// 置き場の外の変更は報告する（照合が効いていることの確かめ）。
	if got := (scan{changes: []git.Change{{Kind: git.KindChanged, Path: "x", Full: filepath.Join(root, "x")}}, rules: all, mine: []string{state}}).findings(); len(got) != 1 {
		t.Fatalf("置き場の外の変更を報告しない: %d", len(got))
	}
	for _, full := range []string{filepath.Join(state, "s1.json"), filepath.Join(root, ".CCNAVI", "State", "s1.json")} {
		s := scan{changes: []git.Change{{Kind: git.KindChanged, Path: "x", Full: full}}, rules: all, mine: []string{state}}
		if got := s.findings(); len(got) != 0 {
			t.Errorf("%s: 自分の書き込みを報告した: %v", full, got[0].change.Path)
		}
	}
}
