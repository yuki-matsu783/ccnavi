package rules

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// hook が読めない payload を受けたときの受入テスト。終了コード 1 で終わると、Claude Code は PreToolUse の呼び出しを
// そのまま通す。入口で受けて、イベントごとに決めた形で終えることを確かめる。
//
// ccnavi 自身の失敗（内部エラー）の受け皿と、判定に達しなかった回をツールの種類で通すか止めるかは、外から失敗を
// 起こせないので Go の単体テスト（entry/cli の internal_error_test.go と hook/failsafe）が確かめる。

// raw は root に生の本文を 1 回渡す（コアファイルの自己防衛は切る）。
func raw(t testing.TB, root, mode string, body any) run.Result {
	t.Helper()
	return fire(t, root, []string{"--mode", mode}, body, guardOff)
}

func preBody() payload { return preBash("echo hi") }

func with(from payload, extra payload) payload {
	out := payload{}
	for k, v := range from {
		out[k] = v
	}
	for k, v := range extra {
		out[k] = v
	}
	return out
}

func TestALoneSurrogateInTheCommandIsJudgedAndRecorded(t *testing.T) {
	t.Parallel()
	// JSON の "\ud800"（UTF-8 にできない字）がコマンドに入っても、判定は効き、記録も残る。記録には代わりに U+FFFD が残る。
	root := base.TempDir(t)
	text := `{"hook_event_name":"PreToolUse","session_id":"s1","tool_name":"Bash",` +
		`"tool_input":{"command":"rm -rf /tmp/x \ud800"}}`
	r := raw(t, root, "enable", text)
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	if parse(t, r).Decision != "deny" {
		t.Errorf("deny のはず: %s", r.Stdout)
	}
	row := lastRecord(t, root)
	if str(row, "decision") != "deny" || !strings.Contains(str(row, "subject"), "rm -rf /tmp/x �") {
		t.Errorf("記録: %v", row)
	}
}

func TestAPayloadWithAFieldOfTheWrongTypeIsUnusable(t *testing.T) {
	t.Parallel()
	// イベント名・ツール名が文字列でない、tool_input がオブジェクトでない・読む欄が文字列でない payload は、
	// 読めない payload として止めて記録する（空として通さない）。
	for name, body := range map[string]payload{
		"event name":     with(preBody(), payload{"hook_event_name": []string{"PreToolUse"}}),
		"tool name":      with(preBody(), payload{"tool_name": []string{"Bash"}}),
		"input is array": with(preBody(), payload{"tool_input": []string{"git", "push"}}),
		"command array":  with(preBody(), payload{"tool_input": payload{"command": []string{"git", "push"}}}),
		"path number":    with(preBody(), payload{"tool_input": payload{"file_path": 1}}),
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			root := base.TempDir(t)
			r := raw(t, root, "enable", body)
			if r.Code != 2 {
				t.Errorf("終了コード = %d, 2 のはず: %s", r.Code, r.Stderr)
			}
			mustHave(t, r.Stderr, "[ccnavi] payload-unusable")
			row := lastRecord(t, root)
			if str(row, "reason") != "payload-unusable" || str(row, "decision") != "skip" {
				t.Errorf("記録: %v", row)
			}
		})
	}
}

func TestDeeplyNestedClaudeSettingsAreSkipped(t *testing.T) {
	t.Parallel()
	// 入れ子の深すぎる settings.local.json は読めないファイルとして飛ばし、判定は続ける。
	root := base.TempDir(t)
	write(t, root+"/.claude/settings.local.json", strings.Repeat("[", 200000))
	r := raw(t, root, "enable", with(preBody(), payload{"tool_input": payload{"command": "rm -rf /tmp/x"}}))
	if r.Code != 0 || parse(t, r).Decision != "deny" {
		t.Errorf("deny を返して 0 で終わるはず: code=%d %s %s", r.Code, r.Stdout, r.Stderr)
	}
}

func TestAnUnusablePayloadSaysWhatToDo(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	r := raw(t, root, "enable", "not json")
	if r.Code != 2 {
		t.Errorf("終了コード = %d, 2 のはず", r.Code)
	}
	mustHave(t, r.Stderr, "[ccnavi] payload-unusable", "exit code 2 で止まりました")
	// イベント名すら読めない回は、何を止めたかを言わず、直す側だけを名指しする。
	mustNotHave(t, r.Stderr, "この呼び出し")
}

func TestTheUnusablePayloadTextFollowsTheEvent(t *testing.T) {
	t.Parallel()
	// exit 2 が何を止め、文が誰に届くかはイベントで違う。文をそれに合わせる。
	for event, c := range map[string]struct{ what, then string }{
		"PreToolUse":       {"判定できないので止めました", "確かめてもらい"},
		"PostToolUse":      {"すでに実行済み", "確かめてもらい"},
		"UserPromptSubmit": {"Claude に渡す前に止めました", "送り直しても通らない"},
		"Stop":             {"終わらせませんでした", "ターンを終える"},
		"SubagentStop":     {"終わらせませんでした", "最終報告"},
	} {
		t.Run(event, func(t *testing.T) {
			t.Parallel()
			root := base.TempDir(t)
			r := raw(t, root, "enable", payload{"hook_event_name": event, "session_id": 1})
			if r.Code != 2 {
				t.Errorf("終了コード = %d, 2 のはず: %s", r.Code, r.Stderr)
			}
			mustHave(t, r.Stderr, c.what, c.then)
			if event != "PreToolUse" {
				mustNotHave(t, r.Stderr, "この呼び出しを判定できない")
			}
			row := lastRecord(t, root)
			if str(row, "reason") != "payload-unusable" || str(row, "event") != event {
				t.Errorf("記録: %v", row)
			}
		})
	}
}

func TestAnUnusablePayloadInDryRunDoesNotSayStopped(t *testing.T) {
	t.Parallel()
	// ツールの呼び出しを持たないイベントは、dry-run なら exit 0 で通す。「止めた」とは言わず、enable なら止めていたと言う。
	for _, event := range []string{"UserPromptSubmit", "Stop"} {
		t.Run(event, func(t *testing.T) {
			t.Parallel()
			r := raw(t, base.TempDir(t), "dry-run", payload{"hook_event_name": event, "cwd": 1})
			if r.Code != 0 {
				t.Errorf("終了コード = %d, 0 のはず: %s", r.Code, r.Stderr)
			}
			mustHave(t, r.Stderr, "[ccnavi] payload-unusable", "何も止めていません")
			mustNotHave(t, r.Stderr, "止めました")
		})
	}
}

func TestAnUnusablePayloadBeforeAToolCallStopsEvenInDryRun(t *testing.T) {
	t.Parallel()
	// ツールが分からない呼び出しの前は、dry-run でも止める。
	for _, event := range []string{"PreToolUse", ""} {
		t.Run("event="+event, func(t *testing.T) {
			t.Parallel()
			r := raw(t, base.TempDir(t), "dry-run", payload{"hook_event_name": event, "cwd": 1})
			if r.Code != 2 {
				t.Errorf("終了コード = %d, 2 のはず: %s", r.Code, r.Stderr)
			}
			mustHave(t, r.Stderr, "[ccnavi] payload-unusable")
			mustNotHave(t, r.Stderr, "何も止めていません")
		})
	}
}
