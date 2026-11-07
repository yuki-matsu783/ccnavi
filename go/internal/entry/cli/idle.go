package cli

import (
	"fmt"
	"io"
	"os"
	"slices"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/handoff"
)

// idleVerbs は `ccnavi idle` の動詞。
var idleVerbs = []string{"notice", "start", "check", "reset"}

// runIdle は `ccnavi idle notice|start|check|reset`。放置したセッションの引き継ぎ促しの入口。どれも終了コード 0 で
// 終わる（促しの失敗が作業を止めてはならない）。判断は records/handoff、待機（sleep）は sh が受け持つ。
//
// start の標準出力は 1 行 1 項目（`<キー> <値>`）で、始めるときは wait・session・token の 3 行、始めないときは
// `skip <理由>` の 1 行。check は 1 行目が fire か skip。notice は出すときだけ systemMessage の JSON を 1 行書く。
func runIdle(in *input, stdout, stderr io.Writer, conf *config.Settings, words []string) int {
	verb := ""
	if len(words) > 1 {
		verb = words[1]
	}
	if !slices.Contains(idleVerbs, verb) {
		fmt.Fprintln(stderr, "使い方: ccnavi idle notice|start|check|reset")
		return config.ExitOK
	}
	limit := handoff.Threshold(conf.HandoffSuggestTokens)
	if conf.State == "" || limit == 0 {
		idleSkip(stdout, verb, "disabled")
		return config.ExitOK
	}
	if verb == "check" {
		idleCheck(stdout, conf, words[2:], limit)
		return config.ExitOK
	}
	payload, err := hookio.Decode(in.buf)
	if err != nil {
		idleSkip(stdout, verb, "no-payload")
		return config.ExitOK
	}
	if verb == "reset" {
		handoff.Reset(conf.State, payload.SessionID, payload.Prompt, payload.FromBackgroundTask())
		return config.ExitOK
	}
	if why := notForIdle(payload); why != "" {
		idleSkip(stdout, verb, why)
		return config.ExitOK
	}
	usage := handoff.UsageOf(payload.TranscriptPath)
	if verb == "notice" {
		if text, ok := handoff.Notice(conf.State, payload.SessionID, usage, limit, conf.HandoffWaitSeconds); ok {
			writeJSON(stdout, pyjson.NewObject("systemMessage", text), false, 0)
		}
		return config.ExitOK
	}
	handoff.Prune(conf.State, time.Now())
	plan, ok := handoff.Start(conf.State, payload.SessionID, usage, limit, conf.HandoffWaitSeconds, payload.TranscriptPath)
	if !ok {
		fmt.Fprintln(stdout, "skip not-due")
		return config.ExitOK
	}
	fmt.Fprintf(stdout, "wait %d\nsession %s\ntoken %s\n", int(plan.Wait/time.Second), payload.SessionID, plan.Token)
	return config.ExitOK
}

func idleSkip(stdout io.Writer, verb, why string) {
	if verb == "start" || verb == "check" {
		fmt.Fprintf(stdout, "skip %s\n", why)
	}
}

// notForIdle はこの呼び出しが対象でない理由。対象なら空。
func notForIdle(payload hookio.Input) string {
	switch {
	case payload.Event != hookio.Stop:
		return "not-stop"
	case payload.AgentID != "":
		return "subagent"
	case hookio.Headless(os.Getenv):
		return "unattended"
	}
	return ""
}

// idleCheck は `idle check <セッション> <印> <待ち秒>`。待機のあと、起こすべきか、起こすときの指示を返す。
func idleCheck(stdout io.Writer, conf *config.Settings, rest []string, limit int) {
	seconds := -1
	if len(rest) == 3 && pyfmt.IsASCIIDigits(rest[2]) {
		if n, err := pyfmt.ParseInt(rest[2]); err == nil {
			seconds = n
		}
	}
	if seconds < 0 {
		// int に収まらない待ち秒も読めない引数と同じに扱う（Python は大きな整数のまま進む）。
		fmt.Fprintln(stdout, "skip usage")
		return
	}
	text, ok := handoff.Check(conf.State, rest[0], rest[1], time.Duration(seconds)*time.Second, limit)
	if !ok {
		fmt.Fprintln(stdout, "skip not-waiting")
		return
	}
	io.WriteString(stdout, "fire\n"+text)
}
