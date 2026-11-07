package events

import (
	"io"
	"time"

	"ccnavi/internal/catalog/docs"
	"ccnavi/internal/hook/hint"
	"ccnavi/internal/hook/judge"
	"ccnavi/internal/hook/outcome"
	"ccnavi/internal/hook/subagent"
	"ccnavi/internal/hook/turngate"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/tally"
	"ccnavi/internal/records/turn"
	"ccnavi/internal/tickets/model/directcand"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/phase"
)

// toolArtifact は Artifact ツール（ファイルを渡して publish する）。失敗したら HTML の置き場を知らせる。
const toolArtifact = "Artifact"

// dryRunPushback は dry-run で、enable なら差し戻していた報告の頭。
const dryRunPushback = "[ccnavi dry-run] " + string(config.Enable) + " would have sent this back as a correction:\n"

// after は PostToolUse。実行後チェックを 1 回動かし、言うことがあれば返す。差し戻すかどうかに関わらず、最後に直接作業の
// 候補を登録する。書き込みは起きているので、git は呼ばず、失敗しても何も言わない。
func after(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record) (int, error) {
	defer func() { directcand.Touch(conf, root, in, time.Now()) }()
	return check(stdout, stderr, mode, conf, root, in, record)
}

// check は after の本体。期限は渡さない。止められないイベントで、遅れは待ち時間にしかならないため。ワークツリーを読む側が
// 自前の短い時間を持つ。差し戻すのは、直前の実行が汚したと言える deny があるときだけ。enable なら標準エラーと
// ExitBlock、dry-run なら差し戻さず、文脈の文に dry-run の頭を添える。
func check(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record) (int, error) {
	// 成功が入ったので、失敗の連続を 0 に戻す。
	tally.Reset(conf.State, in.SessionID, in.AgentID, tally.StreakThreshold(conf.FailStreakCount))
	// 設定ファイルはルールを読む前に戻す。この呼び出しが空にしたルールで「守るものは無い」と決めないため。
	guarded := judge.GuardSettingFiles(mode, conf, root, in, record, selfguard.After)
	watched, err := watchedFor(stderr, conf, root, record, &in)
	if err != nil {
		return config.ExitOK, err
	}
	if len(watched) > 1 {
		last := watched[len(watched)-1].Tree
		record.Tree, record.Project = last.Name, last.Project
	}
	// 既定に戻ったことはここでは言わない（実行前チェックが呼び出しごとに言っている。記録には fallback が残る）。
	raw := readRaw(conf, root)
	c := checkerOf(stderr, conf, root, watched, raw)
	text, restored := c.Check(mode == config.Enable, config.EffectiveSetting(mode, config.Mode(conf.RestoreIfDeny)), in, record)
	// ガード自身が触られた回は、何より先に読まれてほしい。
	text = paragraphs(guarded, text)
	bounced := ""
	var told []*outcome.Outcome
	if conf.TicketsEnabled() {
		// 戻そうとした回は置き場のファイルも戻っていることがあるので読み直す。
		if restored {
			raw = nil
		}
		// フェーズが終わったばかりなら、ここで 1 度だけ言う（止まるのは次の呼び出しから）。
		if parent, latest := phase.ParentAt(conf, root, in.Cwd, raw); parent != nil {
			text = paragraphs(text, phase.Announce(stderr, conf, root, parent, latest))
		}
		// 差し戻しは 1 回きりなので、無視して終わったサブエージェントは何も言われずに通っている。親にそれを言う。
		ignored := subagent.IgnoredBounce(conf.State, in)
		unchecked := subagent.UncheckedScope(conf.State, in)
		var outcomes string
		outcomes, told = subagent.Notices(conf.State, in)
		bounced = paragraphs(ignored, unchecked, outcomes)
		text = paragraphs(text, bounced)
	}
	// 判定を変えない案内（frontmatter の無い md・手書きの提案の書式・直接作業のコミットの次の一手）。
	hints := paragraphs(docs.MissingFrontmatter(conf, root, in), hint.ProposalHint(conf, root, in))
	hints = paragraphs(hints, hint.CommitNotice(stderr, conf, root, in))
	if text == "" && hints == "" {
		return config.ExitOK, nil
	}
	// セッションが始まる前から在った変更は言うが差し戻さない（誰が書いたか分からないものを直させない）。
	pushback := record.Decision == audit.Deny
	if pushback && mode == config.Enable {
		// ツールはすでに走っているので、渡せるのは次に何をするかだけ。exit 2 と標準エラーで返す。
		if _, err := io.WriteString(stderr, paragraphs(text, hints)+"\n"); err != nil {
			return config.ExitBlock, err
		}
		outcome.Confirm(told)
		return config.ExitBlock, nil
	}
	if pushback {
		// dry-run は止めないことが約束なので、差し戻さずに報告の側へ届ける。
		text = dryRunPushback + text
	}
	// 入れ子のサブエージェントの中では、差し戻しを無視した知らせは、その子にしか届かない。ユーザにも見えるよう、
	// systemMessage に同じ文を載せる。exit 2 の経路では標準出力の JSON が読まれないので載せられない。
	system := ""
	if in.AgentID != "" {
		system = bounced
	}
	if err := hookio.WriteContext(stdout, hookio.PostToolUse, paragraphs(text, hints), system); err != nil {
		return config.ExitOK, err
	}
	outcome.Confirm(told)
	return config.ExitOK, nil
}

// afterFailure は PostToolUseFailure。失敗した呼び出しも、失敗する前に設定ファイルや保護領域を変えていることがある
// （`rm …; false`、途中で落ちたビルド）。そのため成功と同じ照合をして戻し、文脈の文で知らせる。失敗の連続に数え、最初の
// 1 回と閾値の倍数の回に資料を引く案内を返す。中断はユーザが止めたもので、ツールの誤りではないので数えない。
func afterFailure(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record) (int, error) {
	record.Decision, record.Reason = audit.Skip, audit.ReasonEventNotChecked
	// 失敗した呼び出しは、ターン分離のゲートの A に数えない。
	turngate.ForgetCall(conf.State, in.SessionID, in.ToolUseID)
	if in.IsInterrupt && in.AgentID == "" {
		// ユーザの中断。Stop は来ないことがあるので、ここでターンの終わりにする。
		turn.End(conf.State, in.SessionID)
	}
	guarded := judge.GuardSettingFiles(mode, conf, root, in, record, selfguard.After)
	c, err := checker(stderr, conf, root, record, &in, readRaw(conf, root))
	if err != nil {
		return config.ExitOK, err
	}
	text, _ := c.Check(mode == config.Enable, config.EffectiveSetting(mode, config.Mode(conf.RestoreIfDeny)), in, record)
	artifact := ""
	if in.ToolName == toolArtifact && !in.IsInterrupt {
		artifact = texts.Say(root, "NOTICE_ARTIFACT_FAILED", texts.Values{"scratch": place.ScratchDir(root)}, nil)
	}
	streak := ""
	if !in.IsInterrupt {
		streak = judge.FailStreakHint(conf, root, in.SessionID, in.AgentID)
	}
	if said := paragraphs(guarded, text, artifact, streak); said != "" {
		return config.ExitOK, hookio.WriteContext(stdout, hookio.PostToolUseFailure, said, "")
	}
	return config.ExitOK, nil
}
