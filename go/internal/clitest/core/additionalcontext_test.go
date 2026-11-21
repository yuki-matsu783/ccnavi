package core

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// ルールの `additionalContext` の受入テスト。道具を外から呼んで応答だけを見る。
//
//  1. allow にヒットしたルールの文が additionalContext でモデルに渡る
//  2. deny と ask では、理由（permissionDecisionReason）と一緒に additionalContext が付く（両方が届くことは Claude Code 2.1 で確かめた）
//  3. dry-run でも文は届く（判定の代わりの文に続く）
//  4. 書いていないルールではキーごと出ない
//  5. `--lint` は広い allow（何にでもヒットする、選択肢が 3 つ以上）に書いた文を warn にし、狭い allow には何も言わない。`--test` は理由と文の両方を見せる

// maxChars は additionalContextFile の本文を渡す上限の文字数（go/internal/policy/ctxfile の MaxChars）。
const maxChars = 4000

const contextNote = "ワークツリーの中だけで直すこと。main には触らない。"

// ctxRule は message 付きのルール。extra は欄の名前と値の組。
func ctxRule(name, match, message string, extra ...any) rule {
	return with(rule{"id": name, "match": match, "message": message}, extra...)
}

func TestContextAllowCarriesTheTextAsAdditionalContext(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny":  []rule{ctxRule("push", "Bash", "文面", "glob", "*git push*")},
		"allow": []rule{ctxRule("src", "Write", "", "glob", "*/src/*", "additionalContext", contextNote)},
	})
	out := judgeTool(t, root, "Write", filepath.Join(root, "src", "a.py"))
	if _, has := out["permissionDecision"]; has || str(out, "additionalContext") != contextNote {
		t.Errorf("out = %v", out)
	}
}

func TestContextDenyAndAskCarryReasonAndContextTogether(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny":  []rule{ctxRule("push", "Bash", "push はユーザが行う", "glob", "*git push*", "additionalContext", contextNote)},
		"ask":   []rule{ctxRule("mig", "Write", "移行はユーザが見る", "glob", "*/migrations/*", "additionalContext", contextNote)},
		"allow": []rule{ctxRule("src", "Write", "", "glob", "*/src/*")},
	})
	denied := judgeTool(t, root, "Bash", "git push origin main")
	if str(denied, "permissionDecision") != "deny" || str(denied, "additionalContext") != contextNote {
		t.Errorf("denied = %v", denied)
	}
	mustContain(t, "deny の理由", str(denied, "permissionDecisionReason"), "push はユーザが行う")
	asked := judgeTool(t, root, "Write", filepath.Join(root, "migrations", "001.sql"))
	if str(asked, "permissionDecision") != "ask" || str(asked, "additionalContext") != contextNote {
		t.Errorf("asked = %v", asked)
	}
	mustContain(t, "ask の理由", str(asked, "permissionDecisionReason"), "移行はユーザが見る")
}

func TestContextDryRunStillDeliversTheText(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny":  []rule{ctxRule("deploy", "Bash", "deploy はユーザが行う", "glob", "*make deploy*", "additionalContext", contextNote)},
		"allow": []rule{ctxRule("src", "Write", "", "glob", "*/src/*")},
	})
	out := judgeTool(t, root, "Bash", "make deploy prod", "--mode", "dry-run")
	if _, has := out["permissionDecision"]; has {
		t.Errorf("dry-run が止めた: %v", out)
	}
	mustContain(t, "additionalContext", str(out, "additionalContext"), "would have stopped", contextNote)
}

func TestContextRulesWithoutTheFieldAddNothing(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny":  []rule{ctxRule("push", "Bash", "push はユーザが行う", "glob", "*git push*")},
		"allow": []rule{ctxRule("src", "Write", "", "glob", "*/src/*")},
	})
	if _, has := judgeTool(t, root, "Bash", "git push")["additionalContext"]; has {
		t.Error("書いていないのに additionalContext が出た")
	}
	if got := judgeTool(t, root, "Write", filepath.Join(root, "src", "a.py")); len(got) != 0 {
		t.Errorf("out = %v", got)
	}
}

// 広い allow（何にでもヒットする、選択肢が 3 つ以上）に書いた文は warn、狭い allow には何も言わない。
func TestContextLintWarnsOnBroadAllowOnly(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny": []rule{ctxRule("push", "Bash", "文面", "glob", "*git push*")},
		"allow": []rule{
			ctxRule("everything", "Read", "", "glob", "*", "additionalContext", contextNote),
			ctxRule("many", "Bash", "", "regex", `\b(ls|cat|sed)\b`, "additionalContext", contextNote),
			ctxRule("two", "Bash", "", "regex", `\b(ls|cat)\b`, "additionalContext", contextNote),
			ctxRule("narrow", "Write", "", "glob", "*/src/*", "additionalContext", contextNote),
			ctxRule("class", "Bash", "", "regex", `^[a|b|c]x\b`, "additionalContext", contextNote),
		},
	})
	done := ccnavi(t, root, nil, "--lint", "--mode", "enable")
	if done.Code != 0 {
		t.Errorf("code = %d: %s", done.Code, done.Stdout)
	}
	var warned []string
	for _, line := range strings.Split(done.Stdout, "\n") {
		if strings.Contains(line, "additionalContext") {
			warned = append(warned, line)
		}
	}
	if len(warned) != 2 || !anyLine(warned, "", "everything", "何にでもヒットする") || !anyLine(warned, "", "many", "選択肢が 3 つ以上") {
		t.Errorf("warned = %v\n%s", warned, done.Stdout)
	}
}

// once は文脈ごとに 1 回目だけ届く。別のセッションと同じセッションのサブエージェントは別の文脈。
// セッションの開始（compact の後も含む）で忘れる。試験は記録を消費しない。
func TestContextOnceDeliversOnlyTheFirstTimePerContext(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny":  []rule{ctxRule("push", "Bash", "文面", "glob", "*git push*")},
		"allow": []rule{ctxRule("src", "Write", "", "glob", "*/src/*", "additionalContextOnce", contextNote)},
	})
	target := filepath.Join(root, "src", "a.py")
	hit := func(session, agent, event string) map[string]any {
		return judge(t, root, map[string]any{
			"hook_event_name": event, "tool_name": "Write", "session_id": session, "agent_id": agent,
			"tool_input": map[string]any{"file_path": target},
		})
	}
	delivered := func(m map[string]any) bool { return str(m, "additionalContext") == contextNote }
	if !delivered(hit("s1", "", "PreToolUse")) {
		t.Error("1 回目が届かない")
	}
	if got := hit("s1", "", "PreToolUse"); len(got) != 0 {
		t.Errorf("2 回目が届いた: %v", got)
	}
	if !delivered(hit("s2", "", "PreToolUse")) {
		t.Error("別のセッションは別の文脈")
	}
	if !delivered(hit("s1", "a1", "PreToolUse")) {
		t.Error("サブエージェントは別の文脈")
	}
	if got := hit("s1", "a1", "PreToolUse"); len(got) != 0 {
		t.Errorf("サブエージェントの 2 回目が届いた: %v", got)
	}
	hit("s1", "", "SessionStart")
	if !delivered(hit("s1", "", "PreToolUse")) {
		t.Error("セッションの開始で忘れていない")
	}
	ccnavi(t, root, nil, "--state", filepath.Join(root, "logs", "state"), "--test", "Write", target)
	if !delivered(hit("s3", "", "PreToolUse")) {
		t.Error("試験が記録を消費した")
	}
}

// 記録の hinted の欄は、once の案内を渡した 1 回目にだけ付く。2 回目と、毎回渡す案内だけの回には付かない。
func TestContextRecordMarksOnlyTheCallThatDeliveredTheOnceText(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{"allow": []rule{
		ctxRule("src", "Write", "", "glob", "*/src/*", "additionalContextOnce", contextNote),
		ctxRule("doc", "Write", "", "glob", "*/doc/*", "additionalContext", contextNote),
	}})
	for _, parts := range [][]string{{"src", "a.py"}, {"src", "b.py"}, {"doc", "a.md"}, {"doc", "b.md"}} {
		judgeTool(t, root, "Write", filepath.Join(append([]string{root}, parts...)...))
	}
	f, err := os.Open(filepath.Join(root, "logs", "decisions.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var hinted []any
	scan := bufio.NewScanner(f)
	for scan.Scan() {
		if strings.TrimSpace(scan.Text()) == "" {
			continue
		}
		var row map[string]any
		if err := json.Unmarshal(scan.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		hinted = append(hinted, row["hinted"])
	}
	if len(hinted) != 4 || hinted[0] != true || hinted[1] != nil || hinted[2] != nil || hinted[3] != nil {
		t.Errorf("hinted = %v", hinted)
	}
}

func TestContextTestShowsReasonAndContext(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny":  []rule{ctxRule("push", "Bash", "push はユーザが行う", "glob", "*git push*", "additionalContext", contextNote)},
		"allow": []rule{ctxRule("src", "Write", "", "glob", "*/src/*", "additionalContext", contextNote)},
	})
	body := decode(t, ccnavi(t, root, nil, "--test", "Bash", "git push", "--json").Stdout)
	mustContain(t, "response", str(body, "response"), "push はユーザが行う", contextNote)
	allowed := ccnavi(t, root, nil, "--test", "Write", filepath.Join(root, "src", "a.py"))
	mustContain(t, "allow の試験", allowed.Stdout, "verdict: allow", contextNote)
}

// 本文は文のあとに続き、上限で切る。無いファイルは何も足さない。試験にも本文が出る。
func TestContextFileBodyFollowsTheTextAndIsCutAtTheLimit(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	writeFile(t, root, "docs/guide.md", "# 決まり\n\nテストは tests/ に置く。\n")
	writeFile(t, root, "docs/long.md", strings.Repeat("あ", maxChars+50))
	putCommonRules(t, root, map[string]any{
		"deny": []rule{ctxRule("push", "Bash", "文面", "glob", "*git push*")},
		"allow": []rule{
			ctxRule("src", "Write", "", "glob", "*/src/*", "additionalContext", contextNote, "additionalContextFile", "docs/guide.md"),
			ctxRule("doc", "Write", "", "glob", "*/docs/*", "additionalContextFile", "docs/long.md"),
			ctxRule("none", "Write", "", "glob", "*/etc/*", "additionalContextFile", "docs/none.md"),
		},
	})
	got := judgeTool(t, root, "Write", filepath.Join(root, "src", "a.py"))
	if want := contextNote + "\n\n# 決まり\n\nテストは tests/ に置く。"; str(got, "additionalContext") != want {
		t.Errorf("additionalContext = %q, want %q", str(got, "additionalContext"), want)
	}
	cut := str(judgeTool(t, root, "Write", filepath.Join(root, "docs", "x.md")), "additionalContext")
	if !strings.HasPrefix(cut, strings.Repeat("あ", maxChars)+"\n\n(ccnavi: docs/long.md は") || strings.Contains(cut, strings.Repeat("あ", maxChars+1)) {
		t.Errorf("上限で切れていない (%d 字)", len([]rune(cut)))
	}
	mustContain(t, "切った案内", cut, "続きはこのファイルを読んで")
	if got := judgeTool(t, root, "Write", filepath.Join(root, "etc", "a")); len(got) != 0 {
		t.Errorf("無いファイルが何か足した: %v", got)
	}
	shown := ccnavi(t, root, nil, "--test", "Write", filepath.Join(root, "src", "a.py"))
	mustContain(t, "試験", shown.Stdout, "テストは tests/ に置く。")
}

// ワークツリーの中の呼び出しは、ワークツリーの本文を読む。
func TestContextFileInTheWorktreeWinsOverTheRoot(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	writeFile(t, root, "docs/guide.md", "ルートの案内")
	wt := filepath.Join(root, ".claude", "worktrees", "feat")
	writeFile(t, wt, "docs/guide.md", "ワークツリーの案内")
	// 実際のワークツリーと見なされるには、.git ファイルと登録簿の相互参照が要る。
	gitdir := filepath.Join(root, ".git", "worktrees", "feat")
	writeFile(t, wt, ".git", "gitdir: "+gitdir+"\n")
	writeFile(t, gitdir, "gitdir", filepath.Join(wt, ".git")+"\n")
	putCommonRules(t, root, map[string]any{
		"deny":  []rule{ctxRule("push", "Bash", "文面", "glob", "*git push*")},
		"allow": []rule{ctxRule("src", "Write", "", "glob", "*/src/*", "additionalContextFile", "docs/guide.md")},
	})
	if got := str(judgeTool(t, root, "Write", filepath.Join(wt, "src", "a.py")), "additionalContext"); got != "ワークツリーの案内" {
		t.Errorf("ワークツリーの中 = %q", got)
	}
	if got := str(judgeTool(t, root, "Write", filepath.Join(root, "src", "a.py")), "additionalContext"); got != "ルートの案内" {
		t.Errorf("ルート = %q", got)
	}
}

// once の本文は 1 度だけ渡り、lint は本文のパスを調べる（無い・長い・`..`・絶対パス）。外を指す欄は実行時も読まない。
func TestContextOnceFileIsDeliveredOnceAndLintChecksThePath(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	writeFile(t, root, "docs/once.md", "最初に 1 度だけ")
	writeFile(t, root, "docs/long.md", strings.Repeat("い", maxChars+1))
	putCommonRules(t, root, map[string]any{
		"deny": []rule{ctxRule("push", "Bash", "文面", "glob", "*git push*")},
		"allow": []rule{
			ctxRule("src", "Write", "", "glob", "*/src/*", "additionalContextOnceFile", "docs/once.md"),
			ctxRule("gone", "Write", "", "glob", "*/a/*", "additionalContextFile", "docs/gone.md"),
			ctxRule("long", "Write", "", "glob", "*/b/*", "additionalContextFile", "docs/long.md"),
			ctxRule("up", "Write", "", "glob", "*/c/*", "additionalContextFile", "../secret.md"),
			ctxRule("abs", "Write", "", "glob", "*/d/*", "additionalContextOnceFile", "/etc/passwd"),
		},
	})
	payload := mustJSON(t, map[string]any{
		"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": "s1", "cwd": root,
		"tool_input": map[string]any{"file_path": filepath.Join(root, "src", "a.py")},
	})
	args := []string{"--root", root, "--mode", "enable"}
	first := run.Ccnavi(t, args, run.Opts{Input: payload, Dir: base.Root()})
	mustContain(t, "1 回目", first.Stdout, "最初に 1 度だけ")
	if second := run.Ccnavi(t, args, run.Opts{Input: payload, Dir: base.Root()}); strings.TrimSpace(second.Stdout) != "" {
		t.Errorf("2 回目が届いた: %s", second.Stdout)
	}

	lint := ccnavi(t, root, nil, "--lint").Stdout
	mustContain(t, "lint", lint,
		"warn: gone: additionalContextFile の docs/gone.md がありません。",
		"warn: long: additionalContextFile の docs/long.md は "+strconv.Itoa(maxChars)+" 文字",
		"error: up: additionalContextFile の ../secret.md: `..`",
		"error: abs: additionalContextOnceFile の /etc/passwd: 絶対パス")
	mustNotContain(t, "lint", lint, "src:")
	if got := judgeTool(t, root, "Write", filepath.Join(root, "c", "a")); len(got) != 0 {
		t.Errorf("外を指す欄を読んだ: %v", got)
	}
}

// 拒否文の `subject:` は、読み直す前のコマンドの原文を出す（引用符と空白を落とさない）。秘密の形は記録と同じく伏せる。
func TestReasonSubjectIsTheRawCommandWithSecretsMasked(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{"deny": []rule{ctxRule("redir", "Bash", "文面", "glob", "*>> a.yml*")}})
	reason := func(command string) string {
		out := judgeTool(t, root, "Bash", command)
		if str(out, "permissionDecision") != "deny" {
			t.Fatalf("deny でない: %v", out)
		}
		return str(out, "permissionDecisionReason")
	}
	command := `echo "# x" >> a.yml`
	mustContain(t, "拒否文（引用符と空白を保つ）", reason(command), "subject: "+command)
	secret := "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
	mustNotContain(t, "拒否文（秘密を伏せる）", reason("echo "+secret+" >> a.yml"), secret)
}
