package cli

import (
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/entry/diagnose"
	"ccnavi/internal/entry/lint"
	"ccnavi/internal/entry/shask"
	"ccnavi/internal/entry/suggest"
	"ccnavi/internal/entry/version"
	"ccnavi/internal/infra/argparse"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/core"
	"ccnavi/internal/tickets/lifecycle/branch"
	"ccnavi/internal/tickets/lifecycle/wapmerge"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/model/via"
)

// ticketControlOff はチケット制御が切られているときの断り。
var ticketControlOff = fmt.Sprintf("ccnavi: チケット制御が無効になっています（%s）。\n", env.TicketControl)

// rootOf は `--root`（sh が渡す）か、無ければ上へ辿って見つけたワークスペースルート。
func rootOf(a opts) string {
	if r, ok := a.Namespace["root"].(string); ok {
		return r
	}
	return defaultRoot()
}

// cwdOf は `--cwd`（sh が渡す）か、無ければ作業ディレクトリ。
func cwdOf(a opts) string {
	if c := a.Str("cwd"); c != "" {
		return c
	}
	wd, _ := ospath.Getwd()
	return wd
}

// parsed は読めた引数を経路ごとの処理へ振り分ける。payload を読まない経路を順に見て、どれにも当たらなければ hook の
// 判定として扱う。経路を足したら hookRoute にも足す。
func parsed(in *input, stdout, stderr io.Writer, parser *argparse.Parser, a opts, unmarked *useronly.Verb) int {
	// 版だけを出す。受け付けるオプションの一覧は出さない。
	if a.Truthy("version") {
		return version.Report(stdout, a.Truthy("json"))
	}
	if !oneWrapperFlagEach(stderr, a) {
		return config.ExitError
	}
	if templatesAsked(a) {
		if refused := templatesUsage(a); refused != "" {
			fmt.Fprintf(stderr, "ccnavi: %s\n", refused)
			return config.ExitError
		}
	}
	if a.Truthy("learnings") {
		if refused := learningsUsage(a); refused != "" {
			fmt.Fprintf(stderr, "ccnavi: %s\n", refused)
			return config.ExitError
		}
	}
	// `--docs` に添えたほかの経路のフラグは、気づかれないまま無視せずに止める。置き場の差し替えはこの後で無視され、
	// `--ticket-control` などは設定に重ねられるので、その前に見る。
	if a.Truthy("docs") {
		if refused := notWithDocsGiven(a, ""); refused != "" {
			fmt.Fprintf(stderr, "ccnavi: --docs は %s と同時に指定できない\n", refused)
			return config.ExitError
		}
	}

	root := rootOf(a)
	conf, problems, crashed, ok := loadSettings(stdout, stderr, root, a)
	if !ok {
		return crashed
	}
	return routes(in, stdout, stderr, parser, a, unmarked, root, conf, problems)
}

// loadSettings は設定を読む。読む途中で落ちたら、hook の経路だけは受け皿で終える（payload はまだ読んでいないので
// イベントが分からず、PreToolUse と同じく止める）。ほかの経路は、打った人が見られるので落ちたまま伝える。
func loadSettings(stdout, stderr io.Writer, root string, a opts) (conf *config.Settings, problems []string, code int, ok bool) {
	if hookRoute(a) {
		defer onPanic(func(_ any, f failure) {
			code, ok = hookCrashed(stdout, stderr, unloaded(root), root, &hookSoFar{mode: config.Enable}, f), false
		})
	}
	conf, problems = config.Load(root)
	return conf, problems, 0, true
}

// prepare はフラグを設定に重ね、切り替えを解く。診断の外では置き場の差し替えを捨てる（hook とサブコマンドに
// 差し替えの手段を残すと、設定を保存せずに緩める経路になる）。
func prepare(stderr io.Writer, a opts, conf *config.Settings) {
	if !diagnosing(a) {
		dropOutsideDiagnosis(stderr, a)
	}
	override(conf, a)
	// フラグは設定ファイルより強い。書かれた値のほうも合わせて差し替える。取れない語は読めない値と同じく enable に
	// する（--lint はそれを error にする）。
	if flag := a.Str("guard_ticket_approval"); flag != "" {
		conf.GuardTicketApprovalDeclared = flag
	}
	conf.GuardTicketApproval = resolveGate(stderr, a.Str("guard_ticket_approval"), conf.GuardTicketApproval,
		env.GuardTicketApproval)
}

// prepareTicketControl はチケット制御の切り替えを解く。切ったつもりで値を書き誤った設定は判定では有効なままになり、
// --lint が error で名指しする。
func prepareTicketControl(stderr io.Writer, a opts, conf *config.Settings) {
	if flag := a.Str("ticket_control"); flag != "" {
		conf.TicketControlDeclared = flag
	}
	conf.TicketControl = resolveGate(stderr, a.Str("ticket_control"), conf.TicketControl, env.TicketControl)
}

func routes(in *input, stdout, stderr io.Writer, parser *argparse.Parser, a opts, unmarked *useronly.Verb,
	root string, conf *config.Settings, problems []string) int {
	isDiagnosis := diagnosing(a)
	prepare(stderr, a, conf)
	// 入口の印が無い `user <操作>` は実行ファイルを直に打った形。ユーザが打つ入口は sh で、承認のあとの後始末は
	// そこが行う。テストと端末の無い実行環境は、端末要求と同じ切り替えで通す。
	if unmarked != nil && conf.GuardTicketApproval != string(config.Disable) {
		script := config.ScriptCommand(root, useronly.UserScriptPrefix+unmarked.Script+".sh")
		fmt.Fprintf(stderr, "ccnavi: %s\n", useronly.DirectRefusal(*unmarked, script))
		return config.ExitBlock
	}
	prepareTicketControl(stderr, a, conf)

	// 取り込みとミラーは、書くとき（`--yes`）にターミナルの前のユーザを確かめる。ボードの経路は `--expect` の一致と
	// hook の止めに置く。
	switch {
	case a.Truthy("handoff"):
		return runHandoff(in, stdout, stderr, parser, conf, root, a)
	case a.Truthy("mirror"):
		return runMirror(in, stdout, stderr, parser, conf, root, a)
	case a.Truthy("templates"):
		return runTemplates(stdout, stderr, conf, root, a)
	case a.Truthy("learnings"):
		return runLearnings(stdout, stderr, conf, root, a)
	case a.Truthy("docs"):
		return runDocs(stdout, stderr, conf, root, a)
	}
	// 絞り込みのフラグは `--docs` でだけ読む。無視して先へ進めると `ticket start X --limit 3` のような打ち間違いが通る。
	if stray := docsFlagsGiven(a); len(stray) > 0 {
		for _, flag := range stray {
			fmt.Fprintf(stderr, docsOnly, flag)
		}
		return config.ExitError
	}

	// 1 つのレイヤーだけを差し替える形。使われる経路は共通レイヤーの 3 本と同じ。
	for _, s := range []struct {
		flag, dest string
		swaps      map[string]string
	}{
		{"--project-rules-file", "project_rules_file", conf.ProjectRulesFiles},
		{"--project-phases-file", "project_phases_file", conf.ProjectPhasesFiles},
		{"--project-risk-file", "project_risk_file", conf.ProjectRiskFiles},
	} {
		value := a.Str(s.dest)
		if value == "" {
			continue
		}
		if !isDiagnosis {
			fmt.Fprintf(stderr, diagnosisOnly, s.flag)
			continue
		}
		name, path, ok := strings.Cut(value, "=")
		if !ok || name == "" || path == "" {
			fmt.Fprintf(stderr, "ccnavi: %s は <名前>=<パス> の形で指定してください。\n", s.flag)
			return config.ExitError
		}
		s.swaps[name] = ospath.Abspath(path)
	}
	// フローの確かめは `--lint` だけが読む。
	flow := a.Str("flow")
	if flow != "" {
		switch {
		case !isDiagnosis:
			fmt.Fprintf(stderr, diagnosisOnly, "--flow")
			flow = ""
		case !a.Truthy("lint"):
			fmt.Fprintln(stderr, "ccnavi: --flow は --lint でだけ読む")
			flow = ""
		default:
			flow = ospath.Abspath(flow)
		}
	}

	// 検証だけを行う経路。判定にとっては読み飛ばした設定の報告でしかない苦情が、検証にとっては結論そのものになる。
	if a.Truthy("lint") {
		return lint.Report(stdout, lint.Options{Root: root, Conf: conf, Notes: problems, Mode: a.Str("mode"),
			RestoreIfDeny: a.Str("restore_if_deny"), GuardCoreFiles: a.Str("guard_core_files"), JSON: a.Truthy("json"),
			Flow: flow, Compat: version.Compat})
	}
	if code, done := diagnosis(stdout, stderr, conf, root, a); done {
		return code
	}
	if a.Truthy("prune") {
		return runPrune(in, stdout, stderr, conf, root, a.Truthy("preview"))
	}
	if code, done := planOrder(stdout, stderr, conf, root, a); done {
		return code
	}
	if a.Truthy("agree") {
		return agree(in, stdout, stderr, conf, root, a)
	}
	if code, done := asked(in, stdout, stderr, conf, root, a); done {
		return code
	}
	if len(a.command()) > 0 || !a.IsNone("reviewed") || a.Truthy("close_early") {
		// 残った指摘を見せるだけの `--preview` と、オーバーレイで押した `--yes` はターミナルを求めない。`--yes` を守るのは、
		// 見せた指摘のダイジェストの一致と、シェルから打つ形を止める組み込みの deny。
		board := !a.IsNone("reviewed") && a.Truthy("accept_unresolved") &&
			(a.Truthy("preview") || a.Truthy("yes") || a.Truthy("check"))
		if !a.IsNone("reviewed") && !board && !fromTerminal(in, conf, stderr, useronly.OpReviewed) {
			return config.ExitError
		}
		return operate(in, stdout, stderr, conf, root, a)
	}
	return runHook(in, stdout, stderr, conf, problems, root, a)
}

// diagnosis は診断の経路（`--test`・`--test-expectations`・`--suggest`・`--explain`）。どれも payload を読まず、判定を
// 実行にも記録にも繋げない。判定が読む切り替えは hook と同じ関数で解いてから渡す。
func diagnosis(stdout, stderr io.Writer, conf *config.Settings, root string, a opts) (int, bool) {
	test := a.Strings("test")
	explain := a.Truthy("explain")
	asJSON := a.Truthy("json")
	if test != nil || a.Str("test_expectations") != "" || explain && !asJSON {
		resolveSwitches(stderr, conf, a)
	}
	switch {
	case test != nil:
		call := diagnose.Call{Cwd: a.Str("cwd"), PermissionMode: a.Str("permission_mode"), AgentID: a.Str("agent_id")}
		code, err := diagnose.Test(stdout, stderr, conf, root, test[0], test[1], call, asJSON)
		return finish(stderr, code, err), true
	case a.Str("test_expectations") != "":
		code, err := diagnose.TestExpectations(stdout, stderr, conf, root, a.Str("test_expectations"), asJSON)
		return finish(stderr, code, err), true
	case a.Truthy("suggest"):
		code, err := suggest.Report(stdout, conf, root, asJSON)
		return finish(stderr, code, err), true
	case explain && asJSON:
		code, err := diagnose.ExplainJSON(stdout, stderr, conf, root, a.Str("mode"), a.Str("ticket_control"))
		return finish(stderr, code, err), true
	case explain:
		code, err := diagnose.Explain(stdout, stderr, conf, root)
		return finish(stderr, code, err), true
	}
	return 0, false
}

// planOrder は親の提案の計画を書き換える補助の経路。`--agree` のターミナルの確かめより手前で分ける。書くのは
// 承認待ちの親の提案（エージェントも書ける置き場）だけで、承認はしない。
func planOrder(stdout, stderr io.Writer, conf *config.Settings, root string, a opts) (int, bool) {
	reordering := !a.IsNone("order") || a.Truthy("write") || !a.IsNone("expect")
	if a.IsNone("plan_order") && !a.Truthy("fill_phases") && !reordering {
		return 0, false
	}
	if problem := planOrderUsage(a, reordering); problem != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", problem)
		return config.ExitError, true
	}
	if !conf.TicketsEnabled() {
		io.WriteString(stderr, ticketControlOff)
		return config.ExitError, true
	}
	name := ospath.Strip(a.Str("plan_order"))
	if a.Truthy("fill_phases") {
		return fillPhases(stdout, stderr, conf, root, name), true
	}
	var order *string
	if !a.IsNone("order") {
		text := a.Str("order")
		order = &text
	}
	return reorder(stdout, stderr, conf, root, name, a.Truthy("json"), order, a.Truthy("write"), a.Str("expect")), true
}

// planOrderUsage は `--plan-order` の組み合わせの誤り。
func planOrderUsage(a opts, reordering bool) string {
	switch {
	case a.Truthy("agree"):
		return "--plan-order は --agree と同時に指定できない（承認の外の補助）"
	case a.IsNone("plan_order"):
		if a.Truthy("fill_phases") {
			return "--fill-phases は --plan-order <親> と一緒に使う"
		}
		return "--order・--write・--expect は --plan-order <親> と一緒に使う"
	case a.Truthy("fill_phases"):
		if reordering || a.Truthy("json") {
			return "--fill-phases は --json・--order・--write・--expect と同時に指定できない"
		}
		return ""
	case a.Truthy("write"):
		if a.IsNone("order") {
			return "--write は --order <JSON>（元の番号での直接の先行）と一緒に使う"
		}
		if a.IsNone("expect") {
			return "--write は --expect <ハッシュ>（--plan-order <親> --json が返す source_sha）と一緒に使う"
		}
		return ""
	case !a.IsNone("expect"):
		return "--expect は --write と一緒に使う"
	case !a.Truthy("json"):
		return "--plan-order <親> は --json（読むだけ。--order で振り直した答え）か、" +
			"--order <JSON> --expect <ハッシュ> --write（書き戻す）か、" +
			"--fill-phases（計画が使う定義だけを phases.yml から親の提案の phases: にコピーする）と一緒に使う"
	}
	return ""
}

// agree は承認の経路。ユーザがターミナルから打つもので、判定を 1 度も通らない（承認は、これから判定に使われる範囲に
// ついての合意になる）。
func agree(in *input, stdout, stderr io.Writer, conf *config.Settings, root string, a opts) int {
	if !conf.TicketsEnabled() {
		io.WriteString(stderr, ticketControlOff)
		return config.ExitError
	}
	preview, yes := a.Truthy("preview"), a.Str("yes")
	only := a.command()
	if preview && yes != "" {
		fmt.Fprintln(stderr, "ccnavi: --preview と --yes は同時に指定できません。")
		return config.ExitError
	}
	// 確かめるだけの枝は `--preview` と一緒に使う。組み込みの deny が免除するのは `--preview` の付いた `--agree` だけ
	// なので、単独で打てる形にすると、エージェントが打てないものを案内することになる。
	if a.Truthy("verify") && !preview {
		fmt.Fprintln(stderr, "ccnavi: --verify は --agree --preview と一緒に指定してください。")
		return config.ExitError
	}
	// 答えは 0（はい）と 3（いいえ）で、使い方と設定の誤りの 1 とは分ける。
	if a.Truthy("verify") {
		return core.Verify(stdout, stderr, conf, root, a.Truthy("json"), only)
	}
	if preview {
		return okIf(core.Preview(stdout, stderr, conf, root, a.Truthy("json"), only) == 0)
	}
	// 拡張のオーバーレイでユーザが押した承認。ターミナルの確認の代わりに、見せた一覧と今の一覧が同じであることを
	// 求める。エージェントが Bash で打つ形は組み込みの deny が止める。
	if yes != "" {
		via.SetRoute(via.Board)
		code, err := core.ApproveYes(stdout, stderr, conf, root, strings.Split(yes, ","), a.Truthy("json"), only,
			a.Str("digest"))
		if err != nil {
			return finish(stderr, code, err)
		}
		return okIf(code == 0)
	}
	if !fromTerminal(in, conf, stderr, useronly.OpApprove) {
		return config.ExitError
	}
	via.SetRoute(via.Terminal)
	code, err := core.Approve(in.buf, stdout, stderr, conf, root, only, a.Str("placed_out"))
	if err != nil {
		return finish(stderr, code, err)
	}
	if code == config.ExitPartial {
		return config.ExitPartial
	}
	return okIf(code == 0)
}

// asked は sh が聞くこと（操作記録・取り込み・文面・ツリー・write-and-push・ブランチ・放置・Stop の判定役）。
// 当たらなければ done が偽。
func asked(in *input, stdout, stderr io.Writer, conf *config.Settings, root string, a opts) (int, bool) {
	cmd := a.command()
	switch {
	case len(cmd) == 0:
		return 0, false
	case cmd[0] == "ops":
		// 操作記録の読み書き。チケット制御の有無に依らない。
		return opsCommand(stdout, stderr, conf, cmd[1:]), true
	case slices.Equal(cmd, []string{"sync", "paths"}):
		return okIf(syncPaths(stdout, conf) == 0), true
	case slices.Equal(cmd, []string{"sync", "retired"}):
		return okIf(syncRetired(stdout, conf) == 0), true
	case (len(cmd) == 3 || len(cmd) == 4) && a.head("sync", "check"):
		repo := ""
		if len(cmd) == 4 {
			repo = cmd[3]
		}
		return okIf(syncCheck(stdout, stderr, root, conf, cmd[2], repo) == 0), true
	case cmd[0] == "text":
		return runText(stdout, stderr, root, cmd[1:]), true
	}
	if code, matched := shask.Answer(stdout, stderr, root, conf, cmd); matched {
		return code, true
	}
	switch {
	case len(cmd) == 6 && a.head("sync", "tidy"):
		return okIf(syncTidy(stdout, stderr, root, conf, cwdOf(a), cmd[2], cmd[3], cmd[4], cmd[5]) == 0), true
	case len(cmd) == 3 && a.head("write-and-push", "family"):
		if !ids.IsValidFamily(cmd[2]) {
			fmt.Fprintf(stderr, "ccnavi: write-and-push family の %s は有効な識別子の形式ではありません。\n", pyfmt.Repr(cmd[2]))
			return config.ExitError, true
		}
		return okIf(wap.Family(stdout, conf, root, cmd[2]) == 0), true
	case (len(cmd) == 3 || len(cmd) == 4) && a.head("write-and-push", "sort"):
		since := ""
		if len(cmd) == 4 {
			since = cmd[3]
		}
		if !ids.IsValidFamily(cmd[2]) {
			fmt.Fprintf(stderr, "ccnavi: write-and-push sort の %s は有効な識別子の形式ではありません。\n", pyfmt.Repr(cmd[2]))
			return config.ExitError, true
		}
		if since != "" && !revisionOK(since) {
			fmt.Fprintf(stderr, "ccnavi: write-and-push sort の版 %s は読めない\n", pyfmt.Repr(since))
			return config.ExitError, true
		}
		return okIf(wap.Sort(stdout, stderr, conf, root, cmd[2], since) == 0), true
	case len(cmd) >= 3 && a.head("write-and-push", "merge"):
		if !ids.IsValidFamily(cmd[2]) || len(cmd) != 3 {
			fmt.Fprintf(stderr, "ccnavi: write-and-push merge の %s は有効な識別子の形式ではありません。\n", pyfmt.Repr(cmd[2]))
			return config.ExitError, true
		}
		return okIf(wapmerge.Plan(stdout, conf, root, cmd[2]) == 0), true
	case len(cmd) == 3 && cmd[0] == "branches":
		// ホストの結果は sh が取ってきて `--result` で渡す。
		return okIf(branch.Report(stdout, stderr, conf, root, cwdOf(a), cmd[1], cmd[2], a.Str("result"),
			a.Truthy("json")) == 0), true
	case cmd[0] == "idle":
		return runIdle(in, stdout, stderr, conf, cmd), true
	case cmd[0] == "stop-review":
		return runStopReview(in, stdout, stderr, conf, cmd, root), true
	}
	return 0, false
}
