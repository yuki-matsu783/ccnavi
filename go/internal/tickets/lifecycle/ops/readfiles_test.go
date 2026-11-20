package ops

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/reads"
	"ccnavi/internal/tickets/model/fields"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// finish を打ったセッションと、チケットのワークツリーで作業したセッションの記録が、セッションごとに回数の多い順で
// 本文の最後へ載る。ワークツリーに触れていないほかのセッションは載らない。記録が無ければ本文は変わらない。
func TestNoteReadFilesWritesEachSession(t *testing.T) {
	root := testkit.ShapedWorkspace(t)
	conf := testkit.Settings(t, root)
	worktree := testkit.AddWorktree(t, root, "i0001-01-01", "main")
	for _, full := range []string{filepath.Join(root, "a.go"), filepath.Join(root, "b.go"), filepath.Join(worktree, "c.go")} {
		testkit.Put(t, full, "x\n")
	}
	path := testkit.Approved(root, "i0001", "doing", "i0001-01-01")
	text := testkit.ChildText("i0001-01-01", "i0001", "1")
	testkit.Put(t, path, text)
	found := &ticket.Ticket{ID: "i0001-01-01", Path: path}

	t.Setenv(env.ClaudeSessionID, "s1")
	noteReadFiles(conf, root, found)
	if got, _ := os.ReadFile(path); string(got) != text {
		t.Fatalf("記録が無いのに書いた: %q", got)
	}
	note := func(session, tool, file string) {
		in := hookio.Input{Event: hookio.PostToolUse, ToolName: tool, SessionID: session, Cwd: root,
			ToolInput: pyjson.NewObject("file_path", filepath.Join(root, file))}
		reads.Note(conf.State, root, in, time.Now())
	}
	note("s0", config.ToolRead, filepath.Join(".claude", "worktrees", "i0001-01-01", "c.go"))
	noteReadFiles(conf, root, found)
	note("s1", config.ToolRead, "b.go")
	noteReadFiles(conf, root, found)
	note("s1", config.ToolRead, "a.go")
	note("s1", config.ToolEdit, "a.go")
	note("s2", config.ToolRead, "b.go")
	noteReadFiles(conf, root, found)
	got, _ := os.ReadFile(path)
	want := "## 参照したファイル\n\n### セッション `s0`\n\n- `c.go`（Read 1）\n" +
		"\n### セッション `s1`\n\n- `a.go`（Edit 1・Read 1）\n- `b.go`（Read 1）\n"
	if !strings.HasSuffix(string(got), want) || !fields.HasReadFiles(string(got)) {
		t.Fatalf("got %q", got)
	}
}

func TestReadFileLinesFoldsTheRest(t *testing.T) {
	var listed []reads.Item
	for range readFilesListed + 3 {
		listed = append(listed, reads.Item{Rel: "x", Tools: map[string]int{"Read": 1}, Count: 1})
	}
	lines := readFileLines(listed)
	if len(lines) != readFilesListed+1 || lines[len(lines)-1] != "ほか 3 件" {
		t.Fatalf("got %d lines, last %q", len(lines), lines[len(lines)-1])
	}
}
