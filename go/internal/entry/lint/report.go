package lint

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/hook/judge"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/tickets/flow"
)

// JSONVersion は `--lint --json` の形の版。欄を足すだけなら上げない。欄の意味や名前を変えたら上げ、読む側
// （VS Code 拡張）は違う版を「読めない」として扱う。
const JSONVersion = 1

// 苦情の出どころの表記。
const (
	whereSettings   = "(settings)"
	whereMode       = "(mode)"
	whereRestore    = "(restore)"
	whereUnwatched  = "(unwatched)"
	whereTicket     = "(ticket)"
	whereLearning   = "(learning)"
	whereProject    = "(project)"
	whereProjects   = "(projects)"
	whereVersion    = "(version)"
	whereCcnaviMD   = "(ccnavi-md)"
	whereStopReview = "(stop-review)"
	whereLayers     = "(layers)"
	// whereFlow は `--lint --flow` の苦情の場所。VS Code 拡張のフロー編集画面はこの場所の苦情だけを読む。
	whereFlow = "(flow)"
)

// 判定の側が標準エラーに書く文の頭。深刻度を頭に付けるので外す。
const complaintHead = "ccnavi: "

// Options は `--lint` に渡すもの。値は CLI が読んで解いたもの。
type Options struct {
	Root string
	Conf *config.Settings
	// Notes は設定の解決が出した苦情（深刻度を持たない文）。
	Notes []string
	// Mode・RestoreIfDeny・GuardCoreFiles は --mode・--restore-if-deny・--guard-core-files の値（無ければ空）。
	Mode           string
	RestoreIfDeny  string
	GuardCoreFiles string
	JSON           bool
	// Flow は --flow で渡した子のフローのファイル（絶対パス。無ければ空）。
	Flow string
	// Compat はこの実行ファイルの互換の版（version.Compat）。
	Compat int
}

// 戻す働きの 2 つが、切られている・予行になっているときに言うこと。止めない 2 つの値それぞれに文がある。
var (
	coreFilesVoice = map[config.Mode]string{
		config.Disable: "ccnavi 自身の設定ファイル（.claude/settings*.json と、共通レイヤー・自身のレイヤー・" +
			"プロジェクトのレイヤーそれぞれの設定 3 本）のバックアップを取らず、書き換えられても戻さない。" +
			"ふだん実行前に追加している組み込みの deny（実行ファイル・ccnavi ディレクトリ・共通レイヤーの 3 本）も" +
			"追加しないので、ワークツリー側のレイヤーの設定は、ルールファイルが名指ししていなければ書き込める",
		config.DryRun: "ccnavi 自身の設定ファイルが書き換えられても戻さない（戻すはずだったことを報告するだけ）。" +
			"実行前の deny は追加したままなので、実行前に止める働きは有効なまま",
	}
	restoreVoice = map[config.Mode]string{
		config.Disable: "`deny` と宣言した場所が副作用で変わっても戻さない",
		config.DryRun:  "`deny` と宣言した場所が副作用で変わっても戻さない（戻すはずだったことを報告するだけ）",
	}
)

// Report は検証の結果を stdout に書き、error が 1 件でもあれば config.ExitError を返す。JSON なら
// docs/11_requirements/json-shapes.md「lint の JSON」の形で 1 つの JSON を書く（拡張との契約）。
//
// モードと戻す働きの解決は判定と同じ関数に任せる。別に書くと、検証は通ったのに実運用では違う値になる。
// 苦情は受け皿で拾い、出どころを取り違えないよう受け皿を分けて深刻度を付け直す。
func Report(stdout io.Writer, o Options) int {
	conf, root := o.Conf, o.Root
	var complaints strings.Builder
	mode := config.ResolveMode(&complaints, o.Mode, conf)

	var said strings.Builder
	restoreIfDeny := selfguard.Resolve(&said, o.RestoreIfDeny, conf.RestoreIfDeny, env.RestoreIfDeny, selfguard.Choices)
	guardCoreFiles := selfguard.Resolve(&said, o.GuardCoreFiles, conf.GuardCoreFiles, env.GuardCoreFiles, selfguard.Choices)
	var unwatchedSaid strings.Builder
	guardUnwatched := selfguard.Resolve(&unwatchedSaid, "", conf.GuardUnwatched, env.GuardUnwatched, selfguard.GateChoices)

	problems := collect(conf, root, o.Notes, mode, complaints.String(), o.Compat)
	var read flowRead
	var catalog flow.Catalog
	if o.Flow != "" {
		catalog = flow.LoadCatalog(root)
		var found []rules.Problem
		found, read = flowProblems(o.Flow, root, &catalog)
		problems = append(problems, found...)
	}
	problems = append(problems, saidLines(said.String(), whereRestore)...)
	problems = append(problems, saidLines(unwatchedSaid.String(), whereUnwatched)...)
	problems = append(problems, switchProblems(conf, guardUnwatched)...)
	// 戻す働きの 2 つも problems に入れる。入らないと --json を読む側からは「揃っている」と見える。
	problems = append(problems, gate(env.GuardCoreFiles, guardCoreFiles, mode, coreFilesVoice)...)
	problems = append(problems, gate(env.RestoreIfDeny, restoreIfDeny, mode, restoreVoice)...)
	problems = append(problems, stopReviewProblems(root, conf)...)

	counts := map[rules.Severity]int{}
	for _, p := range problems {
		counts[p.Severity]++
	}
	code := config.ExitOK
	if counts[rules.Error] > 0 {
		code = config.ExitError
	}
	if o.JSON {
		writeJSON(stdout, o, mode, problems, counts, read, catalog)
		return code
	}

	w := func(format string, a ...any) { fmt.Fprintf(stdout, format, a...) }
	w("ccnavi: 設定を検証する\n")
	w("  ルール: %s\n", conf.Rules)
	if o.Flow != "" {
		w("  フロー: %s\n", o.Flow)
	}
	w("  deny の場所を戻す: %s\n", shown(restoreIfDeny, mode))
	w("  コアファイルを守る: %s\n", shown(guardCoreFiles, mode))
	w("  確認できる者が居ないモードで守る: %s\n", guardUnwatched)
	w("  チケット制御: %s\n", conf.TicketControl)
	learning := config.Disable
	if conf.LearningEnabled() {
		learning = config.Enable
	}
	w("  学びの蓄積: %s\n", learning)
	if conf.TicketsEnabled() {
		w("  チケットの承認の経路を守る: %s\n", conf.GuardTicketApproval)
	}
	// 環境変数はこの起動が受け取ったもので、セッションが受け取るものではない。どちらを見た結果かを明示する。
	w("  モード: %s（この起動の環境から解決したもの）\n", mode)
	for _, p := range problems {
		w("%s\n", p)
	}
	w("error %d 件、warn %d 件、info %d 件\n", counts[rules.Error], counts[rules.Warn], counts[rules.Info])
	return code
}

func writeJSON(stdout io.Writer, o Options, mode config.Mode, problems []rules.Problem, counts map[rules.Severity]int,
	read flowRead, catalog flow.Catalog) {
	projects := []any{}
	for _, t := range tree.Projects(o.Conf.Projects) {
		projects = append(projects, t.Name)
	}
	listed := []any{}
	for _, p := range problems {
		item := pyjson.NewObject("severity", string(p.Severity), "where", p.Rule, "detail", p.Detail)
		if p.Kind != "" {
			item.Set("kind", p.Kind)
		}
		listed = append(listed, item)
	}
	payload := pyjson.NewObject(
		"version", JSONVersion,
		"root", o.Root,
		"rules", o.Conf.Rules,
		"mode", string(mode),
		"ticket_control", o.Conf.TicketControl,
		"projects", projects,
		"problems", listed,
		"errors", counts[rules.Error],
		"warns", counts[rules.Warn],
		"infos", counts[rules.Info],
	)
	if o.Flow != "" {
		// 実行ファイルが読んだ中身。拡張のフロー編集画面は自分の読みとこれを見比べる。rendered は SubagentStart で
		// 渡る手順の行（読めなければ null）、candidates はフローで選べるサブエージェントとスキルの名前。
		var rendered any
		if read.ok {
			rendered = read.rendered
		}
		payload.Set("flow", pyjson.NewObject("path", o.Flow, "data", read.data, "rendered", rendered,
			"candidates", catalog.Object()))
	}
	// 載せる値はどれも JSON にできる型なので失敗しない。失敗すれば Write は何も書かない。
	pyjson.Write(stdout, payload, pyjson.Options{Indent: 1})
}

// saidLines は苦情の受け皿の行を warn にする。
func saidLines(text, where string) []rules.Problem {
	var out []rules.Problem
	for _, line := range ospath.Splitlines(text) {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: where, Detail: strings.TrimPrefix(line, complaintHead)})
	}
	return out
}

// declared は書かれた切り替えの値を、判定と同じく前後の空白を落とし小文字にして読む。
func declared(value string) string { return pyfmt.Lower(ospath.Strip(value)) }

func modeIn(value string, choices []config.Mode) bool {
	for _, c := range choices {
		if string(c) == value {
			return true
		}
	}
	return false
}

func joinModes(choices []config.Mode, sep string) string {
	names := make([]string, len(choices))
	for i, c := range choices {
		names[i] = string(c)
	}
	return strings.Join(names, sep)
}

// switchProblems は 2 値の切り替えの書き誤りと、切ってあることそのもの。
func switchProblems(conf *config.Settings, guardUnwatched config.Mode) []rules.Problem {
	var out []rules.Problem
	gates := joinModes(selfguard.GateChoices, " か ")
	// 承認の経路の切り替えに dry-run は無い。書いたユーザは「止めずに報告する」つもりでも実際は enable と同じに止め、
	// 直すまで設定ファイルの書き方と実際の動きが食い違ったままになるので error にする。
	if v := declared(conf.GuardTicketApprovalDeclared); v != "" && !modeIn(v, selfguard.GateChoices) {
		out = append(out, rules.Problem{Severity: rules.Error, Rule: whereTicket, Detail: fmt.Sprintf(
			"%s=%s。この設定は %s しか受け付けない（承認は一度通れば済んでしまうので、止めずに報告するだけの値がありません）。"+
				"今は %s として動いている", env.GuardTicketApproval, v, gates, config.Enable)})
	}
	// 切ったつもりの書き誤りは enable として動いているので、「切れている」と思い続けないよう error にする。
	if v := declared(conf.TicketControlDeclared); v != "" && !modeIn(v, selfguard.GateChoices) {
		out = append(out, rules.Problem{Severity: rules.Error, Rule: whereTicket, Detail: fmt.Sprintf(
			"%s=%s。%s しか受け付けない。今は %s として動いている", env.TicketControl, v, gates, config.Enable)})
	}
	// 学びの蓄積の書き誤りも enable として動く。こちらは判定に関わらないので warn。前後の空白は落とさずに見る。
	if v := conf.LearningDeclared; v != "" && v != string(config.Enable) && v != string(config.Disable) {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereLearning, Detail: fmt.Sprintf(
			"%s=%s。enable か disable しか受け付けない。今は enable として動いている", env.Learning, v)})
	}
	// 切ってあるのは設定として正しいが、外から見て「ルールが揃っている状態」と区別が付かないので言う。
	if guardUnwatched == config.Disable {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereUnwatched, Detail: fmt.Sprintf(
			"%s=disable。%s のモードでは、どのルールも言及しない呼び出しを止めない",
			env.GuardUnwatched, strings.Join(judge.PermissionNoJudge, " と "))})
	}
	if conf.TicketsEnabled() && conf.GuardTicketApproval == string(config.Disable) {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereTicket, Detail: env.GuardTicketApproval +
			"=disable。エージェントが ccnavi の実行ファイルを直接実行して、承認・レビュー済みの受け入れ・状態の移動を行える"})
	}
	return out
}

// shown はユーザ向けの本文に出す値。CCNAVI_MODE で実際の値が変わるなら、そのことも書く（dry-run のもとで enable と
// だけ出すと、読んだユーザは守られていると思う）。
func shown(written, mode config.Mode) string {
	effective := config.EffectiveSetting(mode, written)
	if effective == written {
		return string(written)
	}
	return fmt.Sprintf("%s（%s=%s なので実際は %s）", written, env.Mode, mode, effective)
}

// gate は守る働きを持つ切り替えが止めない値になっていることを言う。見るのは CCNAVI_MODE を反映したあとの値で、
// 書かれた値と違えばそれも言う（直す先が切り替えなのか CCNAVI_MODE なのかが読めるように）。
func gate(name string, written, mode config.Mode, voices map[config.Mode]string) []rules.Problem {
	effective := config.EffectiveSetting(mode, written)
	voice := voices[effective]
	if voice == "" {
		return nil
	}
	how := fmt.Sprintf("%s=%s", name, written)
	if effective != written {
		how = fmt.Sprintf("%s=%s だが %s=%s なので実際は %s", name, written, env.Mode, mode, effective)
	}
	return []rules.Problem{{Severity: rules.Warn, Rule: whereRestore, Detail: how + "。" + voice}}
}
