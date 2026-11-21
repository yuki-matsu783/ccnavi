package samples

import (
	"strings"
	"testing"
)

func ids(defs []Definition) []string {
	var out []string
	for _, d := range defs {
		out = append(out, d.ID)
	}
	return out
}

func TestDefinitionsKeepOrderAndIgnoreUnreadableText(t *testing.T) {
	t.Parallel()
	if got := strings.Join(ids(Definitions(Phases)), ","); got != "research,design,acceptance,implement,implement-feedback,chores,chores-feedback" {
		t.Errorf("定義の順: %s", got)
	}
	for _, bad := range []string{"", "- a\n- b\n", "phases: [1]\n", "phases: {a: 1}\n", ": : :\n\t["} {
		if got := Definitions(bad); len(got) != 0 {
			t.Errorf("%q: 読めない本文は空のはず: %v", bad, got)
		}
	}
}

func TestCombinedPhasesKeepTheFirstOfTheSameID(t *testing.T) {
	t.Parallel()
	first := "version: 1\nphases:\n  a:\n    title: 前\n  b:\n    title: b\n"
	second := "version: 1\nphases:\n  a:\n    title: 後\n  c:\n    title: c\n"
	got := CombinedPhases(first, second)
	if !strings.Contains(got, "title: 前") || strings.Contains(got, "title: 後") || !strings.Contains(got, "  c:") {
		t.Errorf("同じ id は前を残す:\n%s", got)
	}
	if CombinedPhases("", "x: 1") != "" {
		t.Error("定義が無ければ空")
	}
}

func TestInsertPhasesLeavesChildrenAndAlreadyCopiedParentsAlone(t *testing.T) {
	t.Parallel()
	parent := ParentText("i1", Plan("design"), ParentOpts{NoCopy: true})
	filled := InsertPhases(parent, Phases)
	if !strings.Contains(filled, "phases:\n  design:\n") {
		t.Fatalf("コピーされない:\n%s", filled)
	}
	if again := InsertPhases(filled, Phases); again != filled {
		t.Error("phases: がある本文は変えない")
	}
	child := ChildText("c1", "i1", 1, nil)
	if InsertPhases(child, Phases) != child {
		t.Error("plan: が無い本文は変えない")
	}
	for _, text := range []string{"plan: x\n", "---\nplan: [a]\n"} {
		if InsertPhases(text, Phases) != text {
			t.Errorf("囲いが無い・閉じない本文は変えない: %q", text)
		}
	}
	// phases.yml に無い定義はコピーしない
	if got := InsertPhases(parent, "version: 1\nphases:\n  other:\n    title: x\n"); !strings.Contains(got, "phases: {}\n") {
		t.Errorf("無い定義は空の phases: になる:\n%s", got)
	}
}
