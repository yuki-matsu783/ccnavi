package planedit

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/testkit"
)

// todoFiles は i0001 の todo/ の中身（名前 → 中身）。
func todoFiles(t *testing.T, root string) map[string]string {
	t.Helper()
	dir := testkit.TodoDir(root, "i0001")
	names, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for _, n := range names {
		data, err := os.ReadFile(filepath.Join(dir, n.Name()))
		if err != nil {
			t.Fatal(err)
		}
		out[n.Name()] = string(data)
	}
	return out
}

// 読んでから書くまでの間に子の提案が外で変わったら、何も書かない（開いたときのハッシュが合っていても）。
// 変わった子は変えられたまま残し、ほかの提案は 1 字も変えない。
func TestAProposalChangedWhileSavingWritesNothing(t *testing.T) {
	root := planWorkspace(t)
	conf := testkit.Settings(t, root)
	proposals := approval.ReadProposals(conf, root, nil).Kept
	target, _ := Pick(proposals, "i0001", root)
	s, why := Load(conf, root, target, proposals, CurrentParent(conf, root, "i0001"))
	if s == nil {
		t.Fatal(why)
	}
	order, why := ParseOrder(`{"plan": {"2": [3], "3": [1], "4": [2]}}`, s.Parent)
	if order == nil {
		t.Fatal(why)
	}
	answer := Reorder(s, order)
	if len(answer.changes) == 0 {
		t.Fatal("付け替える子が無い（見本が違う）")
	}
	expect := s.sha()
	child := filepath.Join(testkit.TodoDir(root, "i0001"), "i0001-03-01.md")
	data, err := os.ReadFile(child)
	if err != nil {
		t.Fatal(err)
	}
	changed := strings.Replace(string(data), "本文", "別の本文", 1)
	testkit.Put(t, child, changed)
	before := todoFiles(t, root)
	saved := answer.Save(expect)
	if saved.OK || saved.Written || !slices.ContainsFunc(saved.Messages, func(m string) bool { return strings.Contains(m, "外で変わった") }) {
		t.Errorf("%+v", saved)
	}
	after := todoFiles(t, root)
	if after["i0001-03-01.md"] != changed {
		t.Error("外で変えた子が残っていない")
	}
	if len(after) != len(before) {
		t.Errorf("提案の数が変わった: %v", after)
	}
	for name, text := range before {
		if after[name] != text {
			t.Errorf("%s が変わった", name)
		}
	}
}
