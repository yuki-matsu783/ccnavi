package core

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// `ccnavi text <識別子> [名前=値 ...]` が、文面ファイルの文を出すか、標準出力に何も出さずに失敗すること。
// 文面ファイルの読み込み・検査・組み立ては `policy/texts` の go test が見る。

const sampleMessages = `version: 1
messages:
  NEXT_SAMPLE:
    kind: next
    values: [args_q]
    next: "打ち直す: {script:ccnavi-start.sh} {args_q}"
`

func TestTextCLIPrintsTheTextOrFailsWithNothingOnStdout(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	path := filepath.Join(root, ".ccnavi", "messages", "a.yml")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(sampleMessages), 0o644); err != nil {
		t.Fatal(err)
	}
	text := func(words ...string) run.Result {
		return run.Ccnavi(t, append([]string{"--root", root, "text"}, words...))
	}

	if r := text("NEXT_SAMPLE", "args_q=--x"); r.Code != 0 || !strings.HasPrefix(r.Stdout, "次: 打ち直す:") {
		t.Errorf("code = %d, stdout = %q, stderr = %q", r.Code, r.Stdout, r.Stderr)
	}
	if r := text("NEXT_NOPE"); r.Code != 1 || r.Stdout != "" {
		t.Errorf("未知の識別子: code = %d, stdout = %q", r.Code, r.Stdout)
	}
	if r := text(); r.Code != 2 {
		t.Errorf("識別子なし: code = %d", r.Code)
	}
}
