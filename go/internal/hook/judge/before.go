package judge

import (
	"io"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"ccnavi/internal/catalog/skills"
	"ccnavi/internal/hook/failsafe"
	"ccnavi/internal/hook/launch"
	"ccnavi/internal/hook/reasons"
	"ccnavi/internal/hook/shellguard"
	"ccnavi/internal/hook/turngate"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/builtin"
	"ccnavi/internal/policy/ctxfile"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/redact"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/guard"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/phase"
)

// Before は実行前チェック。応答を stdout に書き、何が起きたかを record に書く。応答と記録は同じ結論から作る。
// deadline は判定の期限で、入口が呼び出しの頭で config.HookDeadline を足した時刻。戻り値は hook の終了コード。
// record.Subject は、ルールを当てる欄（rules.SubjectOf）を入口が入れておく。
func Before(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input, record *audit.Record, deadline time.Time) (int, error) {
	// バックアップは判定より先に取る。この呼び出しが何かを書き換えたときに戻る先になる。
	guardNote := GuardSettingFiles(mode, conf, root, in, record, selfguard.Before)
	j := &judgement{stdout: stdout, stderr: stderr, mode: mode, conf: conf, root: root, in: in, record: record,
		askContext: askContextCheck(root, in)}

	if slices.Contains(config.AskUserTools(), in.ToolName) {
		if text := j.askContext(); text != "" {
			return j.refuse(rules.Deny, append(pyfmt.NonEmpty([]string{guardNote}, pyfmt.KeepRepeats), askContextRefusal(record, askSubject(in), text)),
				refusal{kind: config.General})
		}
	}
	if rules.UnreadWriteTarget(in) {
		// 書き込み先が読めない書き込みは、保護の対象か決められない。判定を飛ばすと保護が素通りするので止める。
		return j.builtinRefusal(pyfmt.NonEmpty([]string{guardNote}, pyfmt.KeepRepeats), reasons.CodeWriteTargetUnread,
			reasons.WriteTargetUnreadRule, reasons.WriteTargetUnread(j.root, in.ToolName), config.General)
	}
	if record.Subject == "" {
		record.Decision, record.Reason = audit.Skip, audit.ReasonNoSubject
		return skip(stdout, guardNote)
	}

	// ルールを読み、この呼び出しの行き先のツリーを決める。dest はチケットの範囲を引く行き先で、cwd のツリーで置き換える前のもの。
	// ワークスペースの外へ書くときに cwd のワークツリーの範囲を当てないよう、置き換える前の値を使う。
	ruleSet, source, target := ruleload.RulesFor(stderr, conf, root, in, record)
	dest := target
	if target == nil && in.Cwd != "" {
		// Bash には行き先が無い。記録には cwd のツリーをつける（判定には使わない）。
		if t, ok := tree.TreeOf(root, in.Cwd, conf.Projects); ok {
			target = &t
		}
	}
	if target != nil {
		record.Tree, record.Project = target.Name, target.Project
	}
	coreFilesOn := config.EffectiveSetting(mode, config.Mode(conf.GuardCoreFiles)) != config.Disable
	if err := addGuards(conf, root, in, record, ruleSet, coreFilesOn); err != nil {
		return 0, err
	}

	subject, bare, inner, rewrites := screen(in.ToolName, record.Subject, record)
	if subject == "" {
		// コメントだけの行などは実行される部分が残らない。何も走らないものに判断を求める必要はない。
		record.Decision, record.Reason = audit.Skip, audit.ReasonNothingToRun
		return skip(stdout, guardNote)
	}
	if length := matchedLength(in.ToolName, subject, inner); length > commandLengthLimit {
		return j.builtinRefusal(pyfmt.NonEmpty([]string{guardNote}, pyfmt.KeepRepeats), reasons.CodeCommandTooLong,
			reasons.CommandTooLongRule, reasons.CommandTooLong(root, commandLengthLimit, length), config.General)
	}

	// 組み込みの既定に戻ったときだけ知らせる。レイヤーが不正で空になったのは退避ではないので、記録と lint が伝える。
	fallback := ""
	if record.Fallback == builtin.Fallback {
		path := record.Detail
		if path == "" {
			path = conf.Rules
		}
		fallback = reasons.FallenBack(path, root)
	}
	notices := pyfmt.NonEmpty([]string{guardNote, fallback}, pyfmt.KeepRepeats)

	if code, done, err := j.builtinStops(notices, subject, inner, coreFilesOn); done || err != nil {
		return code, err
	}

	// 承認済みチケットの置き場は 1 回の判定で 1 度だけ読み、レビュー待ちの止めと範囲の索引で共有する。
	var raw *approval.Raw

	// レビューが要るフェーズが終わってマーカーが無い間、サブエージェントの起動と、例外以外のシェルを止める。
	if conf.TicketsEnabled() && slices.Contains(useronly.HeldTools(), in.ToolName) {
		var parent *ticket.Ticket
		parent, raw = phase.ParentAt(conf, root, in.Cwd, raw)
		var held *phase.Phase
		if parent != nil {
			held = phase.HeldPhase(conf, root, parent.ID, raw)
		}
		exempt := in.ToolName == config.ToolBash && useronly.Exempt(subject, record.Degraded)
		if held != nil && !exempt {
			record.Code, record.Rules = phase.CodeReview, []string{reasons.PhaseReviewRule}
			return j.refuse(rules.Deny, append(notices, phase.HoldReason(held, in.ToolName, root)), refusal{kind: config.Gate})
		}
	}

	// サブエージェントの起動。担当が決まらないときはここで止める。担当が決まったときの入力の更新（担当の案内）は、
	// 最後の応答に合わせるため持ったまま判定を進める。ルールには、書き足す前の入力を当てる。
	var updated *pyjson.Object
	if slices.Contains(config.AgentTools(), in.ToolName) {
		r := launch.Decide(conf, root, in, raw)
		if r.Stop != nil {
			return j.builtinRefusal(notices, r.Stop.Code, r.Stop.Rule, r.Stop.Text, r.Stop.Kind)
		}
		updated = r.Apply(in.ToolInput)
	}

	// 承認済みチケットの索引。ワークツリーへの書き込みでは、取り違えの点検と範囲の判定の両方が引くので 1 度だけ読む。
	var index map[string]*ticket.Ticket
	var seen scanned
	writes := conf.TicketsEnabled() && target != nil && isWriteTool(in.ToolName)
	if writes && !target.IsMain() {
		if raw == nil {
			raw = approval.ReadRaw(conf, root)
		}
		seen.read(conf, root, raw)
		index = approval.ByID(seen.tickets)
	}
	if writes {
		// 取り違えはルールより先に見る（範囲の宣言ではないので、ルールの allow でも通さない）。
		if mismatch := projectMismatch(root, *target, record.Subject, index); mismatch != "" {
			record.Code, record.Rules = reasons.CodeTicketProject, []string{reasons.TicketProjectRule}
			return j.refuse(rules.Deny, append(notices, mismatch), refusal{kind: config.Gate})
		}
		// 着手中の子のフローは書き換えさせない（組み込みの保護を切った設定でも、着手中と名指しして止める）。
		locked := flowLock(conf, root, in, record.Subject, &seen)
		if index == nil && seen.have {
			index = approval.ByID(seen.tickets)
		}
		if locked != "" {
			record.Code, record.Rules = flow.CodeLocked, []string{flow.LockRule}
			return j.refuse(rules.Deny, append(notices, locked), refusal{kind: config.Gate})
		}
	}

	// 読みが分かれる形（ブレース展開・実行時に決まるコマンド名・バッククォート）は、読み分けずに止める。
	// 形ごとの書き直し方を 1 回で返す。読み違えると止まらずに通ってしまうため。
	if len(rewrites) > 0 {
		kind := rewriteKind(conf, in, subject, inner, rewrites)
		var forms []shell.Form
		for _, r := range rewrites {
			if !slices.Contains(forms, r.Form) {
				forms = append(forms, r.Form)
			}
		}
		record.Code = reasons.RewriteCode(forms[0])
		record.Rules = nil
		git, _ := config.GitWrapperCommand(root)
		parts := slices.Clip(notices)
		for _, form := range forms {
			record.Rules = append(record.Rules, reasons.RewriteRule(form))
			var found []string
			for _, r := range rewrites {
				if r.Form == form {
					found = append(found, r.Text)
				}
			}
			parts = append(parts, reasons.Rewrite(root, record.Subject, form, found, git))
		}
		return j.refuse(rules.Deny, parts, refusal{kind: kind})
	}

	return j.byRules(ruleSet, source, subject, bare, inner, notices, target, dest, index, updated, deadline)
}

// commandLengthLimit は、ルールを当てるシェルのコマンドの長さ（文字数）の上限。超えたら照合せずに止める。
// ルールの正規表現には、区切り（`|`・`;`・`&`）の多い長い文字列で時間が長さの 2 乗で伸びるものがある（引用の中の
// `a|b` を 2 万字並べると 1 本で 20 秒を超える）。期限はルール 1 本ごとの間でしか見られず、1 本が hook の期限
// （Claude Code の既定 10 秒）を越えると Claude Code が hook を打ち切り、呼び出しは判定なしで通る。上限の長さなら、
// 知られている最悪の形（`ls | ` の繰り返し・引用の中の `a|b` の繰り返し）でも、負荷の高い機械で全体が 2 秒ほどで終わる。
// 数えるのは読んだ形（ヒアドキュメントの本文とコメントは照合しないので含まない）。引用の中の演算子の文字は、両側に
// 目印が入るぶん打った長さより長くなる。
const commandLengthLimit = 4_000

// matchedLength はシェルのツールで、ルールを当てる文字列（読んだ形と、中で実行される層）のうち最も長いものの文字数。
// シェル以外のツールは 0（パスや見出しは長くならない）。
func matchedLength(tool, subject string, inner []layer) int {
	if !slices.Contains(config.ShellTools(), tool) {
		return 0
	}
	longest := utf8.RuneCountInString(subject)
	for _, l := range inner {
		longest = max(longest, utf8.RuneCountInString(l.text))
	}
	return longest
}

// addGuards は組み込みの保護（設定ファイル・チケットの置き場・生の git・ユーザの判断の経路）をルールに足す。
func addGuards(conf *config.Settings, root string, in hookio.Input, record *audit.Record, rs *rules.RuleSet, coreFilesOn bool) error {
	// シェルから設定ファイルへ書く形を止める側。既定に戻った間は組み込みの既定に同じ形が入っているので足さない
	// （二重に当たると同じ話が 2 度返る）。名前で書くツールの 4 本は既定に無いので、既定の間も足す（AddRules の後）。
	if record.Fallback != builtin.Fallback && coreFilesOn {
		selfguard.AddRules(rs, selfguard.Options{
			Bin: conf.Bin, ProjectHome: conf.ProjectHome, Root: root, CommonFiles: selfguard.CommonLayerFiles(conf),
			Log: conf.Log, State: conf.State, Tool: in.ToolName, Cwd: in.Cwd,
		})
	}
	if coreFilesOn {
		selfguard.AddNamedRules(rs, conf.ProjectHome, root)
	}
	// 生の git はチケット制御の有無によらず止める（入口をラッパー 1 本に寄せる）。
	rawGit, err := guard.RawGitRule(root)
	if err != nil {
		return err
	}
	rs.Deny = append(rs.Deny, rawGit)
	if !conf.TicketsEnabled() {
		return nil
	}
	places, err := guard.GuardRules(conf.Tickets, root)
	if err != nil {
		return err
	}
	// 承認済みの置き場と操作記録の置き場はゲートなので、dry-run でも保護の設定でも外れない。
	gates, err := guard.GateRules(conf.Approved, root)
	if err != nil {
		return err
	}
	rs.Deny = slices.Concat(rs.Deny, places, gates)
	// ユーザの判断の経路を、実行ファイルを直接打つ形で通さない。サブエージェントからは設定に関わらず止める。
	if conf.GuardTicketApproval != string(config.Disable) || in.AgentID != "" {
		approvalRule, err := useronly.TicketApprovalRule(conf.Bin, root)
		if err != nil {
			return err
		}
		rs.Deny = append(rs.Deny, approvalRule)
	}
	return nil
}

// builtinStops はルールより先に当てる組み込みの止め（レビュー待ちより前の分）。done なら応答を書き終えている。
func (j *judgement) builtinStops(notices []string, subject string, inner []layer, coreFilesOn bool) (int, bool, error) {
	conf, root, in, record := j.conf, j.root, j.in, j.record
	degraded := record.Degraded != ""
	stop := func(code, ruleID, text string, kind config.HoldKind) (int, bool, error) {
		c, err := j.builtinRefusal(notices, code, ruleID, text, kind)
		return c, true, err
	}
	shellTool := slices.Contains(config.ShellTools(), in.ToolName)
	if in.ToolName == config.ToolBash {
		// 保護済みの sh を、sh の検査の材料を変える環境変数と同じ行で呼ぶ形（補助。dry-run でも止める）。
		if script, names := shellguard.CheckEnv(record.Subject, degraded); len(names) > 0 {
			return stop(shellguard.CodeEnv, shellguard.EnvRuleID, shellguard.EnvMessage(root, script, names), config.Aux)
		}
		// git のラッパーで自己防衛の対象を消す・別のコミットの中身に戻す形。
		if coreFilesOn {
			if sub, word, kind := shellguard.CheckCorePaths(conf, root, in.Cwd, record.Subject, degraded); sub != "" {
				return stop(shellguard.CodeCorePath, shellguard.CorePathRuleID, shellguard.CorePathMessage(root, sub, word, kind), config.General)
			}
		}
	}
	// 記録と state を消す形は、チケット制御に依らず止める（ガード自身を守る設定で切れる）。
	if shellTool && coreFilesOn {
		found := useronly.PruneForm(record.Subject, conf.Bin)
		if found == "" {
			found = useronly.UserPruneForm(record.Subject, conf.Bin)
		}
		if found != "" {
			return stop(useronly.CodeRecordsPrune, useronly.RecordsPruneRuleID, useronly.PruneMessage(root), config.General)
		}
	}
	// ユーザの判断の経路のうち、hook のほかに保護が無い形。呼び方は追い切れないので文字列の組で見る。
	if conf.TicketsEnabled() && conf.GuardTicketApproval != string(config.Disable) && shellTool {
		if kind, found := useronly.HumanPathForm(record.Subject); found != "" {
			return stop(useronly.CodeTicketApproval, useronly.TicketApprovalRuleID, useronly.HumanMessage(root, kind, found), config.Gate)
		}
		// 2 段目の無いユーザの入口は、引数を変数・置換・glob で組むと表記の検査から外れるので、読めなければ止める。
		layers := make([]useronly.Layer, len(inner))
		for i, l := range inner {
			layers[i] = useronly.Layer{Runner: l.runner, Text: l.text}
		}
		if entry, found := useronly.HiddenUserEntry(record.Subject, layers, conf.Bin); entry != "" {
			return stop(useronly.CodeTicketApproval, useronly.TicketApprovalRuleID, useronly.HiddenEntryMessage(root, entry, found), config.Gate)
		}
	}
	// 子チケットのワークツリーからの push は誰が打っても止める（sh の検査は 2 重目）。
	if conf.TicketsEnabled() && in.ToolName == config.ToolBash {
		if name, parent := shellguard.CheckChildPush(conf, root, in.Cwd, record.Subject, degraded); name != "" {
			return stop(shellguard.CodeChildPush, shellguard.ChildPushRuleID, shellguard.ChildPushMessage(root, name, parent), config.Gate)
		}
	}
	if shellTool {
		if code, done, err := j.turnGate(notices, subject, inner); done || err != nil {
			return code, done, err
		}
	}
	// サブエージェントには、状態を動かすスクリプトもレビューのスクリプトも打たせない（閉じるのは親だけ）。
	// 中で実行されるコマンドにも当てる（`env sh …` は元の形だけでは先頭の sh に固定した形から外れる）。
	if conf.TicketsEnabled() && in.AgentID != "" && shellTool && useronly.Forbidden(subject, joinLayers(inner, shell.Sep)) {
		runner, text := "", ""
		if !useronly.Forbidden(subject, "") {
			for _, l := range inner {
				if useronly.Forbidden(l.text, "") {
					runner, text = l.runner, l.text
					break
				}
			}
		}
		record.Code, record.Rules = phase.CodeSubagent, []string{reasons.SubagentTicketOpRule}
		record.Unwrapped = text
		code, err := j.refuse(rules.Deny, append(notices, reasons.SubagentForbidden(root, redact.Redact(record.Subject), runner, text)),
			refusal{kind: config.Gate})
		return code, true, err
	}
	// サブエージェントは親のワークツリーを書き換えない（合流・コミット・push はメインが finish で行う）。
	if conf.TicketsEnabled() && in.AgentID != "" {
		if message := j.parentTreeWrite(inner); message != "" {
			return stop(shellguard.CodeParentTree, shellguard.ParentTreeRuleID, message, config.Gate)
		}
	}
	return 0, false, nil
}

// turnGate はユーザの発言をまたぐゲート。ルールが allow でも、ユーザの返事を待つ手順は外さない。B は止め、通した A は
// 記録する。付き添いのあるセッションでは、askIfAttended の宣言があると B を確認に出す（同じコマンドに A と B を並べたら止める）。
// サブエージェントは対話でも発言を待つ。確認で先に返すと、後のサブエージェントの禁止に届かないため。
func (j *judgement) turnGate(notices []string, subject string, inner []layer) (int, bool, error) {
	conf, in, record := j.conf, j.in, j.record
	commands := []string{subject}
	for _, l := range inner {
		if l.runner != shell.Moved {
			commands = append(commands, l.text)
		}
	}
	waited, asked := turngate.SplitByAttendance(turngate.Builtin(), !hookio.Unattended() && in.AgentID == "")
	stop, err := turngate.Check(waited, commands, conf.State, in.SessionID, false)
	if err == nil && stop == nil {
		stop, err = turngate.Check(asked, commands, conf.State, in.SessionID, true)
	}
	if err == nil && stop == nil {
		stop, err = turngate.Note(waited, commands, conf.State, in.SessionID, in.ToolUseID, time.Time{})
	}
	if err != nil {
		return 0, true, err
	}
	if stop == nil {
		confirm, err := turngate.Asks(asked, commands)
		if err != nil {
			return 0, true, err
		}
		if confirm != nil {
			id := turngate.RuleID(confirm.Gate)
			record.Code, record.Rules = reasons.CodeRuleAsk, []string{id}
			code, err := j.refuse(rules.Ask, append(slices.Clip(notices), reasons.BuiltinRefusal(reasons.CodeRuleAsk, id,
				redact.Redact(record.Subject), turngate.AskMessage(confirm, j.root))), refusal{kind: config.Gate, checkAsk: true})
			return code, true, err
		}
		return 0, false, nil
	}
	code, err := j.builtinRefusal(notices, turngate.Code, turngate.RuleID(stop.Gate), turngate.Message(stop, j.root), config.Gate)
	return code, true, err
}

// parentTreeWrite はサブエージェントの、親のワークツリーへの書き込みを止める文（当たらなければ空）。居場所
// （実行先のツリー）で見る。そのため、子のワークツリーでのコミットと、子で親のブランチを取り込む merge は通る。
func (j *judgement) parentTreeWrite(inner []layer) string {
	conf, root, in, record := j.conf, j.root, j.in, j.record
	degraded := record.Degraded != ""
	if in.ToolName == config.ToolBash {
		if sub, name := shellguard.CheckParentTreeGit(conf, root, in.Cwd, record.Subject, degraded); name != "" {
			return shellguard.ParentTreeMessage(root, sub, name)
		}
		// シェルからの書き込み（リダイレクト・tee・sed -i・cp / mv の行き先、インタプリタに渡したコード）。
		w := shellguard.CheckParentWrite(conf, root, in.Cwd, record.Subject, degraded, lineLayers(inner))
		if w.What != "" {
			return shellguard.ParentWriteMessage(root, w.What, w.Parent, w.Kind)
		}
		return ""
	}
	if !isWriteTool(in.ToolName) {
		return ""
	}
	// 書き込み先が親のワークツリーの中か（sh の検査と同じ広さ。シェルの書き込み先と同じ関数で見る）。
	if name := shellguard.ParentWriteAt(conf, root, record.Subject); name != "" {
		return shellguard.ParentWriteMessage(root, in.ToolName, name, shellguard.KindWrite)
	}
	return ""
}

// viaLayer は、ルールが中で実行されるコマンドでヒットしたときのその層（元の形でヒットしたら空）。
type viaLayer struct {
	runner, text string
	quoted       bool
}

// byRules はルールを deny・ask・allow の順に当て、ワークスペースルート直下へのシェルの書き込みとチケットの範囲を合わせて
// 結論を返す。
//
// 処理フロー:
//  1. 読み切れない文字列への照合に上限を掛けるか決める（capped）
//  2. deny・ask・allow の順にルールを当て、最初に当たった区分の組を結論にする（期限を越えたら失敗側に倒す）
//  3. ワークスペースルート直下への書き込みと、チケットの範囲を合わせる（強い側を採る）
//  4. 文脈を集め、allow・渡す・止める・聞くのどれかで応答を書く
func (j *judgement) byRules(ruleSet *rules.RuleSet, source rules.Origin, subject, bare string, inner []layer, notices []string,
	target, dest *tree.Tree, index map[string]*ticket.Ticket, updated *pyjson.Object, deadline time.Time) (int, error) {
	conf, root, in, record := j.conf, j.root, j.in, j.record
	tool := in.ToolName
	degraded := shell.Reason(record.Degraded)
	display := redact.Redact(record.Subject)
	git, _ := config.GitWrapperCommand(root)

	// 生の文字列（読み切れなかった Bash と、読まない PowerShell）に `>` が多すぎると、シェルから書く形の保護の照合が遅くなり、
	// 期限を越える。そのときは、その保護だけを生の文字列に当てず、ほかの deny と ask を当てたうえで権限モードに依らず拒否にする
	// （照合を飛ばして通すと、上限の後ろの保護対象への書き込みが素通りするため）。外す保護がこのツールに当たらないなら上限も掛けない。
	unread := degraded != "" || slices.Contains(config.ShellTools(), tool) && tool != config.ToolBash
	capped := unread && selfguard.TooManyRedirects(subject) && slices.ContainsFunc(ruleSet.Deny, func(r *rules.Rule) bool {
		return slices.Contains(shellGuardRuleIDs, r.ID) && rules.ToolMatches(r.Match, tool)
	})

	// group は結論の区分で当たったルール。via は各ルールが元の形で当たったか（空）、中で実行される層で当たったか（その層）。
	var verdict rules.Decision
	var group []*rules.Rule
	var via []viaLayer
	for _, name := range rules.Sections() {
		if name == rules.Allow && (degraded != "" || capped) {
			// 読み切れなかったコマンドに allow は当てない（読めなかった文字列にそう書いてあった、を根拠に通さない）。
			break
		}
		// 中で実行されるコマンドは止める側（deny・ask）にだけ当てる。allow に当てると `sudo -u me cat /etc/hosts` が
		// 中の cat で通ってしまう。引用の外の層を先に見る（引用の中の断りは、中にだけ当たったときに付ける）。
		var layers []layer
		if name != rules.Allow {
			layers = slices.Clone(inner)
			slices.SortStableFunc(layers, func(a, b layer) int { return boolOrder(a.quoted) - boolOrder(b.quoted) })
		}
		for _, rule := range *ruleSet.Section(name) {
			if fsio.Now().After(deadline) {
				// 期限切れの結論もツールの種類で決める。通すのは設定を読まずに安全と分かる読み取りだけ。
				code := config.FailClosed(j.mode, tool, failsafe.IsSafe(in, root))
				_, err := io.WriteString(j.stderr, reasons.DeadlineExceeded(root, string(audit.ReasonDeadlineExceeded),
					config.HookDeadline.Value(), code == config.ExitOK)+"\n")
				record.Decision, record.Reason = audit.Skip, audit.ReasonDeadlineExceeded
				return code, err
			}
			rawSkipped := capped && slices.Contains(shellGuardRuleIDs, rule.ID)
			if !rawSkipped && rule.Matches(tool, subject) {
				group = append(group, rule)
				via = append(via, viaLayer{})
				continue
			}
			for _, l := range layers {
				if rule.Matches(tool, l.text) {
					group = append(group, rule)
					via = append(via, viaLayer{l.runner, l.text, l.quoted})
					break
				}
			}
		}
		if len(group) > 0 {
			verdict = name
			break
		}
	}
	// 分類（dry-run でも止めるか）は、土台を落とす前の組で決める。層の deny が同時に当たっても、生の git の分類を失わない。
	hitIDs := map[string]bool{}
	for _, r := range group {
		hitIDs[r.ID] = true
	}
	if verdict == rules.Deny && slices.ContainsFunc(group, func(r *rules.Rule) bool { return r.Base }) {
		// 土台はいつも当たる。レイヤーの deny にも当たったなら、そちらだけを言う（同じ拒否を 2 つの文で返さない）。
		var keptRules []*rules.Rule
		var keptVia []viaLayer
		for i, r := range group {
			if !r.Base {
				keptRules, keptVia = append(keptRules, r), append(keptVia, via[i])
			}
		}
		if len(keptRules) > 0 {
			group, via = keptRules, keptVia
		}
	}
	// 保護を当てなかったぶん、ルールの deny に当たっていなければ拒否に上げる（委ねる先が無い）。
	capped = capped && verdict != rules.Deny
	if capped {
		verdict = rules.Deny
	}

	// シェルからワークスペースルート直下へ書く形は、Write・Edit と同じ決まり（workspace-root）に当てて止める。
	// ほかの deny に当たった呼び出しは、その文で止まる。
	if tool == config.ToolBash && verdict != rules.Deny {
		if w := shellguard.CheckRootWrite(ruleSet, in.Cwd, record.Subject, lineLayers(inner)); w.Rule != nil {
			record.Code, record.Rules = shellguard.CodeRoot, []string{rules.RecordName(w.Rule)}
			record.Hits = []any{w.Rule}
			return j.refuse(rules.Deny, append(slices.Clip(notices), reasons.BuiltinRefusal(shellguard.CodeRoot,
				shellguard.RootRuleID, display, shellguard.RootWriteMessage(root, w))), refusal{kind: config.General})
		}
	}

	record.Rules = make([]string, len(group))
	record.Hits = make([]any, len(group))
	var unwrapped []string
	for i, r := range group {
		record.Rules[i], record.Hits[i] = rules.RecordName(r), r
		if via[i].text != "" && !slices.Contains(unwrapped, via[i].text) {
			unwrapped = append(unwrapped, via[i].text)
		}
	}
	record.Unwrapped = strings.Join(unwrapped, shell.Sep)
	if len(group) > 0 {
		record.Source = group[0].Source
	}

	// 止めた・聞いたルールのうち、引用の中から切り出したコマンドにだけ当たったもの（書いた側は文字のつもりでいる）。
	// 元の形で当たったルールは、引用の中を除いた読み（bare）に当て直して決める。層で当たったルールは、その層が
	// 引用の中から切り出したものかで決める。
	inside := make([]bool, len(group))
	if verdict == rules.Deny || verdict == rules.Ask {
		for i, r := range group {
			if via[i].text != "" {
				inside[i] = via[i].quoted
			} else {
				inside[i] = bare != subject && !r.Matches(tool, bare)
			}
		}
	}
	record.Quoted = nil
	for i, name := range record.Rules {
		if inside[i] {
			record.Quoted = append(record.Quoted, name)
		}
	}

	// ルールの判定とチケットの判定を合わせ、強い側を採る（同じならルール）。チケットが言うのは閉じる向きだけで、
	// ルールの deny・ask を緩めることはない。
	ticketReason, ticketCode := "", ""
	if verdict != rules.Deny {
		var hit *ruleHit
		if len(group) > 0 {
			hit = &ruleHit{rules.RecordName(group[0]), verdict}
		}
		t := ticketVerdict(conf, root, tool, record.Subject, dest, index, hit)
		if strength[t.decision] > strength[verdict] {
			verdict, ticketReason, ticketCode = t.decision, t.text, t.code
			if ticketReason != "" {
				// 判定を下したのはレイヤーを持たないチケット。狭められたルールの id は後ろに残す。
				decidedBy := builtin.TicketScopeRuleID
				switch t.code {
				case reasons.CodeTicketBlocked:
					decidedBy = reasons.TicketBlockedRule
				case reasons.CodeTicketParent:
					decidedBy = reasons.TicketParentRule
				}
				record.Rules = slices.Concat([]string{decidedBy}, record.Rules)
				record.Source = ""
			}
		}
	}

	// モデルへの文脈（ルールの文・提案の確認・スキルの目録）。判定とは別の経路（additionalContext）で届く。
	context, hinted := ctxfile.Deliver(j.stderr, conf.State, in, group, ctxfile.Options{Bases: ctxfile.Bases(conf, root, target)})
	record.Hinted = hinted
	told := ""
	if conf.TicketsEnabled() {
		told = guard.ProposeNotice(j.stderr, conf, root, in, record.Subject)
	}
	told = pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, told, skills.Notice(j.stderr, conf, root, in, false))
	context = pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, told, context)

	if verdict == rules.Allow {
		record.Decision, record.Enforced = audit.Allow, true
		if len(notices) > 0 || context != "" || updated != nil {
			// 通した回にも言う。ガードが今なにを見ていないのかを言わないと、誰も知らないまま作業が進む。
			return config.ExitOK, writePass(j.stdout, pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, append(slices.Clip(notices), context)...), updated)
		}
		return config.ExitOK, nil
	}

	reasonFor := func(i int) string {
		return reasons.ReasonFor(root, reasons.Hit{Rule: group[i], Tool: tool, Subject: display, Degraded: degraded,
			Runner: via[i].runner, Inner: via[i].text, Quoted: inside[i]}, git)
	}
	var parts []string
	switch {
	case verdict == rules.Deny && capped:
		// `>` の上限で止めた。当たった ask のルールがあれば、その文も後ろに並べる。
		parts = append(parts, reasons.BuiltinRefusal(reasons.CodeRedirectLimitDeny, reasons.RedirectLimitRule, display,
			texts.Say(root, reasons.CodeRedirectLimitDeny, texts.Values{
				"limit":  strconv.Itoa(selfguard.RedirectLimit),
				"length": strconv.Itoa(selfguard.RawLengthLimit),
			}, nil)))
		for i := range group {
			parts = append(parts, reasonFor(i))
		}
		record.Code = reasons.CodeRedirectLimitDeny
		record.Rules = slices.Concat([]string{reasons.RedirectLimitRule}, record.Rules)
		// 決めたのはレイヤーを持たない組み込み。並べた ask のレイヤーを残すと、ルールファイルが止めたように読める。
		record.Source = ""
	case verdict == rules.Deny && ticketReason == "":
		// ヒットした理由はまとめて 1 回で返す（1 つずつ返すと、直すたびに往復が増える）。
		readThrough := true
		for i := range group {
			parts = append(parts, reasonFor(i))
			readThrough = readThrough && via[i].text != ""
		}
		// 全部が中で実行されるコマンドで当たったなら、当てた先は読み直したコマンドで、生の文字列ではない。
		codeDegraded := degraded
		if readThrough {
			codeDegraded = ""
		}
		record.Code = reasons.CodeFor(tool, codeDegraded)
		if slices.ContainsFunc(group, func(r *rules.Rule) bool { return r.ID == useronly.TicketApprovalRuleID }) {
			record.Code = useronly.CodeTicketApproval
		}
	case verdict == rules.Deny:
		parts = []string{ticketReason}
		record.Code = ticketCode
		if record.Code == "" {
			record.Code = reasons.CodeTicketScope
		}
	case verdict == rules.Ask && ticketReason == "":
		for i, r := range group {
			lines := []string{reasonFor(i)}
			if r.Decision == rules.Ask {
				lines = append(lines, reasons.AskHint(root, r.BareID, display)...)
			}
			parts = append(parts, strings.Join(lines, "\n"))
		}
		record.Code = reasons.CodeRuleAsk
	case verdict == rules.Ask:
		parts = []string{ticketReason}
		record.Code = reasons.CodeTicketAsk
	default:
		// どのタイプも言及しなかった。結末は Claude Code の権限モードが決める。
		record.Code = reasons.CodeUndeclared
		if degraded != "" {
			record.Code = reasons.CodeUncertain
		}
		verdict = UndeclaredVerdict(in.PermissionMode, record.Degraded, conf.GuardUnwatched)
		if verdict != Handover {
			parts = []string{reasons.Undeclared(root, tool, display, source, degraded, verdict, record.Fallback == builtin.Fallback)}
		}
	}

	if verdict == Handover {
		// 判定は渡した。文は返さない（渡した先が判断するだけの回に、毎度文脈を積まない）。記録の handover で数える。
		// 1 度だけ渡す文（提案の確認・スキルの目録）は渡す回にも言う。
		record.Decision, record.Enforced = audit.Handover, false
		if len(notices) > 0 || told != "" || updated != nil {
			return config.ExitOK, writePass(j.stdout, pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, append(slices.Clip(notices), told)...), updated)
		}
		return config.ExitOK, nil
	}
	return j.refuse(verdict, append(slices.Clip(notices), parts...), refusal{
		kind:     finalKind(conf, hitIDs, ticketReason != "", capped),
		context:  context,
		updated:  updated,
		checkAsk: true,
	})
}

// skip は判定せずに抜ける回。設定ファイルの知らせがあれば、それだけを返す。
func skip(stdout io.Writer, note string) (int, error) {
	if note == "" {
		return config.ExitOK, nil
	}
	return config.ExitOK, hookio.WriteContext(stdout, hookio.PreToolUse, note, "")
}

func isWriteTool(tool string) bool { return slices.Contains(config.WriteTools(), tool) }

func boolOrder(b bool) int {
	if b {
		return 1
	}
	return 0
}
