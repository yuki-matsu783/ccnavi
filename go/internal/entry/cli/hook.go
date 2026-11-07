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

// hookRoute はこの起動が hook の経路（routes の最後）に進むか。設定を読む前に分ける必要があるときに使う。payload を
// 読まない経路のどれにも当たらないもの。経路を足したら、ここにも足す。
func hookRoute(a opts) bool {
	return !(a.Truthy("docs") || a.Truthy("learnings") || a.Truthy("handoff") || a.Truthy("mirror") || diagnosing(a) ||
		a.Truthy("prune") || !a.IsNone("plan_order") || a.Truthy("fill_phases") || !a.IsNone("order") ||
		a.Truthy("write") || !a.IsNone("expect") || a.Truthy("agree") || len(a.command()) > 0 ||
		!a.IsNone("reviewed") || a.Truthy("close_early"))
}

// unloaded は設定を読めなかった回の、記録と state の既定の置き場だけを持つ設定。
func unloaded(root string) *config.Settings {
	return &config.Settings{Log: ospath.Join(root, config.DefaultLog), State: ospath.Join(root, config.DefaultState)}
}

// hookSoFar は hook の 1 回で、失敗までにどこまで進んだか。受け皿（hookCrashed）が読む。モードを解く前に落ちたら
// enable として止める側に倒す。記録は 1 度しか試さない（記録そのものが落ちた回に、受け皿で同じ失敗を重ねない）。
type hookSoFar struct {
	mode     config.Mode
	payload  *hookio.Input
	record   *audit.Record
	log      *audit.Log
	recorded bool
}

// failure は判定に達せなかった想定外の失敗の、記録に残す型と場所。失敗の文は書かない（コマンドやパスの断片が
// 混ざることがある）。型と場所があれば辿れる。
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

// panicked は recover した値の失敗。場所は panic した関数の行。recover を呼んだ遅延関数の中から直に呼ぶ
// （スタックに panic の枠が残っている間だけ場所が分かる）。
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

// onPanic は panic を受ける遅延関数。panic していれば、値と、それを読んだ失敗を handle に渡す。recover は遅延した
// 関数の中でしか効かないので、`defer onPanic(…)` の形で直に置く。
func onPanic(handle func(r any, f failure)) {
	if r := recover(); r != nil {
		handle(r, panicked(r))
	}
}

// runHook は hook として起動された 1 回。途中のどこで失敗しても、終了コード 1 で抜けさせない。1 で終わると
// Claude Code は PreToolUse の呼び出しをそのまま通す。
func runHook(in *input, stdout, stderr io.Writer, conf *config.Settings, problems []string, root string, a opts) (code int) {
	so := &hookSoFar{mode: config.Enable}
	defer onPanic(func(_ any, f failure) { code = hookCrashed(stdout, stderr, conf, root, so, f) })
	return hookSteps(in, stdout, stderr, conf, problems, root, a, so)
}

// resolveSwitches は判定が読む切り替えの環境変数を解いて、設定に戻す。hook と診断（`--test` など）で同じもの。
// 解く前の値は書いたままの表記を持ち、判定はどれも解いた後の値と比べるので、解かずに判定を呼ぶと hook と違う答えになる。
func resolveSwitches(stderr io.Writer, conf *config.Settings, a opts) {
	conf.RestoreIfDeny = string(selfguard.Resolve(stderr, a.Str("restore_if_deny"), conf.RestoreIfDeny,
		env.RestoreIfDeny, selfguard.Choices))
	conf.GuardCoreFiles = string(selfguard.Resolve(stderr, a.Str("guard_core_files"), conf.GuardCoreFiles,
		env.GuardCoreFiles, selfguard.Choices))
	// この切り替えは enable / disable の 2 値。止めずに報告する段は CCNAVI_MODE=dry-run が持つ。
	conf.GuardUnwatched = resolveGate(stderr, "", conf.GuardUnwatched, env.GuardUnwatched)
}

// hookSteps は hook の 1 回分の処理。進み具合を so に残す。出力は手元に溜め、記録と診断ログを終えてから出す
// （失敗で終えられなかった回に、途中まで書いた JSON と内部エラーの応答が混ざって、どちらも読まれなくなるのを避ける）。
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
	// 受け付けたイベントと最終判定だけ。subject（コマンドの全文）は書かない。呼び出しごとに走るので DEBUG に置く。
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

// writeRecord は 1 行を書く。書けなかったことは報告して捨てる。書けないことでツールの実行可否を変えない。
func writeRecord(stderr io.Writer, log *audit.Log, record *audit.Record) {
	if err := log.Write(record); err != nil {
		fmt.Fprintf(stderr, "ccnavi: ログを保存できませんでした：%s\n", err)
	}
}

// hookCrashed は判定の本体の外（モードの解決・payload の読み取り・記録など）で落ちた回の受け皿。payload を読む前に
// 落ちたら、何のイベントか分からないので PreToolUse と同じく止める。記録はまだ試していないときだけ書く。
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
		fmt.Fprintf(stderr, "ccnavi: ログを保存できませんでした：%s\n", f.kind)
	})
	writeRecord(stderr, log, record)
}

// internalError はイベントの処理が想定外の失敗で終わったときの応答と記録。Claude Code は 0 と 2 以外を止める扱いに
// しないので、ここで受けて、判定に達せなかった回として終える。
//
//   - PreToolUse（とイベントが分からない回）: ツールの種類で決める。設定を読まずに通してよいと確かめた読み取りだけを通す
//   - PostToolUse: ツールはもう走っているので止めるものは無い。enable は終了コード 2 で、実行後の検査をしていないことを
//     モデルに言う
//   - それ以外: 終了コード 0 で、ユーザに systemMessage で知らせる。2 で終えると Stop は終われず、UserPromptSubmit は
//     依頼文を消す。SubagentStop は子の範囲を確かめられなかった回と同じ印を置き、親に届くようにする
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
