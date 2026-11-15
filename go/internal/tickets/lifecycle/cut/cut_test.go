package cut

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/reply"
)

// 引数の読み。誤りは識別子と終了コードで止め、使い方を出すものは usage を先に付ける。
func TestArgs(t *testing.T) {
	for _, c := range []struct {
		args []string
		want string // 答えの先頭の行（通るなら空）
	}{
		{[]string{"topic"}, ""},
		{[]string{"topic", "--base=main", "--accept", "abc", "--no-wip", "--no-wip", "--side-by-side"}, ""},
		{[]string{"topic", "--offline"}, "fail retired-arg 2"},
		{[]string{"topic", "--from-origin"}, "fail retired-arg 2"},
		{[]string{"--bogus"}, "usage yes"},
		{[]string{"topic", "--base"}, "fail base-missing 2"},
		{[]string{"topic", "--copy-wip", "--no-wip"}, "fail wip-both 2"},
		{[]string{}, "usage yes"},
		{[]string{"a", "b"}, "fail two-names 2"},
		{[]string{"a/b"}, "fail bad-name 2"},
		{[]string{"main"}, "fail bad-name 2"},
		{[]string{"feature-１２３"}, "fail bad-name 2"},
		{[]string{"feature-\u304b\u3099"}, "fail bad-name 2"}, // NFD（か + 濁点）
		{[]string{"topic-" + strings.Repeat("a", 59)}, "fail bad-name 2"},
		{[]string{"--help", "--offline"}, "help yes"},
	} {
		r := &run{in: Input{Args: c.args}, o: &reply.Lines{}}
		passed := r.args()
		first, _, _ := strings.Cut(r.o.String(), "\n")
		if passed != (c.want == "") || first != c.want {
			t.Errorf("%q: %v %q, want %q", c.args, passed, first, c.want)
		}
	}
}

// 確認コードは材料が同じなら同じで、材料が動けば変わる（git hash-object の先頭 8 字と同じ作り）。
func TestCode(t *testing.T) {
	if got := code("x"); got != "c1b0730e" {
		t.Errorf("code(x) = %q", got)
	}
	if code("a") == code("b") {
		t.Error("材料が違っても同じコード")
	}
}

// ワークツリーの置き場は、綴りの大文字小文字が違っても置き場と見る（書きかけに数えない）。
func TestInWorktrees(t *testing.T) {
	for path, want := range map[string]bool{
		".claude/worktrees/a/x": true, `".claude/worktrees/a b"`: true, ".Claude/Worktrees/a": true,
		".claude/worktree": false, "src/.claude/worktrees/a": false,
	} {
		if got := inWorktrees(path); got != want {
			t.Errorf("inWorktrees(%q) = %v", path, got)
		}
	}
}
