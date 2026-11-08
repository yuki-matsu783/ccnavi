package hookio

import (
	"errors"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// payload の欄の名前。
const (
	keyEvent           = "hook_event_name"
	keyToolName        = "tool_name"
	keySessionID       = "session_id"
	keyCwd             = "cwd"
	keyPermissionMode  = "permission_mode"
	keyToolUseID       = "tool_use_id"
	keyToolInput       = "tool_input"
	keyToolResponse    = "tool_response"
	keyAgentID         = "agent_id"
	keyAgentType       = "agent_type"
	keyTools           = "tools"
	keyError           = "error"
	keyIsInterrupt     = "is_interrupt"
	keyStopHookActive  = "stop_hook_active"
	keySource          = "source"
	keyPrompt          = "prompt"
	keyTranscriptPath  = "transcript_path"
	keyBackgroundTasks = "background_tasks"
	keySessionCrons    = "session_crons"
)

// keyTextFields は判定のキーになる欄。あれば文字列でなければならない。無いものとして読むと、
// ツールの名前が分からないまま「ヒットするルールが無い」で通る。
var keyTextFields = []string{keyToolName, keySessionID, keyCwd, keyPermissionMode, keyToolUseID}

// ErrNoPayload は標準入力が空だったこと。hook ではなくユーザが直接打っている。
// 0 で終わると、設置を誤った hook が正常に動いている hook と見分けられなくなるので、別の種類にする。
var ErrNoPayload = errors.New("標準入力に hook の payload がありません。")

// UnusableError は payload が届いたが解釈できないこと。Event は読めたところまでのイベント名
// （読めなかったら空）。止めた文と exit 2 の意味はイベントごとに違うので、呼び手がそれに合わせる。
type UnusableError struct {
	Msg   string
	Event Event
	cause error
}

func (e *UnusableError) Error() string { return e.Msg }

func (e *UnusableError) Unwrap() error { return e.cause }

func unusable(event Event, format string, args ...any) error {
	return &UnusableError{Msg: fmt.Sprintf(format, args...), Event: event}
}

// Input は payload のうち判定が読む部分。知らない欄は無視する（上流に欄が 1 つ増えただけで
// 判定が動かなくならないように）。
type Input struct {
	Event          Event
	ToolName       string
	ToolInput      *pyjson.Object
	SessionID      string
	Cwd            string
	PermissionMode string
	ToolUseID      string
	// AgentID はサブエージェントの中で走った呼び出しにだけ付く。判定のキーには使わない。
	AgentID   string
	AgentType string
	// Tools は SubagentStart の tools（来ているかは実機で確かめていない）。文字列の列でなければ nil。
	Tools []string
	// ToolResponse は PostToolUse にだけ来る。Agent の起動の後には agentId が入る。
	ToolResponse *pyjson.Object
	// error と IsInterrupt は PostToolUseFailure にだけ来る。error は判定にも記録にも使わない。
	error       string
	IsInterrupt bool
	// StopHookActive は Stop・SubagentStop にだけ来る。真なら Stop の hook が続けさせた結果（連鎖の 2 回目以降）。
	StopHookActive bool
	// Source は SessionStart にだけ来る。
	Source StartSource
	// Prompt は UserPromptSubmit にだけ来るユーザの依頼文。記録にも診断ログにも書かない。
	Prompt string
	// TranscriptPath は会話の記録（JSONL）の場所。記録にも診断ログにも書かない。
	TranscriptPath string
	// Busy は Stop で、バックグラウンド作業か予約した繰り返しが残っているとき真。
	// そのターンの終わりのあとにも、エージェントの手がワークツリーに届きうる。
	Busy bool
}

// FromBackgroundTask は、この UserPromptSubmit がユーザの発言ではなくバックグラウンド作業の完了通知か。
// 完了通知の prompt は <task-notification> を含む。ユーザの手による変更を受け入れる処理は、
// この通知では行わない（バックグラウンドのプロセスが書いた変更をユーザの変更として受け入れてしまう）。
func (in Input) FromBackgroundTask() bool {
	return strings.Contains(in.Prompt, taskNotification)
}

// Field は tool_input の欄の文字列（"command" や "file_path" など）。文字列でなければ空。
func (in Input) Field(name string) string {
	return in.ToolInput.String(name)
}

// IsUserPrompt は、この UserPromptSubmit がユーザの発言と言い切れるか。ユーザの返事を数えるもの
// （ターン分離のゲート）だけが使い、迷うものは数えない（数えると、何も打たなくてもゲートが開く）。
//
// 数えないもの: UserPromptSubmit でない、サブエージェントの中、prompt が空、完了通知を含む
// （ユーザがその文字列を打っても数えない。安全側の非対称）、先頭が notUserStarts の目印
// （大文字小文字は区別しない）。予約した実行の prompt は打った文と見分けられないので、数える側に残る。
func IsUserPrompt(in Input) bool {
	if in.Event != UserPromptSubmit || in.AgentID != "" {
		return false
	}
	text := ospath.Strip(in.Prompt)
	if text == "" || in.FromBackgroundTask() {
		return false
	}
	lower := pyfmt.Lower(text)
	for _, mark := range notUserStarts {
		if strings.HasPrefix(lower, mark) {
			return false
		}
	}
	return true
}

// Decode は payload を 1 件読む。空なら ErrNoPayload、解釈できなければ *UnusableError。
// 判定のキーになる欄と tool_input の文字列の欄は型を確かめて止め、残りの欄は型が違えば既定の値
// （空の文字列・空のオブジェクト・偽）にする。
func Decode(r io.Reader) (Input, error) {
	// 1. 読めないバイトを U+FFFD に替えて文字列にし、空なら payload なしとして返す。
	data, err := io.ReadAll(r)
	if err != nil {
		return Input{}, err
	}
	raw := decodeReplace(data)
	if ospath.Strip(raw) == "" {
		return Input{}, ErrNoPayload
	}
	doc, err := pyjson.Loads(raw)
	if err != nil {
		return Input{}, &UnusableError{Msg: "payload を解釈できない: " + err.Error(), cause: err}
	}
	obj, ok := doc.(*pyjson.Object)
	if !ok {
		return Input{}, unusable("", "payload がオブジェクトではない")
	}
	// 2. イベント名と文字列の欄は、型が違えば止める側に倒す（判定のキーを取り違えて通さないため）。
	var event Event
	switch v := obj.Value(keyEvent).(type) {
	case string:
		event = Event(v)
	default:
		if pyfmt.Truthy(v) {
			return Input{}, unusable("", "payload の %s が文字列ではない", keyEvent)
		}
	}
	if event == "" {
		return Input{}, unusable("", "payload に %s がありません。", keyEvent)
	}
	for _, name := range keyTextFields {
		if v := obj.Value(name); v != nil {
			if _, ok := v.(string); !ok {
				return Input{}, unusable(event, "payload の %s が文字列ではない", name)
			}
		}
	}
	toolInput := &pyjson.Object{}
	if v := obj.Value(keyToolInput); v != nil {
		o, ok := v.(*pyjson.Object)
		if !ok {
			return Input{}, unusable(event, "payload の %s がオブジェクトではない", keyToolInput)
		}
		toolInput = o
	}
	for _, name := range toolInputTextFields {
		if v := toolInput.Value(name); v != nil {
			if _, ok := v.(string); !ok {
				return Input{}, unusable(event, "payload の %s.%s が文字列ではない", keyToolInput, name)
			}
		}
	}
	// 3. 判定に使わない残りの欄は、型が違えば既定の値にして続ける。
	toolResponse, ok := obj.Value(keyToolResponse).(*pyjson.Object)
	if !ok {
		toolResponse = &pyjson.Object{}
	}
	return Input{
		Event:          event,
		ToolName:       obj.String(keyToolName),
		ToolInput:      toolInput,
		SessionID:      obj.String(keySessionID),
		Cwd:            obj.String(keyCwd),
		PermissionMode: obj.String(keyPermissionMode),
		ToolUseID:      obj.String(keyToolUseID),
		AgentID:        text(obj.Value(keyAgentID)),
		AgentType:      text(obj.Value(keyAgentType)),
		Tools:          textList(obj.Value(keyTools)),
		ToolResponse:   toolResponse,
		error:          obj.String(keyError),
		IsInterrupt:    obj.Value(keyIsInterrupt) == true,
		StopHookActive: obj.Value(keyStopHookActive) == true,
		Source:         StartSource(text(obj.Value(keySource))),
		Prompt:         obj.String(keyPrompt),
		TranscriptPath: obj.String(keyTranscriptPath),
		Busy:           hasItems(obj.Value(keyBackgroundTasks)) || hasItems(obj.Value(keySessionCrons)),
	}, nil
}

// text は値を文字列にする。偽の値（無い・null・空など）は空文字。
func text(v any) string {
	if !pyfmt.Truthy(v) {
		return ""
	}
	return pyfmt.Str(v)
}

// textList は文字列だけの列ならその写し、それ以外は nil（欄が無いのと同じに読む）。
func textList(v any) []string {
	items, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		s, ok := item.(string)
		if !ok {
			return nil
		}
		out = append(out, s)
	}
	return out
}

// hasItems は空でない列か。欄が無い・形が違うときは偽。
func hasItems(v any) bool {
	items, ok := v.([]any)
	return ok && len(items) > 0
}

// decodeReplace は UTF-8 として読めないバイトを U+FFFD に替える。読めないバイトの並びは、
// 「正しく始まった並びの、読めた所まで」を 1 つの U+FFFD にする（Go の標準の替え方と数が違う）。
func decodeReplace(data []byte) string {
	if utf8.Valid(data) {
		return string(data)
	}
	var b strings.Builder
	for i := 0; i < len(data); {
		r, size := utf8.DecodeRune(data[i:])
		if r != utf8.RuneError || size > 1 {
			b.WriteRune(r)
			i += size
			continue
		}
		b.WriteRune(utf8.RuneError)
		i += invalidLength(data[i:])
	}
	return b.String()
}

// invalidLength は読めない並びの長さ（1 つの U+FFFD に置き換えるバイト数）。
func invalidLength(p []byte) int {
	lo, hi := byte(0x80), byte(0xbf)
	need := 0
	switch b := p[0]; {
	case b >= 0xc2 && b <= 0xdf:
		need = 1
	case b == 0xe0:
		need, lo = 2, 0xa0
	case b >= 0xe1 && b <= 0xec || b == 0xee || b == 0xef:
		need = 2
	case b == 0xed:
		need, hi = 2, 0x9f
	case b == 0xf0:
		need, lo = 3, 0x90
	case b >= 0xf1 && b <= 0xf3:
		need = 3
	case b == 0xf4:
		need, hi = 3, 0x8f
	default:
		return 1
	}
	n := 1
	for k := 0; k < need && n < len(p); k++ {
		c := p[n]
		if k == 0 && (c < lo || c > hi) || k > 0 && (c < 0x80 || c > 0xbf) {
			break
		}
		n++
	}
	return n
}
