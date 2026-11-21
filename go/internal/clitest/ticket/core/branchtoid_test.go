package core

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// issue から新しい親のブランチ名（= 識別子）を作るサブコマンド `ccnavi worktree issue-id`。Chrome 拡張と同じ答えを返す
// （変換と採用の可否の表 tests/fixtures/branch-to-id.json は、Go の ids・sh・TS が同じ表で一致を確かめる）。
func TestWorktreeIssueIDMakesTheBranchOfAnIssueAsChromeDoes(t *testing.T) {
	t.Parallel()
	root := filepath.Join(base.Root(), "tests", "fixtures")
	ask := func(words ...string) run.Result {
		return run.Ccnavi(t, append([]string{"--root", root}, words...))
	}
	for _, row := range []struct {
		args  []string
		title string
		want  string
	}{
		{[]string{"7", "feature"}, "Update 10 20", "feature-7-update"},
		{[]string{"7", "feature"}, "Fix bug 12", "feature-7-fix-bug"},
		{[]string{"7", "fix", "web"}, "ログイン画面", "fix-7-web-ログイン画面"},
	} {
		r := ticketkit.OK(t, ask(append(append([]string{"worktree", "issue-id"}, row.args...), "--title", row.title)...))
		if got := strings.TrimSpace(r.Stdout); got != "id "+row.want {
			t.Errorf("%s: %q", row.title, got)
		}
	}
	// タイトルが長くて slug を短くしたときは、`long yes` を添える（start.sh が短い --slug を求める）
	long := ticketkit.OK(t, ask("worktree", "issue-id", "41", "feature", "--title", "テキスト整形CLI（main.sh）を README の仕様どおりに実装する"))
	if got := strings.TrimSpace(long.Stdout); got != "id feature-41-テキスト整形cli-main-sh-を-readme\nlong yes" {
		t.Errorf("長いタイトル: %q", got)
	}
	bad := ticketkit.Fails(t, ask("worktree", "issue-id", "7", "Bad-Prefix", "--title", "x"))
	if strings.TrimSpace(bad.Stdout) != "" {
		t.Errorf("stdout = %q", bad.Stdout)
	}
}
