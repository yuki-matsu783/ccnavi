package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/records/stopreview"
)

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

// ワークツリーの中から起動しても、ワークツリーの .claude で止まらずにワークスペースルートを返す。
func TestDefaultRootSkipsTheWorktree(t *testing.T) {
	ws, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	wt := filepath.Join(ws, ".claude", "worktrees", "x")
	sub := filepath.Join(wt, "sub")
	if err := os.MkdirAll(filepath.Join(wt, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv(env.ClaudeProjectDir, "")
	for _, start := range []string{wt, sub} {
		t.Chdir(start)
		if got, _ := filepath.EvalSymlinks(defaultRoot()); got != ws {
			t.Errorf("%s から: %s", start, got)
		}
	}
}

// ワークツリーの位置の見分けは、どの機械でも大文字小文字を区別しない。
func TestIsWorktreeDirIgnoresCase(t *testing.T) {
	for path, want := range map[string]bool{
		filepath.Join("ws", ".CLAUDE", "WORKTREES", "x"): true,
		filepath.Join("ws", ".claude", "worktrees", "x"): true,
		filepath.Join("ws", "x"):                         false,
	} {
		if got := isWorktreeDir(path); got != want {
			t.Errorf("%s: %v", path, got)
		}
	}
}

// write-and-push が通す wip/ の例外は、ツリーの中の、今は無い wip/ の下のものだけ。在るもの・wip/ の外・ツリーの外
// （別の親）は通さない。
func TestReadyRemovedWip(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	base := filepath.Join(root, ".claude", "worktrees", "p1")
	write(t, filepath.Join(base, "wip", "kept.md"), "x\n")
	for path, want := range map[string]bool{
		filepath.Join(base, "wip", "gone.md"):             true,
		filepath.Join(base, "WIP", "design", "gone.html"): true,
		filepath.Join(base, "wip", "kept.md"):             false,
		filepath.Join(base, "src", "gone.py"):             false,
		filepath.Join(base, "wipx", "gone.md"):            false,
		filepath.Join(base, "wip"):                        false,
		base:                                              false,
		filepath.Join(root, "wip", "gone.md"):             false,
		filepath.Join(root, ".claude", "worktrees", "i0002", "wip", "gone.md"): false,
	} {
		if got := readyRemovedWip(path, base); got != want {
			t.Errorf("%s: %v", path, got)
		}
	}
}

// `ccnavi text` は文を出すか、標準出力を空にして失敗する（引けない 1・引数の誤り 2）。
func TestTextPrintsOrFailsWithNothingOnStdout(t *testing.T) {
	root := t.TempDir()
	write(t, filepath.Join(root, ".ccnavi", "messages", "a.yml"), "version: 1\nmessages:\n"+
		"  NEXT_SAMPLE:\n    kind: next\n    values: [args_q]\n    next: \"打ち直す: {args_q}\"\n")
	var out, errOut bytes.Buffer
	if code := runText(&out, &errOut, root, []string{"NEXT_SAMPLE", "args_q=--x"}); code != 0 ||
		!strings.HasPrefix(out.String(), "次: 打ち直す: --x") {
		t.Errorf("code %d: %q %q", code, out.String(), errOut.String())
	}
	for _, c := range []struct {
		words []string
		code  int
	}{
		{[]string{"NEXT_NOPE"}, textUnavailable},
		{[]string{"NEXT_SAMPLE"}, textUnavailable}, // 値の不足
		{nil, textUsage},
		{[]string{"NEXT_SAMPLE", "noequals"}, textUsage},
	} {
		out.Reset()
		if code := runText(&out, &errOut, root, c.words); code != c.code || out.Len() != 0 {
			t.Errorf("%v: code %d、標準出力 %q", c.words, code, out.String())
		}
	}
}

// 判定役に渡す提案の本文は、sha の形の版と、ツリーの中の md だけから読む（git の引数やリンクの先を材料にしない）。
func TestProposalBodyValidatesCommitAndPaths(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	ws := filepath.Join(root, "ws")
	write(t, filepath.Join(ws, "a.md"), "目的\n")
	outside := filepath.Join(root, "outside.md")
	write(t, outside, "外\n")
	link := filepath.Join(ws, "link.md")
	if err := os.Symlink(outside, link); err != nil {
		t.Skip("リンクを作れない:", err)
	}
	sent := stopreview.Proposal{Route: stopreview.RouteSent, Commit: "--output=x", Files: []stopreview.File{{Path: "a.md"}}}
	if got := proposalBody(ws, sent); got != "" {
		t.Errorf("sha でない版から読んだ: %q", got)
	}
	unsent := stopreview.Proposal{Route: stopreview.RouteUnsent,
		Files: []stopreview.File{{Path: filepath.Join(ws, "a.md")}, {Path: link}, {Path: filepath.Join(ws, "a.txt")}}}
	got := proposalBody(ws, unsent)
	if !strings.Contains(got, "目的") || strings.Contains(got, "外") || strings.Count(got, "---") != 2 {
		t.Errorf("未送信の本文: %q", got)
	}
}

// 引数の組み合わせの誤りは、設定を読む前に断る。ユーザ専用のオプションは 2、ほかは 1。
// ユーザ専用の入口の誤り（EntryError）は、文面ファイルの what・なぜ・次: で言う。
func TestEntryErrorIsSaidFromTheMessageFile(t *testing.T) {
	var out, errOut bytes.Buffer
	if code := Run(strings.NewReader(""), &out, &errOut, []string{"--entry", "sh", "ticket"}); code != 2 {
		t.Fatalf("code %d", code)
	}
	for _, want := range []string{"ccnavi: ユーザ専用の入口を使えません: ", "\nなぜ: ", "\n次: "} {
		if !strings.Contains(errOut.String(), want) {
			t.Errorf("%q が無い: %q", want, errOut.String())
		}
	}
}

func TestUsageRefusals(t *testing.T) {
	for _, c := range []struct {
		args []string
		code int
		said string
	}{
		{[]string{"--agree"}, 2, "--agree はユーザ専用です"},
		{[]string{"--yes"}, 2, "--yes はユーザ専用です"},
		{[]string{"--permission-mode", "auto"}, 1, "--permission-mode は --test でだけ指定できる"},
		{[]string{"--test", "Bash", "--json", "ls", "x"}, 1, "--test は TOOL と SUBJECT の 2 語だけを取る（余った語: x）"},
		{[]string{"--test", "Bash", "--root"}, 1, "--test の TOOL と SUBJECT にオプション（--root）が入った"},
		{[]string{"--record-tree", "x"}, 1, "--record-tree は --record-writes と一緒に指定してください。"},
		{[]string{"--actor", "a b"}, 1, "--actor にはホストのアカウント名"},
		{[]string{"--actor", "me"}, 1, "--actor は review confirm か"},
		{[]string{"--via", "board"}, 1, "--via は decide の --actor と一緒に指定してください。"},
		{[]string{"--all"}, 1, "--all は --help と一緒にだけ指定できます"},
		{[]string{"--close"}, 1, "ccnavi: error: unrecognized arguments: --close"},
		{[]string{"--root", "a", "--root", "b", "ticket", "status"}, 1, "--root は 1 度しか渡せない（2 度渡された）"},
		{[]string{"--entry", "sh", "ticket"}, 2, "--entry は user の入口と一緒に渡す"},
	} {
		var out, errOut bytes.Buffer
		code := Run(strings.NewReader(""), &out, &errOut, c.args)
		if code != c.code || !strings.Contains(errOut.String(), c.said) {
			t.Errorf("%v: code %d: %s", c.args, code, errOut.String())
		}
	}
}
