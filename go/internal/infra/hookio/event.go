package hookio

import "slices"

// Event は hook のイベント名。payload に自分のイベント名が入るので、登録を間違えても気づかないうちに
// 別の判定が走ることはない。
type Event string

const (
	PreToolUse  Event = "PreToolUse"
	PostToolUse Event = "PostToolUse"
	// PostToolUseFailure はツール自体が失敗した後に来る。止められないイベントで、返せるのは文だけ。
	PostToolUseFailure Event = "PostToolUseFailure"
	SessionStart       Event = "SessionStart"
	UserPromptSubmit   Event = "UserPromptSubmit"
	Stop               Event = "Stop"
	// SubagentStart は止められないイベントで、返せるのは文だけ。
	SubagentStart Event = "SubagentStart"
	// SubagentStop は Stop と同じく、exit 2 が「続けさせる」を意味する。
	SubagentStop Event = "SubagentStop"
)

// StartSource は SessionStart の source（なぜセッションが始まったか）。
type StartSource string

const (
	StartStartup StartSource = "startup"
	StartResume  StartSource = "resume"
	startClear   StartSource = "clear"
	startCompact StartSource = "compact"
)

// Decision は 1 回の呼び出しに対する判定。緩い順に ask・allow・deny。
type Decision string

const (
	Ask   Decision = "ask"
	Allow Decision = "allow"
	Deny  Decision = "deny"
)

// taskNotification は、バックグラウンド作業の完了通知が UserPromptSubmit の prompt として届くときの目印。
const taskNotification = "<task-notification>"

// notUserStarts は、ユーザが打ったのではない UserPromptSubmit の prompt の先頭に付く目印（小文字で比べる）。
// IdleHandoffMarker は ccnavi 自身が放置したセッションを起こす指示。残りは、外部の出来事がセッションを起こす封筒の形。
var notUserStarts = []string{
	IdleHandoffMarker,
	"<wake",
	"<webhook-payload",
	"<child-session-event",
	"<event ",
	"[system notification",
	"<system-reminder",
}

// toolInputTextFields は、tool_input のうち判定が文字列として読む欄。ここが文字列でない payload は
// 読まずに止める。空として読むと {"command": ["git", "push"]} が「ヒットするルールが無い」で通る。
var toolInputTextFields = []string{
	"command", "file_path", "path", "notebook_path", "skill", "description",
	"url", "prompt", "pattern", "content", "old_string", "new_string",
}

// ToolInputTextFields は toolInputTextFields の写しを返す（呼び手が書き換えても元は変わらない）。
func ToolInputTextFields() []string { return slices.Clone(toolInputTextFields) }
