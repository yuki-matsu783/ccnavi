package cli

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"reflect"
	"runtime"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/hook/events"
	"ccnavi/internal/hook/failsafe"
	"ccnavi/internal/hook/reasons"
	"ccnavi/internal/hook/subagent"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/diaglog"
	"ccnavi/internal/tickets/model/via"
)

// diagSource は hook の診断ログの出どころ（`logs/diag/ccnavi.log`）。
const diagSource = "ccnavi"

// hookRoute は、この起動が hook の経路（routes の最後）に進むかを決める。
// 設定を読む前に分ける必要があるときに使う。payload を読まない経路のどれにも当たらない起動が対象になる。
// 経路を足したら、ここにも足す。
func hookRoute(a opts) bool {
	return !(a.Truthy("docs") || a.Truthy("learnings") || a.Truthy("handoff") || a.Truthy("mirror") || diagnosing(a) ||
		a.Truthy("prune") || !a.IsNone("plan_order") || a.Truthy("fill_phases") || !a.IsNone("order") ||
		a.Truthy("write") || !a.IsNone("expect") || a.Truthy("agree") || len(a.command()) > 0 ||
		!a.IsNone("reviewed") || a.Truthy("close_early"))
}

// unloaded は、設定を読めなかった回の設定である。記録と state の既定の置き場だけを持つ。
func unloaded(root string) *config.Settings {
	return &config.Settings{Log: ospath.Join(root, config.DefaultLog), State: ospath.Join(root, config.DefaultState)}
}

// hookSoFar は hook の 1 回で、失敗までにどこまで進んだかを表す。受け皿（hookCrashed）が読む。
// モードを解く前に落ちたら、enable として止める側の答えにする。記録は 1 度しか試さない。記録そのものが落ちた回に、
// 受け皿で同じ失敗を重ねないためである。
type hookSoFar struct {
	mode     config.Mode
	payload  *hookio.Input
	record   *audit.Record
	log      *audit.Log
	recorded bool
}

// failure は、判定に達しなかった想定外の失敗を記録に残すときの型と場所を表す。失敗の文は書かない。
// コマンドやパスの断片が混ざることがあるためである。型と場所があれば辿れる。
type failure struct{ kind, where string }

func (f failure) detail() string {
	if f.where == "" {
		return f.kind
	}
	return f.kind + " at " + f.where
}

// failedWith は error で返った失敗。型は包みを外した元の error の型。
func failedWith(err error) failure {
	for {
		inner := errors.Unwrap(err)
		if inner == nil {
			break
		}
		err = inner
	}
	return failure{kind: typeName(err)}
}

func typeName(v any) string {
	name := reflect.TypeOf(v).String()
	name = strings.TrimLeft(name, "*")
	if at := strings.LastIndex(name, "."); at >= 0 {
		name = name[at+1:]
	}
	return name
}

// panicFrames は panic の場所を探すときに読むスタックの枠の数の上限。
const panicFrames = 64

// panicked は recover した値の失敗を表す。場所は panic した関数の行である。
// recover を呼んだ遅延関数の中から直接呼ぶ。スタックに panic の枠が残っている間だけ場所が分かるためである。
func panicked(r any) failure {
	f := failure{kind: "panic"}
	if err, ok := r.(error); ok {
		f.kind = typeName(err)
	}
	pcs := make([]uintptr, panicFrames)
	frames := runtime.CallersFrames(pcs[:runtime.Callers(1, pcs)])
	seen := false
	for {
		frame, more := frames.Next()
		if seen && !strings.HasPrefix(frame.Function, "runtime.") {
			f.where = ospath.Basename(frame.File) + ":" + strconv.Itoa(frame.Line)
			break
		}
		if frame.Function == "runtime.gopanic" {
			seen = true
		}
		if !more {
			break
		}
	}
	return f
}

// onPanic は panic を受ける遅延関数である。panic していれば、その値と、それを読んだ失敗を handle に渡す。
// recover は遅延された関数の中でしか効かないので、`defer onPanic(…)` の形で直接置く。
func onPanic(handle func(r any, f failure)) {
	if r := recover(); r != nil {
		handle(r, panicked(r))
	}
}

// runHook は hook として起動された 1 回を処理する。途中のどこで失敗しても、終了コード 1 で終わらせない。
// 1 で終わると Claude Code は PreToolUse の呼び出しをそのまま通すからである。
func runHook(in *input, stdout, stderr io.Writer, conf *config.Settings, problems []string, root string, a opts) (code int) {
	so := &hookSoFar{mode: config.Enable}
	defer onPanic(func(_ any, f failure) { code = hookCrashed(stdout, stderr, conf, root, so, f) })
	return hookSteps(in, stdout, stderr, conf, problems, root, a, so)
}

// resolveSwitches は、判定が読む切り替えの環境変数を解いて設定に戻す。
// hook と診断（`--test` など）が同じ関数を使う。解く前の値は書いたままの表記を持つ。
// 判定はどれも解いた後の値と比べるので、解かずに判定を呼ぶと hook と違う答えになる。
func resolveSwitches(stderr io.Writer, conf *config.Settings, a opts) {
	conf.RestoreIfDeny = string(selfguard.Resolve(stderr, a.Str("restore_if_deny"), conf.RestoreIfDeny,
		env.RestoreIfDeny, selfguard.Choices))
	conf.GuardCoreFiles = string(selfguard.Resolve(stderr, a.Str("guard_core_files"), conf.GuardCoreFiles,
		env.GuardCoreFiles, selfguard.Choices))
	// この切り替えは enable / disable の 2 値。止めずに報告する段は CCNAVI_MODE=dry-run が持つ。
	conf.GuardUnwatched = resolveGate(stderr, "", conf.GuardUnwatched, env.GuardUnwatched)
}

// hookSteps は hook の 1 回分の処理である。進み具合を so に残す。出力は手元に溜めておき、
// 記録と診断ログを終えてから出す。失敗で終えられなかった回に、途中まで書いた JSON と内部エラーの応答が混ざり、
// どちらも読まれなくなるのを避けるためである。
func hookSteps(in *input, stdout, stderr io.Writer, conf *config.Settings, problems []string, root string, a opts,
	so *hookSoFar) int {
	for _, problem := range problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", problem)
	}
	mode := config.ResolveMode(stderr, a.Str("mode"), conf)
	so.mode = mode
	resolveSwitches(stderr, conf, a)
	log := audit.NewLog(conf.Log)
	so.log = log
	deadline := time.Now().Add(config.HookDeadline.Value())

	payload, err := hookio.Decode(in.buf)
	var unusable *hookio.UnusableError
	switch {
	case errors.Is(err, hookio.ErrNoPayload):
		// hook ではなくターミナルの前のユーザ。使い方を出す。
		io.WriteString(stderr, overview())
		return config.ExitError
	case errors.As(err, &unusable):
		fmt.Fprintln(stderr, reasons.PayloadUnusable(root, string(audit.ReasonPayloadUnusable), unusable.Msg,
			unusable.Event, mode))
		diaglog.Get(diagSource, root).Warn("hook の payload を読めない", "mode", string(mode))
		so.recorded = true
		writeRecord(stderr, log, &audit.Record{Mode: mode, Event: string(unusable.Event), Decision: audit.Skip,
			Reason: audit.ReasonPayloadUnusable})
		// ツールが分からない PreToolUse（とイベントすら読めなかった回）は dry-run でも止める。
		if unusable.Event == "" || unusable.Event == hookio.PreToolUse {
			return config.FailClosed(mode, "", false)
		}
		return config.FailClosedEvent(mode)
	case err != nil:
		return hookCrashed(stdout, stderr, conf, root, so, failedWith(err))
	}

	so.payload = &payload
	record := &audit.Record{Mode: mode, PermissionMode: payload.PermissionMode, Event: string(payload.Event),
		Tool: payload.ToolName, Session: payload.SessionID, Agent: payload.AgentID}
	so.record = record

	var buffered bytes.Buffer
	code, fail := judgeEvent(&buffered, stderr, mode, conf, root, payload, record, deadline)
	if fail != nil {
		buffered.Reset()
		code = internalError(&buffered, stderr, mode, conf, root, payload, record, *fail)
	}
	so.recorded = true
	writeRecord(stderr, log, record)
	// 受け付けたイベントと最終判定だけを書く。subject（コマンドの全文）は書かない。
	// 呼び出しごとに走るので DEBUG に置く。
	diaglog.Get(diagSource, root).Debug("hook を判定した", "event", record.Event, "tool", record.Tool,
		"mode", string(mode), "decision", string(record.Decision), "code", record.Code, "exit", code)
	stdout.Write(buffered.Bytes())
	return code
}

// judgeEvent はイベントを判定する。下の部品は panic を受けないので、ここで受けて error と同じく失敗として返す。
func judgeEvent(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record, deadline time.Time) (code int, fail *failure) {
	defer onPanic(func(_ any, f failure) { fail = &f })
	defer via.Session(via.Hook, "")()
	record.Subject = rules.SubjectOf(in)
	code, err := events.Decide(stdout, stderr, mode, conf, root, in, record, deadline)
	if err != nil {
		f := failedWith(err)
		return code, &f
	}
	return code, nil
}

// writeRecord は 1 行を書く。書けなかったときは報告して捨てる。書けないことでツールの実行可否は変えない。
func writeRecord(stderr io.Writer, log *audit.Log, record *audit.Record) {
	if err := log.Write(record); err != nil {
		logUnsaved(stderr, fsio.ErrorText(err))
	}
}

// logUnsaved は記録を書けなかったことを、その理由とともに報告する。
func logUnsaved(stderr io.Writer, reason string) {
	fmt.Fprintf(stderr, "ccnavi: ログを保存できませんでした：%s\n", reason)
}

// hookCrashed は、判定の本体の外（モードの解決、payload の読み取り、記録など）で落ちた回の受け皿である。
// payload を読む前に落ちたら、何のイベントか分からないので、PreToolUse と同じく止める。
// 記録はまだ試していないときだけ書く。
func hookCrashed(stdout, stderr io.Writer, conf *config.Settings, root string, so *hookSoFar, fail failure) int {
	payload := hookio.Input{}
	if so.payload != nil {
		payload = *so.payload
	}
	record := so.record
	if record == nil {
		record = &audit.Record{Mode: so.mode, Event: string(payload.Event), Tool: payload.ToolName,
			Session: payload.SessionID}
	}
	var out bytes.Buffer
	code := internalError(&out, stderr, so.mode, conf, root, payload, record, fail)
	if !so.recorded {
		so.recorded = true
		log := so.log
		if log == nil {
			log = audit.NewLog(conf.Log)
		}
		recordQuietly(stderr, log, record)
	}
	stdout.Write(out.Bytes())
	return code
}

// recordQuietly は受け皿の中の記録。ここで落ちても失敗を重ねない。
func recordQuietly(stderr io.Writer, log *audit.Log, record *audit.Record) {
	defer onPanic(func(_ any, f failure) {
		logUnsaved(stderr, f.kind)
	})
	writeRecord(stderr, log, record)
}

// internalError は、イベントの処理が想定外の失敗で終わったときの応答と記録である。
// Claude Code は 0 と 2 以外を止める扱いにしないので、ここで受けて、判定に達しなかった回として終える。
//
//   - PreToolUse（とイベントが分からない回）: ツールの種類で決める。
//     設定を読まずに通してよいと確かめた読み取りだけを通す
//   - PostToolUse: ツールはもう走っているので止めるものは無い。enable は終了コード 2 で、
//     実行後の検査をしていないことをモデルに言う
//   - それ以外: 終了コード 0 で終え、ユーザに systemMessage で知らせる。2 で終えると Stop は終われず、
//     UserPromptSubmit は依頼文を消してしまう。SubagentStop は、子の範囲を確かめられなかった回と同じ印を置き、
//     親に届くようにする
func internalError(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, payload hookio.Input,
	record *audit.Record, fail failure) int {
	logPath := ospath.Join(root, diaglog.Dir, diagSource+".log")
	quietly(func() {
		diaglog.Get(diagSource, root).Error("hook の処理が例外で終わった", "event", string(payload.Event),
			"tool", payload.ToolName, "mode", string(mode), "error", fail.kind, "at", fail.where)
	})
	record.Decision, record.Reason = audit.Skip, audit.ReasonInternalError
	record.Code, record.Rules = "", nil
	record.Detail = fail.detail()
	code := string(audit.ReasonInternalError)
	switch payload.Event {
	case "", hookio.PreToolUse, hookio.PostToolUse:
		var exit int
		if payload.Event == hookio.PostToolUse {
			exit = config.FailClosedEvent(mode)
		} else {
			exit = config.FailClosed(mode, payload.ToolName, failsafe.IsSafe(payload, root))
		}
		passed := exit == config.ExitOK && payload.Event != hookio.PostToolUse
		fmt.Fprintln(stderr, reasons.InternalError(root, code, payload.Event, fail.kind, logPath, passed))
		return exit
	case hookio.SubagentStop:
		subagent.AfterError(stderr, conf, root, payload)
	}
	hookio.WriteSystemMessage(stdout, reasons.InternalErrorNotice(code, payload.Event, fail.kind, logPath))
	return config.ExitOK
}

// quietly は失敗の出どころが診断ログそのものでも、受け皿の中で失敗を重ねない。
func quietly(f func()) {
	defer onPanic(func(any, failure) {})
	f()
}
