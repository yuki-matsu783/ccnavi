package judge

import (
	"strings"
	"testing"

	"ccnavi/internal/hook/reasons"
	"ccnavi/internal/infra/config"
)

// 書き込みのリダイレクト先が変数か置換なら、書く先を読めない形として扱い、利用者の allow（受入テストのルールは何でも
// allow）で通さない。読める行き先・読み込み・fd の複製はこれまでどおり allow で通る。
func TestExpandedRedirectTargetIsNotAllowed(t *testing.T) {
	root := fixtureWorkspace(t, "")
	run := func(command, permission string) judgeAnswer {
		return runGo(t, root, judgeCase{Payload: call(config.ToolBash, root, map[string]any{"command": command}, "permission_mode", permission), Env: with()})
	}
	for _, command := range []string{`echo x > "$(echo .claude/settings.json)"`, `echo x > $X`, `echo x >> "${HOME}/a"`, `printf x 2> "$LOG"`} {
		if a := run(command, "default"); a.Record["decision"] != "ask" || a.Record["degraded"] != "expanded-redirect-target" ||
			!strings.Contains(a.Stdout, "リダイレクトの書き込み先が変数か $( )") {
			t.Errorf("%s: %v %s", command, a.Record, a.Stdout)
		}
		// 確認に答える人のいない権限モードでは止める。
		if a := run(command, "bypassPermissions"); a.Record["decision"] != "deny" {
			t.Errorf("%s (bypassPermissions): %v", command, a.Record)
		}
	}
	for _, command := range []string{`echo x > out.txt 2>&1`, `cat < $X`, `echo $X > out.txt`} {
		if a := run(command, "default"); a.Record["decision"] != "allow" {
			t.Errorf("%s: %v %s", command, a.Record, a.Stdout)
		}
	}
}

// 書き込みのツールで書き込み先の欄を読めないときは、判定を飛ばさずに止める（飛ばすと設定ファイルも保護領域も素通りする）。
func TestUnreadWriteTargetIsDenied(t *testing.T) {
	root := fixtureWorkspace(t, "")
	settings := root + "/.claude/settings.json"
	for _, c := range []struct {
		tool  string
		input map[string]any
		deny  bool
	}{
		{config.ToolWrite, map[string]any{"path": settings, "content": "x"}, true},
		{config.ToolEdit, map[string]any{"path": settings, "old_string": "a", "new_string": "b"}, true},
		{"WriteFile", map[string]any{"file_path": settings, "content": "x"}, true},
		{"mcp__fs__write_file", map[string]any{"path": settings, "content": "x"}, true},
		{"mcp__fs__editFile", map[string]any{"filePath": settings}, true},
		// 書き込みの名前でも書き込み先らしい欄が無い・読むだけの名前・Claude Code の対象を持たないツールは、これまでどおり飛ばす。
		{"mcp__github__create_issue", map[string]any{"title": "x", "body": "y"}, false},
		{"mcp__fs__read_file", map[string]any{"path": settings}, false},
		{"mcp__file-editor__list_dir", map[string]any{"path": root}, false},
		{"TodoWrite", map[string]any{"todos": []any{}}, false},
		{config.ToolWebSearch, map[string]any{"query": "x"}, false},
	} {
		a := runGo(t, root, judgeCase{Payload: call(c.tool, root, c.input), Env: with()})
		if c.deny && (a.Record["decision"] != "deny" || a.Record["code"] != reasons.CodeWriteTargetUnread || !strings.Contains(a.Stdout, c.tool+" は書き込みのツール")) {
			t.Errorf("%s %v: %v %s", c.tool, c.input, a.Record, a.Stdout)
		}
		if !c.deny && (a.Record["decision"] != "skip" || a.Record["reason"] != "no-subject") {
			t.Errorf("%s %v: %v", c.tool, c.input, a.Record)
		}
	}
}

// ルールを当てるシェルのコマンドが上限より長ければ、照合せずに止める（照合が hook の期限を越えると判定なしで通るため）。
// ヒアドキュメントの本文は照合しないので数えない。
func TestTooLongCommandIsDenied(t *testing.T) {
	root := fixtureWorkspace(t, "")
	run := func(command string) judgeAnswer {
		return runGo(t, root, judgeCase{Payload: call(config.ToolBash, root, map[string]any{"command": command}), Env: with()})
	}
	for _, command := range []string{
		strings.Repeat("ls | ", 4000) + "ls",
		`echo "` + strings.Repeat("a|b ", 5000) + `"`,
	} {
		a := run(command)
		if a.Record["decision"] != "deny" || a.Record["code"] != reasons.CodeCommandTooLong || !strings.Contains(a.Stdout, "上限の 4000 文字") {
			t.Errorf("%.20q: %v %.300s", command, a.Record, a.Stdout)
		}
	}
	if a := run("echo " + strings.Repeat("a", CommandLengthLimit-5)); a.Record["decision"] != "allow" {
		t.Errorf("上限ちょうど: %v", a.Record)
	}
	// 本文の長いヒアドキュメント（受入テストのルールは << を deny するので、上限の止めでないことだけを見る）。
	if a := run("cat <<'EOF' > notes.txt\n" + strings.Repeat("a|b ", 5000) + "\nEOF\n"); a.Record["code"] == reasons.CodeCommandTooLong {
		t.Errorf("ヒアドキュメントの本文を数えた: %v", a.Record)
	}
}
