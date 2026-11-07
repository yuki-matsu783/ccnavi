package hookio

import (
	"io"

	"ccnavi/internal/infra/pyjson"
)

// 応答の欄の名前。
const (
	keyHookSpecificOutput       = "hookSpecificOutput"
	keyHookEventName            = "hookEventName"
	keyPermissionDecision       = "permissionDecision"
	keyPermissionDecisionReason = "permissionDecisionReason"
	keyAdditionalContext        = "additionalContext"
	keyUpdatedInput             = "updatedInput"
	keySystemMessage            = "systemMessage"
	keyDecision                 = "decision"
	keyReason                   = "reason"
	// stopBlock は Stop を止めてモデルに続けさせる decision の値。
	stopBlock = "block"
)

// outputOptions は応答の JSON の書き方（ASCII でない字をそのまま書く）。
var outputOptions = pyjson.Options{RawUnicode: true}

// PreToolUseResponse は PreToolUse の応答。どの欄も空にできる。
type PreToolUseResponse struct {
	// Decision は判定。空なら判定を付けず、権限の確認の結末は変わらない。
	Decision Decision
	// Reason は判定の理由（なぜ止めたかと、代わりに何をすればよいか）。Decision があるときだけ載せる。
	Reason string
	// Context はモデルに届く文（ルールの additionalContext）。deny でも ask でも理由と一緒に届く。
	Context string
	// UpdatedInput は入力の書き換え。判定が無ければ書き換えた入力でそのまま進み、allow は確認を飛ばし、
	// ask は書き換えた入力をユーザに見せる。
	UpdatedInput *pyjson.Object
}

// WritePreToolUse は PreToolUse の応答を 1 つの JSON にして書く。空の欄はキーごと出さない。
func WritePreToolUse(w io.Writer, r PreToolUseResponse) error {
	body := pyjson.NewObject(keyHookEventName, string(PreToolUse))
	if r.Decision != "" {
		body.Set(keyPermissionDecision, string(r.Decision))
		body.Set(keyPermissionDecisionReason, r.Reason)
	}
	if r.Context != "" {
		body.Set(keyAdditionalContext, r.Context)
	}
	if r.UpdatedInput != nil {
		body.Set(keyUpdatedInput, r.UpdatedInput)
	}
	return write(w, pyjson.NewObject(keyHookSpecificOutput, body))
}

// WriteContext は、判定を返さないときにモデルに届く文を書く。PostToolUse と SessionStart は判定が
// できないのでこれだけ。PreToolUse でも、判定を持たないときや dry-run で知らせるだけのときに使う
// （素の標準出力は破棄される）。system はユーザに見せる文で、空ならキーごと出さない。
func WriteContext(w io.Writer, event Event, text, system string) error {
	body := pyjson.NewObject(keyHookSpecificOutput,
		pyjson.NewObject(keyHookEventName, string(event), keyAdditionalContext, text))
	if system != "" {
		body.Set(keySystemMessage, system)
	}
	return write(w, body)
}

// WriteSystemMessage はユーザに見せる文を書く。additionalContext はモデルが読み、これはトランスクリプトに
// 出てユーザが読む。
func WriteSystemMessage(w io.Writer, text string) error {
	return write(w, pyjson.NewObject(keySystemMessage, text))
}

// WriteStopBlock は Stop を止めてモデルに続けさせる。reason はモデルが読む次の一手。ユーザへの報告
// （system）も同じ 1 つの JSON で返す（exit 2 だと標準出力の JSON は読まれない）。空の system は出さない。
func WriteStopBlock(w io.Writer, reason, system string) error {
	body := pyjson.NewObject(keyDecision, stopBlock, keyReason, reason)
	if system != "" {
		body.Set(keySystemMessage, system)
	}
	return write(w, body)
}

// write は 1 つの JSON と改行を書く。Claude Code は標準出力が "{" で始まり "}" で終わるときだけ
// JSON として読むので、ほかのものを一緒に出さない。
func write(w io.Writer, body *pyjson.Object) error {
	text, err := pyjson.Dumps(body, outputOptions)
	if err != nil {
		return err
	}
	_, err = io.WriteString(w, text+"\n")
	return err
}
