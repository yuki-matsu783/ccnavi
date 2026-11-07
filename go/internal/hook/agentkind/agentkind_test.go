package agentkind

import (
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

type kindCase struct {
	Type  string   `json:"type"`
	Tools []string `json:"tools"`
	want  Kind
}

// 読み取りと確かめられたものだけを Read にし、見分けられないものは Edit に倒す。Python と同じ答えか。
func TestOf(t *testing.T) {
	near, far := t.TempDir(), t.TempDir()
	put := func(root, name, text string) {
		testkit.Put(t, filepath.Join(root, ".claude", "agents", name), text)
	}
	put(near, "reader.md", "---\nname: reader\ntools: Read, Grep\n---\n")
	put(near, "lister.md", "---\ntools: [Read, Glob, LS]\n---\n")
	put(near, "shell.md", "---\ntools: Read, Bash\n---\n")
	put(near, "free.md", "---\nname: free\n---\n")
	put(near, "empty.md", "---\ntools: \"\"\n---\n")
	put(near, "mcp.md", "---\ntools: [Read, mcp__x__write]\n---\n")
	put(near, "deny.md", "---\ntools: Read\ndisallowedTools: Write\n---\n")
	put(near, "mixed.md", "---\ntools: [Read, 3]\n---\n")
	put(near, "explore.md", "---\nname: Explore\ntools: Read, Write\n---\n") // 組み込みの名前を上書きする
	put(near, "bom.md", "\ufeff---\nname: bom\ntools: Read\n---\n")
	put(near, "open.md", "---\nname: open\ntools: Read\n")
	put(near, "broken.md", "---\nname: broken\ntools: [Read\n---\n")
	put(near, "spaced.md", "  ---  \nname:  Spaced \ntools: Read\n---\n")
	put(far, "reader.md", "---\nname: reader\ntools: Write\n---\n") // 近い側が先
	put(far, "faronly.md", "---\ntools: Read\n---\n")
	// リンクを作れない機械でも、無い定義として同じ答え（Edit）になる。
	_ = os.Symlink(filepath.Join(near, ".claude", "agents", "lister.md"), filepath.Join(near, ".claude", "agents", "linked.md"))
	cases := []kindCase{
		{Type: "", want: Edit},
		{Type: "  ", want: Edit},
		{Type: "Explore", want: Edit}, // プロジェクトの定義が組み込みより先
		{Type: "Plan", want: read},
		{Type: "claude-code-guide", want: read},
		{Type: "general-purpose", want: Edit},
		{Type: "reader", want: read},
		{Type: "READER", want: read},
		{Type: "lister", want: read},
		{Type: "shell", want: Edit},
		{Type: "free", want: Edit},
		{Type: "empty", want: Edit},
		{Type: "mcp", want: Edit},
		{Type: "deny", want: read},
		{Type: "mixed", want: Edit},
		{Type: "bom", want: read},
		{Type: "open", want: Edit},
		{Type: "broken", want: Edit},
		{Type: "spaced", want: read},
		{Type: "faronly", want: read},
		{Type: "linked", want: Edit}, // リンクは辿らない
		{Type: "reader", Tools: []string{}, want: Edit},
		{Type: "free", Tools: []string{"read", " Grep "}, want: read},
		{Type: "reader", Tools: []string{"Read", "Edit"}, want: Edit},
		{Type: "", Tools: []string{"Task"}, want: read},
	}
	roots := []string{near, far}
	py := golden.Load[[]Kind](t, "kind")
	for i, c := range cases {
		got := Of(roots, c.Type, c.Tools)
		if got != c.want || got != py[i] {
			t.Errorf("Of(%q, %v) = %s, want %s（Python %v）", c.Type, c.Tools, got, c.want, py)
		}
	}
}
