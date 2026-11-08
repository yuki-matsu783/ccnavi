package hookio

import (
	"errors"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
)

func decodeText(t *testing.T, text string) (Input, error) {
	t.Helper()
	return Decode(strings.NewReader(text))
}

// 解釈できない payload は、決めた文とイベント名で止める。
func TestDecodeRefusals(t *testing.T) {
	cases := []struct {
		in, msg string
		event   Event
	}{
		{"x", "payload を解釈できない: Expecting value: line 1 column 1 (char 0)", ""},
		{"[]", "payload がオブジェクトではない", ""},
		{`{"hook_event_name": 1}`, "payload の hook_event_name が文字列ではない", ""},
		{`{"hook_event_name": 0}`, "payload に hook_event_name がありません。", ""},
		{`{}`, "payload に hook_event_name がありません。", ""},
		{`{"hook_event_name": "PreToolUse", "tool_name": 3}`, "payload の tool_name が文字列ではない", PreToolUse},
		{`{"hook_event_name": "PreToolUse", "tool_input": []}`, "payload の tool_input がオブジェクトではない", PreToolUse},
		// 判定が読む欄が文字列でなければ、無い欄として通さずに止める。
		{`{"hook_event_name": "PreToolUse", "tool_input": {"command": ["git", "push"]}}`,
			"payload の tool_input.command が文字列ではない", PreToolUse},
	}
	for _, c := range cases {
		_, err := decodeText(t, c.in)
		var u *UnusableError
		if !errors.As(err, &u) || u.Msg != c.msg || u.Event != c.event {
			t.Errorf("Decode(%s) = %v, want %q (%q)", c.in, err, c.msg, c.event)
		}
	}
	for _, in := range []string{"", "  \n"} {
		if _, err := decodeText(t, in); !errors.Is(err, ErrNoPayload) {
			t.Errorf("Decode(%q) = %v", in, err)
		}
	}
}

// 残りの欄は型が違えば既定の値にし、文字列にする欄は偽の値（null・空）を空文字にする。
func TestDecodeFields(t *testing.T) {
	in, err := decodeText(t, `{"hook_event_name": "Stop", "agent_id": 5, "agent_type": true, "tools": ["Read", 1],
		"error": 3, "is_interrupt": 1, "stop_hook_active": true, "source": {"a": [1, null]}, "prompt": 7,
		"transcript_path": "/t", "session_crons": [1], "tool_response": "x", "cwd": null, "tool_input": null}`)
	if err != nil {
		t.Fatal(err)
	}
	if in.AgentID != "5" || in.AgentType != "True" || in.Tools != nil || in.error != "" || in.IsInterrupt ||
		!in.StopHookActive || in.Source != "{'a': [1, None]}" || in.Prompt != "" || in.TranscriptPath != "/t" ||
		!in.Busy || in.ToolResponse.Len() != 0 || in.ToolInput.Len() != 0 || in.Cwd != "" {
		t.Errorf("%+v", in)
	}
	in, err = decodeText(t, `{"hook_event_name": "PreToolUse", "tools": ["Read", "Bash"],
		"tool_input": {"z": 1, "command": "ls"}, "background_tasks": []}`)
	if err != nil {
		t.Fatal(err)
	}
	if len(in.Tools) != 2 || in.Busy || in.Field("command") != "ls" || in.Field("z") != "" ||
		strings.Join(in.ToolInput.Keys(), ",") != "z,command" {
		t.Errorf("%+v", in)
	}
}

// 標準入力の UTF-8 として読めないバイトは、正しく始まった並びごとに 1 つの U+FFFD にする。
func TestDecodeReplacesBrokenUTF8(t *testing.T) {
	if got := decodeReplace([]byte("a\xff\xe3\x81\x82\xed\xa0\x80\xf0\x9f\x98")); got != "a�あ����" {
		t.Errorf("got %q", got)
	}
}

func prompt(t *testing.T, text string, extra string) Input {
	t.Helper()
	body, _ := pyjson.Dumps(pyjson.NewObject("hook_event_name", "UserPromptSubmit", "session_id", "s1", "prompt", text), pyjson.Options{})
	if extra != "" {
		body = strings.TrimSuffix(body, "}") + ", " + extra + "}"
	}
	in, err := decodeText(t, body)
	if err != nil {
		t.Fatal(err)
	}
	return in
}

// ユーザの発言と言い切れるものだけを数える。迷うもの（完了通知・起こす指示・封筒・空・サブエージェント・
// ほかのイベント）は数えない。予約した実行の prompt は見分けられないので数える側に残る。
func TestIsUserPrompt(t *testing.T) {
	cases := []struct {
		text, extra string
		want        bool
	}{
		{"README.md の末尾に 1 行足してください", "", true},
		{"  先頭に空白のある文", "", true},
		{"5 分ごとにデプロイを見て", "", true},
		{"task-notification と打ってみました", "", true},
		{"wake の封筒の形について教えて", "", true},
		{"説明: <wake reason=...> は何?", "", true},
		{"続けてください", `"background_tasks": [{"id": "b1"}]`, true},
		{"<task-notification>\n<status>completed</status>\n</task-notification>", "", false},
		{"経過です。<task-notification>", "", false},
		{"<task-notification> と打ってみました", "", false},
		{"[idle-handoff] 引き継ぎを書いて", "", false},
		{"  [IDLE-HANDOFF] 大文字でも", "", false},
		{`<wake reason="external-event"></wake>`, "", false},
		{"<webhook-payload>{}</webhook-payload>", "", false},
		{"<child-session-event>failed</child-session-event>", "", false},
		{`<event source="github">x</event>`, "", false},
		{"[SYSTEM NOTIFICATION] 通知です", "", false},
		{"<system-reminder>x</system-reminder>", "", false},
		{"", "", false},
		{"   ", "", false},
		{"\n", "", false},
		{"お願いします", `"agent_id": "sub1"`, false},
		{"お願いします", `"hook_event_name": "PreToolUse"`, false},
		{"お願いします", `"hook_event_name": "Stop"`, false},
	}
	for _, c := range cases {
		if got := IsUserPrompt(prompt(t, c.text, c.extra)); got != c.want {
			t.Errorf("IsUserPrompt(%q, %s) = %v", c.text, c.extra, got)
		}
	}
	// 完了通知の見分け（ターンの印と変更の受け入れが使う）は、目印の先頭だけを見る判定とは別。
	if !prompt(t, "<task-notification>x", "").FromBackgroundTask() || prompt(t, "[idle-handoff] x", "").FromBackgroundTask() {
		t.Error("FromBackgroundTask")
	}
}

// 応答は決まったバイト列（ensure_ascii=False、区切り ", " と ": "、空の欄は出さない）。
func TestWriters(t *testing.T) {
	var b strings.Builder
	must := func(err error) {
		if err != nil {
			t.Fatal(err)
		}
	}
	must(WritePreToolUse(&b, PreToolUseResponse{Decision: Deny, Reason: "理由\"", Context: "文"}))
	must(WritePreToolUse(&b, PreToolUseResponse{Context: "文", UpdatedInput: pyjson.NewObject("prompt", "p", "b", []any{1.0})}))
	must(WritePreToolUse(&b, PreToolUseResponse{Decision: Ask, Reason: "r", UpdatedInput: pyjson.NewObject("prompt", "p")}))
	must(WritePreToolUse(&b, PreToolUseResponse{}))
	must(WriteContext(&b, SessionStart, "a\nb", "</>"))
	must(WriteContext(&b, PostToolUse, "x", ""))
	must(WriteSystemMessage(&b, "😀\t"))
	must(WriteStopBlock(&b, "続けて", "報告"))
	must(WriteStopBlock(&b, "r", ""))
	want := "{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"permissionDecision\": \"deny\", \"permissionDecisionReason\": \"理由\\\"\", \"additionalContext\": \"文\"}}\n" +
		"{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"additionalContext\": \"文\", \"updatedInput\": {\"prompt\": \"p\", \"b\": [1.0]}}}\n" +
		"{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"permissionDecision\": \"ask\", \"permissionDecisionReason\": \"r\", \"updatedInput\": {\"prompt\": \"p\"}}}\n" +
		"{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\"}}\n" +
		"{\"hookSpecificOutput\": {\"hookEventName\": \"SessionStart\", \"additionalContext\": \"a\\nb\"}, \"systemMessage\": \"</>\"}\n" +
		"{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", \"additionalContext\": \"x\"}}\n" +
		"{\"systemMessage\": \"😀\\t\"}\n" +
		"{\"decision\": \"block\", \"reason\": \"続けて\", \"systemMessage\": \"報告\"}\n" +
		"{\"decision\": \"block\", \"reason\": \"r\"}\n"
	if b.String() != want {
		t.Errorf("got\n%s\nwant\n%s", b.String(), want)
	}
}
