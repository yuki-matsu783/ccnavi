package gitlog

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSummary(t *testing.T) {
	cases := []struct {
		sub, body, want string
	}{
		{"diff", "diff --git a/x b/x\n--- a/x\n+++ b/x\n-old\n+new\n+more\n", "1 ファイル +2 -1"},
		{"log", "commit 0a1b\nAuthor: x\n\ncommit ffee\n", "2 コミット"},
		{"status", " M a\n?? b\n## main\n", "変更 2 件"},
		{"status", "", "変更 0 件"},
		{"branch", "* main\n  x\n", "2 行"},
		{"diff", "", "0 行"},
	}
	for _, c := range cases {
		if got := Summary(c.sub, splitLines(c.body)); got != c.want {
			t.Errorf("%s %q: %q, want %q", c.sub, c.body, got, c.want)
		}
	}
}

// 成功は要約と先頭の行と残りの案内、失敗は末尾の行。記録は全量を持ち、決まった本数だけ残る。
func TestReport(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "logs")
	body := "a\nb\nc\nd\n"
	in := Input{Root: root, Cwd: root, LogDir: dir, Sub: "show", Args: []string{"--no-ext-diff"}, Output: []byte(body),
		MaxLines: 2, FailLines: 1, Keep: 1, Warn: "w"}
	var out strings.Builder
	rel, lines, err := Report(&out, in)
	if err != nil || lines != 4 {
		t.Fatal(err, lines)
	}
	want := "ok  git show  4 行  log=" + rel + "\na\nb\n... 残り 2 行は " + rel + " にある（全文は Read ツールでこのファイルを読む）\n警告: w\n"
	if out.String() != want || !strings.HasPrefix(rel, "logs"+string(os.PathSeparator)+"git-") {
		t.Errorf("成功の出力:\n%s", out.String())
	}
	saved, _ := os.ReadFile(filepath.Join(root, rel))
	if !strings.HasSuffix(string(saved), "$ git show --no-ext-diff\n--- 出力 ---\n"+body) {
		t.Errorf("記録:\n%s", saved)
	}
	in.Status = 1
	out.Reset()
	if _, _, err := Report(&out, in); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(out.String(), "fail  git show  exit=1  log=") || !strings.HasSuffix(out.String(), "\nd\n") {
		t.Errorf("失敗の出力:\n%s", out.String())
	}
	if logs, _ := filepath.Glob(filepath.Join(dir, "git-*.log")); len(logs) != 1 {
		t.Errorf("残った記録 %d 本", len(logs))
	}
}
