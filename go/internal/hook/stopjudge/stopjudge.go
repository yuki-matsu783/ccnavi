package stopjudge

import (
	"io"
	"regexp"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/redact"
	"ccnavi/internal/records/stopreview"
)

const (
	// ReasonJudge は判定役の呼び出しの記録の reason。上の段もほかのイベントで同じ値を残す。
	ReasonJudge  audit.Reason = "stop-review-judge"
	reasonWorker audit.Reason = "stop-review-worker"

	// judgeAgentIDPrefix は実機で確かめた、agent 型 hook の中の呼び出しの agent_id の接頭辞。
	judgeAgentIDPrefix = "hook-agent-"
	// returnTool は判定役が結果を返すために呼ぶ道具。止めると判定役が返せない。
	returnTool = config.ToolStructuredOutput

	verb       = "stop-review"
	ccnaviName = "ccnavi"
	launcher   = config.DefaultProjectHome + "/" + config.ScriptsDir + "/" + config.LauncherScript

	// taintKeep は印に残す呼び出しの数、taintCommandLimit は残すコマンドの長さ（符号位置）。
	taintKeep         = 20
	taintCommandLimit = 300

	allowedText = "ccnavi: Stop の判定役の呼び出し"
	judgeDenied = "ccnavi: Stop の判定役が実行できるのは、次の形の 1 本だけです。連結・リダイレクトは付けず、" +
		"作業ディレクトリのまま実行してください: " + config.Launcher + " stop-review material <session_id> <transcript_path>"
	workerDenied = "ccnavi: `stop-review` は Stop の判定役が使う入口で、作業では使いません。"
)

// verbArgs は動詞のあとの引数の数の範囲。material は <session_id> <transcript_path>、record は JSON を 1 つ足せる。
var verbArgs = map[string][2]int{"material": {2, 2}, "record": {2, 3}}

var (
	runners   = set("sh", "bash", "zsh", "dash", "ksh", "eval", "env", "xargs", "exec", "source")
	feeders   = set("echo", "printf", "cat")
	shells    = set("sh", "bash")
	loose     = pyre.MustCompile(`stop-review["'\s]+["']?(?:material|record)\b`, pyre.IgnoreCase)
	redirect  = pyre.MustCompile(`^(?:\d*>|&>|<(?!<))`, 0)
	harmless  = pyre.MustCompile(`^(?:\d?>>?|&>)(?:/dev/null|&\d)$`, 0)
	redirOp   = pyre.MustCompile(`^(?:\d?>>?|&>|>&)$`, 0)
	fd        = pyre.MustCompile(`^\d$`, 0)
	heredoc   = pyre.MustCompile(`<<-?\s*(?P<q>["']?)(?P<end>\w+)(?P=q)`, 0)
	singleArg = regexp.MustCompile(`'[^']*'`)
)

func set(words ...string) map[string]bool {
	out := make(map[string]bool, len(words))
	for _, w := range words {
		out[w] = true
	}
	return out
}

// IsJudge はこの呼び出しが判定役（agent 型 hook の中）のものか。判定役を登録していなければ偽。
func IsJudge(conf *config.Settings, in hookio.Input) bool {
	return conf.StopReviewAgent && strings.HasPrefix(in.AgentID, judgeAgentIDPrefix)
}

// stopReviewCall は `stop-review material|record` を打つ可能性のあるコマンドか。外側が `ccnavi stop-review …` なら真。
// 読み取れない形（sh -c・eval・置換の中）は `stop-review` と動詞が並んでいれば真にする（印のためなので誤って真に
// なってもよい）。echo や grep の引数に書いただけでは真にしない。
func stopReviewCall(command string) bool {
	outer := shell.PlacedCommands(command, false)
	runsText := false
	for _, p := range outer {
		if isStopReview(p.Words) {
			return true
		}
		if len(p.Words) > 0 && runners[ospath.Basename(pathcase.Fold(p.Words[0]))] {
			runsText = true
		}
	}
	indirect := runsText || strings.ContainsAny(command, "$`")
	return indirect && loose.Search(command)
}

func isStopReview(words []string) bool {
	for at := 1; at < len(words)-1; at++ {
		if _, known := verbArgs[words[at+1]]; known && words[at] == verb {
			prev := ospath.Basename(strings.ReplaceAll(pathcase.Fold(words[at-1]), `\`, "/"))
			if strings.HasPrefix(prev, ccnaviName) {
				return true
			}
		}
	}
	return false
}

// redirectsOK は語の並びのリダイレクトが `/dev/null` か別の fd への複製だけか。シェルの読み取りは `2>/dev/null` を
// `2`・`>`・`/dev/null` のように語に割ることがあるので、並びで見る。
func redirectsOK(words []string) bool {
	for at := 0; at < len(words); {
		word := words[at]
		if harmless.Match(word) {
			at++
			continue
		}
		if redirOp.Match(word) {
			target := ""
			if at+1 < len(words) {
				target = words[at+1]
			}
			if target == "/dev/null" || strings.HasSuffix(word, "&") && fd.Match(target) {
				at += 2
				continue
			}
			return false
		}
		if redirect.Match(word) {
			return false
		}
		at++
	}
	return true
}

// stripRedirects は無害なリダイレクトの語を除く。ほかのリダイレクトがあれば false。
func stripRedirects(words []string) ([]string, bool) {
	if !redirectsOK(words) {
		return nil, false
	}
	var out []string
	for at := 0; at < len(words); {
		word, following := words[at], ""
		if at+1 < len(words) {
			following = words[at+1]
		}
		switch {
		case harmless.Match(word):
			at++
		case fd.Match(word) && redirOp.Match(following):
			at += 3
		case redirOp.Match(word):
			at += 2
		default:
			out = append(out, word)
			at++
		}
	}
	return out, true
}

func sameDir(a, b string) bool {
	return a != "" && b != "" && strings.TrimRight(pathcase.Fold(a), `/\`) == strings.TrimRight(pathcase.Fold(b), `/\`)
}

func slashedFold(p string) string { return strings.ReplaceAll(pathcase.Fold(p), `\`, "/") }

// strictCall は語の並びが、許す形の `stop-review material|record` そのものか（先頭語から完全一致）。
func strictCall(words []string, cwd string) bool {
	var rest []string
	switch {
	case len(words) > 0 && words[0] == ccnaviName:
		rest = words[1:]
	case len(words) > 1 && shells[words[0]]:
		named := slashedFold(words[1])
		own := ""
		if cwd != "" {
			own = strings.TrimRight(slashedFold(cwd), "/") + "/" + launcher
		}
		if named != launcher && named != own {
			return false
		}
		rest = words[2:]
	default:
		return false
	}
	if len(rest) < 2 || rest[0] != verb {
		return false
	}
	span, ok := verbArgs[rest[1]]
	return ok && span[0] <= len(rest)-2 && len(rest)-2 <= span[1]
}

// judgeCommandOK は判定役に許す Bash か。`ccnavi stop-review material|record …` の 1 本だけ。
// 標準入力に JSON を渡す echo・printf・cat（パイプの左）とヒアドキュメントは許す。実機で判定役が付けた
// `cd <プロジェクト> 2>/dev/null;` を拒んで材料を取れずに期限切れになったので、判定役自身の作業ディレクトリへの cd と、
// `/dev/null` や `&1` へのリダイレクトだけも許す。ほかの連結・リダイレクト・置換・読み切れない形は許さない。
func judgeCommandOK(command, cwd string) bool {
	head, ok := heredocHead(command)
	if ok {
		// 単一引用符の中はシェルが展開しない（JSON を引数で渡す形）。引用の外だけを見る。
		head = singleArg.ReplaceAllString(head, "''")
	}
	if shell.Read(command).Degraded || !ok || strings.Contains(head, "$(") || strings.Contains(head, "`") ||
		strings.Contains(head, "${") || strings.Contains(head, "<(") || strings.Contains(head, ">(") ||
		!heredocBodyInert(command) {
		return false
	}
	calls := 0
	for _, p := range shell.PlacedCommands(command, false) {
		if len(p.Words) == 0 {
			return false
		}
		words, ok := stripRedirects(p.Words)
		if !ok || len(words) == 0 {
			return false
		}
		switch {
		case strictCall(words, cwd):
			calls++
		case words[0] == "cd" && len(words) == 2 && sameDir(words[1], cwd):
		case !feeders[words[0]]:
			return false
		}
	}
	return calls == 1
}

func commandLines(command string) []string {
	return strings.Split(strings.Trim(command, "\n"), "\n")
}

// heredocBodyInert はヒアドキュメントの本文でシェルが何も展開しないか。区切りが引用されていれば展開されない。
func heredocBodyInert(command string) bool {
	lines := commandLines(command)
	if len(lines) == 1 {
		return true
	}
	m, found := heredoc.Find(lines[0])
	if !found {
		return true
	}
	if q, _ := m.Named("q"); q != "" {
		return true
	}
	return !strings.ContainsAny(strings.Join(lines[1:], "\n"), "$`\\")
}

// heredocHead はコマンドの 1 行目（ヒアドキュメントの本文を除く）。本文の終端より後に何かあれば false。
func heredocHead(command string) (string, bool) {
	lines := commandLines(command)
	if len(lines) == 1 {
		return lines[0], true
	}
	m, found := heredoc.Find(lines[0])
	if !found {
		return "", false
	}
	end, _ := m.Named("end")
	for at := 1; at < len(lines); at++ {
		if ospath.Strip(lines[at]) == end {
			for _, rest := range lines[at+1:] {
				if ospath.Strip(rest) != "" {
					return "", false
				}
			}
			return lines[0], true
		}
	}
	return "", false
}

// markTaint は作業者が `stop-review` を打った印を残す。時刻と、伏せ字にしたコマンドを直近の taintKeep 件だけ持つ。
func markTaint(state, session, command string, now time.Time) {
	path := fsio.RecordPath(state, stopreview.TaintPrefix, session, fsio.JSONExt)
	var calls []any
	if row, ok := fsio.ReadObject(path); ok {
		calls, _ = row.Value("calls").([]any)
	}
	if len(calls) > taintKeep-1 {
		calls = calls[len(calls)-(taintKeep-1):]
	}
	entry := pyjson.NewObject("t", fsio.UnixSeconds(now), "command", pyfmt.Head(redact.Redact(command), taintCommandLimit))
	fsio.WriteJSON(path, pyjson.NewObject("calls", append(append([]any(nil), calls...), entry)))
}

// Before は PreToolUse の入口。通常の判定へ回さないなら真を返す（応答は書き終えている）。
// 判定役の呼び出しは許す形か返り値の道具だけ通し、ほかは止める。作業者の `stop-review` は印を付け、
// enable なら止める。
func Before(stdout io.Writer, mode config.Mode, conf *config.Settings, in hookio.Input, record *audit.Record) bool {
	if !conf.StopReviewAgent {
		return false
	}
	command := in.Field("command")
	asks := in.ToolName == config.ToolBash && stopReviewCall(command)
	if IsJudge(conf, in) {
		record.Reason = ReasonJudge
		if asks && judgeCommandOK(command, in.Cwd) || in.ToolName == returnTool {
			// agent 型 hook の中は dontAsk で動き、許可の規則が無いと Bash が拒否される（実機で確かめた）。hook の許可で通す。
			record.Decision = audit.Allow
			hookio.WritePreToolUse(stdout, hookio.PreToolUseResponse{Decision: hookio.Allow, Reason: allowedText})
			return true
		}
		hookio.WritePreToolUse(stdout, hookio.PreToolUseResponse{Decision: hookio.Deny, Reason: judgeDenied})
		record.Decision, record.Enforced = audit.Deny, true
		return true
	}
	if !asks {
		return false
	}
	if conf.State != "" && in.SessionID != "" {
		markTaint(conf.State, in.SessionID, command, time.Now())
	}
	if mode == config.DryRun || stopreview.Setting(mode, conf.StopReview) != config.Enable {
		return false
	}
	record.Decision, record.Enforced = audit.Deny, true
	record.Reason = reasonWorker
	hookio.WritePreToolUse(stdout, hookio.PreToolUseResponse{Decision: hookio.Deny, Reason: workerDenied})
	return true
}
