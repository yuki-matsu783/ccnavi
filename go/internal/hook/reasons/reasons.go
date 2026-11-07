package reasons

import (
	"fmt"
	"strings"
	"time"

	"ccnavi/internal/hook/ccnavimd"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/phase"
)

// 判定の理由コード。実行後チェックのコード（POST_*）は post にある。あちらは判定ではなく、起きたことの報告なので混ぜない。
const (
	// CodeCommand は Bash の実行される部分に、CodePath はファイルのパスにルールがヒットした。
	CodeCommand = "DENY_COMMAND_PATTERN"
	CodePath    = "DENY_PATH"
	// CodeRuleAsk はルールが ask と書いた場所。毎回人間が見るべき確認ポイントで、繰り返し出ること自体に値打ちがある。
	CodeRuleAsk = "RULE_ASK"
	// CodeUndeclared はルールがどこも言及していない。危険の表明ではなく、扱いを権限モードに委ねたことを言う。
	// 委ねた先が判断できないモードなら許可としないので、渡した回と断った回の両方に付く（どちらかは記録の decision が持つ）。
	CodeUndeclared = "UNDECLARED"
	// CodeUncertain はコマンドを読み切れず、対象を確定できなかった。権限モードに委ねない（読めなかった事実が委ねた先に
	// 伝わらない）。生の文字列が deny にヒットしたときだけ拒否になり、そのときも読めないまま適用したことを文が断る。
	CodeUncertain = "PARSE_UNCERTAIN"
	// CodeTicketScope は承認されたチケットの範囲の外に書こうとした。次の一手（範囲の中で済ませるか、書き直して承認を
	// 求める）がルールのときと違うので分ける。
	CodeTicketScope = "DENY_TICKET_SCOPE"
	// CodeTicketAsk はチケットが ask と書いた場所。
	CodeTicketAsk = "TICKET_ASK"
	// CodeRedirectLimitDeny は生の文字列の `>` が上限を超え、シェルから書き込む形の保護を当てられなかったので止めた。
	// 権限モードに依らず止め、確認には回さない。
	CodeRedirectLimitDeny = "DENY_REDIRECT_LIMIT"
	// CodeTicketProject はワークツリーの元リポジトリと、チケットが承認されたプロジェクトが食い違う。
	CodeTicketProject = "DENY_TICKET_PROJECT_MISMATCH"
	// CodeTicketBlocked は承認済みチケット自体が信頼できない。範囲外と違い、ユーザが直すまでどこにも書けない。
	CodeTicketBlocked = "DENY_TICKET_BLOCKED"
	// CodeTicketParent は親のワークツリーに置き場以外を書こうとした。範囲を広げても通らず、子のワークツリーで作業する。
	CodeTicketParent = "DENY_TICKET_PARENT"
)

// 組み込みの判定で止めたときの、記録の rules に残す名前。範囲外（TicketRule）を使い回すと、記録の範囲外の件数に
// 混ざり、繰り返しの文も止めた理由と違う名前を言う。
const (
	RedirectLimitRule    = "builtin:redirect-limit"
	TicketRule           = "builtin:ticket-scope"
	SubagentTicketOpRule = "builtin:subagent-ticket-op" // phase.CodeSubagent
	PhaseReviewRule      = "builtin:phase-review"       // phase.CodeReview
	TicketProjectRule    = "builtin:ticket-project"     // CodeTicketProject
	TicketBlockedRule    = "builtin:ticket-blocked"     // CodeTicketBlocked
	TicketParentRule     = "builtin:ticket-parent"      // CodeTicketParent
)

const (
	head          = "[ccnavi] "
	subjectPrefix = "subject: "
	builtinPrefix = "builtin:"
	// rewritesShown は止めた文に並べる表記の数。1 つ直せば残りも同じ直し方になる。
	rewritesShown = 5
)

// rewriteCodes は書き直しを求める形ごとの理由コード（文面ファイルの識別子も同じ）。
var rewriteCodes = map[shell.Form]string{
	shell.FormBrace:       "DENY_BRACE_EXPANSION",
	shell.FormCommandName: "DENY_COMMAND_NAME_EXPANSION",
	shell.FormBackquote:   "DENY_BACKQUOTE",
	shell.FormAmbiguous:   "DENY_AMBIGUOUS_FORM",
}

// RewriteCode は書き直しを求める形で止めたときの理由コード。
func RewriteCode(form shell.Form) string { return rewriteCodes[form] }

// RewriteRule は書き直しを求める形で止めたときに、記録の rules に残す名前。
func RewriteRule(form shell.Form) string { return builtinPrefix + string(form) }

// CodeFor はルールの deny の根拠コード。対象がコマンドかパスかで分かれ、読み切れなければ CodeUncertain。
func CodeFor(tool string, degraded shell.Reason) string {
	if degraded != "" {
		return CodeUncertain
	}
	if tool == config.ToolBash {
		return CodeCommand
	}
	return CodePath
}

// FallenBack はルールファイルを読めずに組み込みの既定に戻ったことを伝える文。通した回にも返す（黙ると、ガードが
// 働いているように見えて、実際にはプロジェクトのルールを 1 件も見ていない状態が続く）。
func FallenBack(rulesPath, root string) string {
	return texts.Say(root, "NOTICE_FALLEN_BACK", texts.Values{"path": rulesPath}, nil)
}

// undeclaredTexts は言及の無い呼び出しの文の識別子。キーは（読み切れなかったか, 結末）。読み切れなかった回は
// ルールファイルに何を足しても読めるようにならないので、次の一手は書き直しになる。
var undeclaredTexts = map[bool]map[rules.Decision]string{
	false: {rules.Ask: "NOTICE_UNDECLARED__ASK", rules.Deny: "NOTICE_UNDECLARED__DENY"},
	true:  {rules.Ask: "NOTICE_UNDECLARED__UNREADABLE_ASK", rules.Deny: "NOTICE_UNDECLARED__UNREADABLE_DENY"},
}

// Undeclared はどのタイプも言及しなかった呼び出しの文。危険だとは言わない（根拠は設定の穴で、呼び出しの中身ではない）。
// verdict は judge が決めた結末（ask か deny）。結末と読み切れたかの組で次の一手が違う。表に無い結末（渡した回）は空。
// source は判定の出所。組み込みの既定に退避した回（fellBack）は、読めなかったルールファイルへ allow を足せとは言わない
// （直し方は FallenBack の文が言う）。
func Undeclared(root, tool, subject, source string, degraded shell.Reason, verdict rules.Decision, fellBack bool) string {
	id, ok := undeclaredTexts[degraded != ""][verdict]
	if !ok {
		return ""
	}
	code := CodeUndeclared
	if degraded != "" {
		code = CodeUncertain
	}
	lines := []string{head + code + " (source: " + source + ")", subjectPrefix + shorten(subject)}
	if degraded != "" {
		lines = append(lines, unreadable(root, degraded, false))
	}
	values := texts.Values{}
	if degraded == "" {
		target := "path"
		if tool == config.ToolBash {
			target = "command"
		}
		values = texts.Values{"target": target, "where": source}
	}
	what := "rules"
	if fellBack {
		what = "fell_back"
	}
	lines = append(lines, texts.Say(root, id, values, texts.Variants{"what": what}))
	if degraded != "" {
		lines = append(lines, rewriteHint(root, degraded)...)
	}
	return strings.Join(lines, "\n")
}

// Hit はルール 1 件のヒット。Runner と Inner は、ヒットしたのが実行役のコマンド（Runner）が中で実行するコマンド
// （Inner）だったときのもの（元の形でヒットしたなら空）。Quoted は引用の中から切り出したコマンドにだけヒットしたこと。
type Hit struct {
	Rule     *rules.Rule
	Tool     string
	Subject  string
	Degraded shell.Reason
	Runner   string
	Inner    string
	Quoted   bool
}

// ReasonFor はヒットしたルール 1 件を、それだけで読んで成立する理由に組む。読めなかった断りは件ごとに繰り返す
// （1 件だけが切り出されて見えても誤読させないため）。中で実行されるコマンドでヒットしたときは断りを付けない
// （適用した先は読み直したコマンドで、生の文字列ではない）。
func ReasonFor(root string, h Hit, git string) string {
	degraded := h.Degraded
	if h.Inner != "" {
		degraded = ""
	}
	code := CodeFor(h.Tool, degraded)
	if h.Rule.Decision == rules.Ask {
		// 拒否と確認で同じコードを返すと、止まったのか聞かれているのかを文から推し量ることになる。
		code = CodeRuleAsk
	}
	source := "rule: " + rules.RecordName(h.Rule)
	if h.Rule.ID == useronly.TicketApprovalRuleID {
		// 組み込み。ルールファイルには無いので、そこを探させない。
		code = useronly.CodeTicketApproval
		source = "組み込みのルール: " + h.Rule.ID + " (" + env.GuardTicketApproval + ")"
	}
	lines := []string{head + code + " (" + source + ")", subjectPrefix + shorten(h.Subject)}
	if h.Inner != "" {
		lines = append(lines, ranBy(root, h.Runner, h.Inner))
	}
	if degraded != "" {
		lines = append(lines, unreadable(root, degraded, true))
		lines = append(lines, rewriteHint(root, degraded)...)
	}
	lines = append(lines, texts.Spoken(h.Rule))
	if h.Quoted {
		lines = append(lines, insideQuotes(root, git))
	}
	return strings.Join(lines, "\n")
}

// askAlternative は非対話で確認が自動で拒否になるとき、ルールごとに分ける文（bare id → 対象に含まれる語の式と識別子）。
// 式が nil なら、そのルールにヒットした回に必ずその文を出す。ask のルールは message を書けず、additionalContext は
// 自動拒否のときに届かないので、ここに置く。direct-worktree-confirm の `--adopt` は、確認が通ってもスクリプトが断るので、
// 「了承されたら打ち直す」だけを言うと通らない案内になる。
var askAlternative = map[string]struct {
	needle *pyre.Regexp
	id     string
}{
	"prefer-webfetch":         {nil, "RULE_ASK__PREFER_WEBFETCH"},
	"direct-worktree-confirm": {pyre.MustCompile(`\s--adopt(?:\s|=|$)`, pyre.IgnoreCase), "RULE_ASK__ADOPT"},
}

// AskHint は確認（ask）の理由に足す説明の行。非対話のときだけ返す（確認に答える人がいないので ask は自動で拒否され、
// ルール名と対象だけでは確認待ちか止められたかが分からない）。確認として返す回にだけ足す。
func AskHint(root, bareID, subject string) []string {
	if !ccnavimd.Unattended() {
		return nil
	}
	id := CodeRuleAsk
	if alt, ok := askAlternative[bareID]; ok && (alt.needle == nil || alt.needle.Search(subject)) {
		id = alt.id
	}
	return ospath.Splitlines(texts.Say(root, id, texts.Values{"subject": shorten(subject)}, nil))
}

// ranBy は実行役のコマンドが中で実行するコマンドにヒットしたことを言う 1 行。元の形（`env rm -f …`）には、ルールが
// 書いた語が出てこない。`cd` で移った先から見たパスにヒットしたときも同じで、どこへ書こうとしているかをパスで示す。
func ranBy(root, runner, inner string) string {
	note := "runner"
	if runner == shell.Moved {
		note = "moved"
	}
	return texts.Say(root, "NOTE_RAN_BY", texts.Values{"runner": oneLine(runner), "inner": oneLine(inner)}, texts.Variants{"note": note})
}

// oneLine はコマンドを文に載せる形にする。目印を空白に戻し、1 行にまとめて上限で切る。
func oneLine(text string) string {
	return shorten(strings.NewReplacer(shell.Sep, " ", shell.WordSep, " ").Replace(text))
}

// shorten は文に載せる対象を 1 行にまとめ、上限で切って切った字数をつける。残りはまとめた後の長さで数える
// （まとめる前の長さで数えると、改行や空白の続きまで残りに入り、見えていない字数を多く言う）。
func shorten(text string) string {
	shown := strings.Join(strings.FieldsFunc(text, ospath.IsSpace), " ")
	if n := len([]rune(shown)); n > texts.ShownSubjectLimit {
		shown = pyfmt.Head(shown, texts.ShownSubjectLimit) + fmt.Sprintf("…(+%d)", n-texts.ShownSubjectLimit)
	}
	return shown
}

// insideQuotes は引用の中から切り出したコマンドにルールがヒットしたときの断り。書いた側は文字を書いただけのつもりで
// いるので、文字として渡す方法を名指しする（知らなければ同じ形を書き直しては止まる）。
func insideQuotes(root, git string) string {
	note := "plain"
	if git != "" {
		note = "git"
	}
	return texts.Say(root, "NOTE_INSIDE_QUOTES", texts.Values{"git": git}, texts.Variants{"note": note})
}

// unreadableTexts は読めなかった理由ごとの断りの識別子。表に無い理由は OTHER。
var unreadableTexts = map[shell.Reason]string{
	shell.ReasonUnterminated:      "NOTE_UNREADABLE__UNTERMINATED",
	shell.ReasonTakenAsCode:       "NOTE_UNREADABLE__TAKEN_AS_CODE",
	shell.ReasonUnterminatedSubst: "NOTE_UNREADABLE__UNTERMINATED_SUBST",
	shell.ReasonAnsiC:             "NOTE_UNREADABLE__ANSI_C",
}

// unreadable はコマンドを読めないまま出した 1 件の断り。「禁止されたコマンドを実行した」と「読めない文字列のどこかに
// その語がある」では次にやることが違う。matched はルールにヒットした 1 件につけるか（言及の無い回は指す先が無いので、
// 読めなかったことだけを言う）。
func unreadable(root string, reason shell.Reason, matched bool) string {
	id, ok := unreadableTexts[reason]
	if !ok {
		id = "NOTE_UNREADABLE__OTHER"
	}
	note := "raw"
	if matched {
		note = "matched"
	}
	return texts.Say(root, id, nil, texts.Variants{"note": note})
}

// rewriteHintTexts は読めなかった理由ごとの書き換え例の識別子。例の無い理由は引かない。
var rewriteHintTexts = map[shell.Reason]string{
	shell.ReasonTakenAsCode:  "NOTE_REWRITE_HINT__TAKEN_AS_CODE",
	shell.ReasonAnsiC:        "NOTE_REWRITE_HINT__ANSI_C",
	shell.ReasonUnterminated: "NOTE_REWRITE_HINT__UNTERMINATED",
}

// rewriteHint は書き換え例の行。非対話のセッションでは確認に答える人がいないので、書き直すほかないことを足す。
func rewriteHint(root string, reason shell.Reason) []string {
	id, ok := rewriteHintTexts[reason]
	if !ok {
		return nil
	}
	lines := ospath.Splitlines(texts.Say(root, id, texts.Values{"runnee": strings.Join(shell.ReadonlyRunnees(), " ")}, nil))
	if ccnavimd.Unattended() {
		lines = append(lines, ospath.Splitlines(texts.Say(root, "NOTE_REWRITE_HINT__UNATTENDED", nil, nil))...)
	}
	return lines
}

// SubagentForbidden はサブエージェントに許さない操作を止めた文。runner・inner は Hit と同じ。
func SubagentForbidden(root, subject, runner, inner string) string {
	lines := []string{head + phase.CodeSubagent, subjectPrefix + shorten(subject)}
	if inner != "" {
		lines = append(lines, ranBy(root, runner, inner))
	}
	lines = append(lines, texts.Say(root, "DENY_SUBAGENT_TICKET_OP", texts.Values{}, nil))
	return strings.Join(lines, "\n")
}

// BuiltinRefusal は組み込みの判定で止めた文。ruleID は記録の rules に残す名前と同じにして、受け取った文から記録の行を引ける
// ようにする。並びは ReasonFor と同じ。
func BuiltinRefusal(code, ruleID, subject, text string) string {
	return strings.Join([]string{head + code + " (組み込みのルール: " + ruleID + ")", subjectPrefix + shorten(subject), text}, "\n")
}

// InternalError は ccnavi 自身の不具合で判定や検査を終えられなかったときに、モデルへ返す文。呼び出しの中身が原因では
// ないので書き直し方は言えず、直せるのはユーザだと言う。passed は通した回か。
func InternalError(root, code string, event hookio.Event, kind, logPath string, passed bool) string {
	what := "stopped"
	switch {
	case event == hookio.PostToolUse:
		what = "post_tool_use"
	case passed:
		what = "passed"
	}
	return head + code + "\n" + texts.Say(root, "DENY_INTERNAL_ERROR", texts.Values{"kind": kind, "log_path": logPath}, texts.Variants{"what": what})
}

// InternalErrorNotice は ccnavi 自身の不具合で、ツールの呼び出しを持たないイベントの処理を終えられなかったときに、
// ユーザへ見せる文。止めていないことと診断ログの場所を言う。
func InternalErrorNotice(code string, event hookio.Event, kind, logPath string) string {
	return fmt.Sprintf("[ccnavi] %s: %s の処理中に ccnavi の内部の不具合が起き（%s）、そこでの処理を飛ばしました。"+
		"止めたものはありません。詳細は診断ログ %s にあります。", code, event, kind, logPath)
}

// payloadStopped は payload を読めずに止めた（exit 2）とき、イベントごとの何が起きたかと次の一手。exit 2 の意味と
// 標準エラーの宛先はイベントで違う。
var payloadStopped = map[hookio.Event][2]string{
	hookio.PreToolUse:       {"pre_tool_use", "check"},
	hookio.PostToolUse:      {"post_tool_use", "check"},
	hookio.UserPromptSubmit: {"user_prompt_submit", "prompt"},
	hookio.Stop:             {"stop", "stop"},
	hookio.SubagentStop:     {"subagent_stop", "subagent"},
}

// PayloadUnusable は hook の入力を読めなかったときの文。直せるのは hook の登録か版を合わせるユーザだけ。
// ツールの呼び出しを判定するイベント（とイベントすら読めなかった回。event が空）は dry-run でも止めるが、
// ほかのイベントは dry-run なら通すので「止めた」と言わない。
func PayloadUnusable(root, code, problem string, event hookio.Event, mode config.Mode) string {
	variants := texts.Variants{"what": "dry_run", "next": "check"}
	if mode != config.DryRun || event == "" || event == hookio.PreToolUse {
		step, ok := payloadStopped[event]
		if !ok {
			step = [2]string{"other", "check"}
		}
		variants = texts.Variants{"what": step[0], "next": step[1]}
	}
	shown := string(event)
	if shown == "" {
		shown = "hook"
	}
	return head + code + "\n" + texts.Say(root, "DENY_PAYLOAD_UNUSABLE", texts.Values{"problem": problem, "event": shown}, variants)
}

// DeadlineExceeded は判定が期限に届かなかったときの文。照合が長引くのはコマンドが長いか `>` のような形が多いときで、
// 短く分ければ通る見込みがある。期限を見るのは PreToolUse だけで、宛先は呼び出しを書いたモデル。passed は dry-run で
// 安全と分かる読み取りを通した回。
func DeadlineExceeded(root, code string, deadline time.Duration, passed bool) string {
	what := "stopped"
	if passed {
		what = "passed"
	}
	return head + code + "\n" + texts.Say(root, "DENY_DEADLINE_EXCEEDED",
		texts.Values{"seconds": pyfmt.FormatG(deadline.Seconds())}, texts.Variants{"what": what})
}

// Rewrite は書き直しを求める形を止めた文（形ごとに 1 件）。ルールにヒットしたのではないので、禁止された操作をしたとは
// 言わない。書き直す方法は必ずあり、名指ししないと同じ形を書き直しては止まる。found は見つけた表記。
func Rewrite(root, subject string, form shell.Form, found []string, git string) string {
	shown := make([]string, 0, rewritesShown)
	for _, text := range found[:min(len(found), rewritesShown)] {
		shown = append(shown, "`"+oneLine(text)+"`")
	}
	listed := strings.Join(shown, ", ")
	if len(found) > rewritesShown {
		listed += fmt.Sprintf(" (+%d)", len(found)-rewritesShown)
	}
	next := "plain"
	if git != "" {
		next = "git"
	}
	values := texts.Values{"found": listed}
	if form == shell.FormBackquote {
		values["git"] = git
	}
	code := rewriteCodes[form]
	text := texts.Say(root, code, values, texts.Variants{"next": next})
	return strings.Join([]string{head + code, subjectPrefix + shorten(subject), text}, "\n")
}
