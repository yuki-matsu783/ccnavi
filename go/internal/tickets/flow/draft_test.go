package flow

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

const layoutFlow = `id: f
name: 設計
nodes:
  - id: start
    type: start
    position: {x: 80, y: 160}
    data: {label: 開始}
  - id: end
    type: end
    position: {x: 600, y: 160}
    data: {label: 終了}
connections:
  - {from: start, to: end, fromPort: output, toPort: input}
subAgentFlows:
  - id: s
    nodes:
      - {id: a, type: prompt, position: {x: 1, y: 2}, data: {prompt: p}}
`

func parsed(t *testing.T, text string) any {
	t.Helper()
	data, why := ParseYAML([]byte(text))
	if why != "" {
		t.Fatal(why)
	}
	return data
}

// 位置・グループの大きさと所属・グループの枠のノード（サブフローの中も）は見比べない。キーの順も見ない。
func TestSameIgnoringLayoutSkipsPositionAndGroups(t *testing.T) {
	base := parsed(t, layoutFlow)
	same := []string{
		// 位置を動かし、グループの枠を足して所属させ、キーの順を入れ替えた
		`name: 設計
id: f
nodes:
  - id: g
    type: group
    style: {width: 300}
  - id: start
    type: start
    parentId: g
    position: {x: 10, y: 20}
    data: {label: 開始}
  - {data: {label: 終了}, type: end, id: end, position: {x: 9, y: 9}}
connections:
  - {from: start, to: end, fromPort: output, toPort: input}
subAgentFlows:
  - id: s
    nodes:
      - {id: a, type: prompt, position: {x: 5, y: 5}, style: {}, data: {prompt: p}}
`,
	}
	for _, text := range same {
		if !SameIgnoringLayout(base, parsed(t, text)) {
			t.Errorf("同じとみなしてほしい:\n%s", text)
		}
	}
	texts := map[string]string{
		"名前":       replaceOnce(layoutFlow, "name: 設計", "name: 設計2"),
		"線の行き先":    replaceOnce(layoutFlow, "to: end, fromPort", "to: start, fromPort"),
		"サブフローの中身": replaceOnce(layoutFlow, "{prompt: p}", "{prompt: q}"),
		"ノードの並び":   replaceOnce(layoutFlow, "  - id: start\n    type: start\n    position: {x: 80, y: 160}\n    data: {label: 開始}\n  - id: end\n    type: end\n    position: {x: 600, y: 160}\n    data: {label: 終了}\n", "  - {id: end, type: end, data: {label: 終了}}\n  - {id: start, type: start, data: {label: 開始}}\n"),
		"位置でない欄":   replaceOnce(layoutFlow, "data: {label: 終了}", "data: {label: 終了}\n    width: 3"),
	}
	for name, text := range texts {
		if SameIgnoringLayout(base, parsed(t, text)) {
			t.Errorf("%s: 違うとみなしてほしい", name)
		}
	}
}

func replaceOnce(s, old, repl string) string {
	if !strings.Contains(s, old) {
		panic("見つからない: " + old)
	}
	return strings.Replace(s, old, repl, 1)
}

// 下書きが無ければ見せない。在れば、読めない・いまのフローが無い・中身が違うときに見せる。クローズした子と、いまのフローが
// 読めないときは見せない。
func TestDraftDiffers(t *testing.T) {
	root := t.TempDir()
	info := func() *Info {
		flowFile := filepath.Join(root, "flows", "a.yml")
		draft := filepath.Join(root, "drafts", "a.yml")
		return &Info{Path: flowFile, Tree: root, Exists: exists(flowFile),
			Draft: DraftInfo{Path: draft, Exists: exists(draft)}}
	}
	if DraftDiffers(info()) {
		t.Error("下書きもフローも無い")
	}
	testkit.Put(t, filepath.Join(root, "drafts", "a.yml"), layoutFlow)
	if !DraftDiffers(info()) {
		t.Error("フローが無いのに下書きがある")
	}
	testkit.Put(t, filepath.Join(root, "flows", "a.yml"), replaceOnce(layoutFlow, "x: 80", "x: 1"))
	if DraftDiffers(info()) {
		t.Error("位置だけ違う")
	}
	testkit.Put(t, filepath.Join(root, "drafts", "a.yml"), replaceOnce(layoutFlow, "name: 設計", "name: 別"))
	if !DraftDiffers(info()) {
		t.Error("名前が違う")
	}
	closed := info()
	closed.Closed = true
	if DraftDiffers(closed) {
		t.Error("クローズした子の下書き")
	}
	testkit.Put(t, filepath.Join(root, "drafts", "a.yml"), "nodes: [\n")
	if !DraftDiffers(info()) {
		t.Error("下書きが YAML として読めない")
	}
	testkit.Put(t, filepath.Join(root, "drafts", "a.yml"), "nodes:\n  - {id: 1}\n")
	if !DraftDiffers(info()) {
		t.Error("下書きの id が文字列でない")
	}
	testkit.Put(t, filepath.Join(root, "flows", "a.yml"), "nodes: [\n")
	if DraftDiffers(info()) {
		t.Error("いまのフローが読めない")
	}
	if DraftDiffers(nil) {
		t.Error("フローを持たないチケット")
	}
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}
