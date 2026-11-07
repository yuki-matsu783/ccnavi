package events

import (
	"fmt"
	"io"
	"strings"
	"time"

	"ccnavi/internal/catalog/docs"
	"ccnavi/internal/catalog/skills"
	"ccnavi/internal/hook/ccnavimd"
	"ccnavi/internal/hook/judge"
	"ccnavi/internal/hook/subagent"
	"ccnavi/internal/hook/turngate"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/ctxfile"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/prune"
	"ccnavi/internal/records/turn"
	"ccnavi/internal/tickets/notice"
)

// atStart は SessionStart の処理。大きい対象（実行ファイル）のバックアップと記録の後始末を、セッションに 1 度、ここで行う。
// 判定は返さない。作業の進め方と作業の決まりは、起動・再開・compact・clear のどの回にも出す。文脈が新しくなるたびに
// 届かないと、compact のあとのモデルは進め方を知らないまま続けてしまう。サブエージェントには出さない。
func atStart(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record, deadline time.Time) (int, error) {
	source := in.Source
	main := in.AgentID == ""
	guarded := judge.GuardSettingFiles(mode, conf, root, in, record, selfguard.AtStart)
	// 再開と compact の後にも来るので、「1 度だけ渡す文」は改めて届き、every の刻みも数え直しになる。
	ctxfile.Forget(conf.State, in.SessionID, source == hookio.StartStartup)
	// 起動と再開は新しいプロセスで、前のプロセスのサブエージェントは生きていない（compact と clear は同じプロセス）。
	if source == hookio.StartStartup || source == hookio.StartResume {
		subagent.ForgetRunning(conf.State, in.SessionID)
		turn.Clear(conf.State, in.SessionID)
		turngate.Clear(conf.State, in.SessionID)
	}
	if source == hookio.StartStartup {
		if err := reportedBaseline(stderr, conf, root, in, record); err != nil {
			return config.ExitOK, err
		}
	}
	// 基準を取った時の注記（読めなかったツリーなど）を消さないよう、足す。
	record.Detail = pyfmt.JoinNonEmpty("; ", record.Detail, pruneAtStart(stderr, conf, root, in.SessionID))
	record.Decision, record.Enforced = audit.Allow, true
	blocks := appendSome(nil, guarded)
	// 上位に却下されて効かない承認済みチケットの要求。索引より先に組み、索引に期限の残りを渡す。
	rejects := ""
	if conf.TicketsEnabled() && main {
		rejects = notice.Rejects(conf, root, in.Cwd, deadline)
	}
	// 終わっていない integrate の記録と、直接作業のワークツリーの一覧。チケット制御は見ない。
	pending, direct := "", ""
	if main {
		pending = notice.PendingIntegrate(conf, root, within(deadline, config.IntegratePending.Value()))
		direct = notice.DirectWorktrees(conf, root, within(deadline, config.IntegratePending.Value()))
	}
	// 作業の決まりの「詳しくは --docs で引く」は、索引の案内を出す回にだけ添えるので、索引を先に組む。
	docsText := ""
	if main {
		docsText = docs.AtStart(conf, root, deadline)
		if guide := ccnavimd.SessionText(conf, root, strings.HasPrefix(docsText, docs.GuideHead),
			ruleload.DenyIDs(conf, root), mode); guide != "" {
			blocks = append(blocks, guide)
		}
		if parents := notice.Parents(conf, root); parents != "" {
			blocks = append(blocks, parents)
		}
		// 読めないレイヤーの deny は黙って効かなくなる。判定のたびには言わないので、開始のたびに言う。
		if broken := ruleload.Unreadable(conf, root); len(broken) > 0 {
			said := texts.Say(root, "NOTICE_LAYER_UNREADABLE", texts.Values{"layers": strings.Join(broken, "、")}, nil)
			rejects = pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, rejects, said)
		}
	}
	blocks = appendSome(blocks, rejects, pending, direct)
	// scratchpad/ の容量の問いは起動のときだけ（再開・compact・clear のたびに繰り返さない）。
	if source == hookio.StartStartup && main {
		blocks = appendSome(blocks, notice.Scratchpad(root, within(deadline, config.IntegratePending.Value())))
	}
	// 学びの提案の候補はユーザにだけ知らせる。
	learning := ""
	if main {
		learning = notice.Learning(conf, root, deadline)
	}
	blocks = appendSome(blocks, skills.Notice(stderr, conf, root, in, true), docsText)
	if len(blocks) == 0 && learning == "" {
		return config.ExitOK, nil
	}
	return config.ExitOK, hookio.WriteContext(stdout, hookio.SessionStart, strings.Join(blocks, pyfmt.ParagraphBreak),
		pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, rejects, pending, learning))
}

// appendSome は空でない文だけを足す。
func appendSome(blocks []string, parts ...string) []string {
	for _, p := range parts {
		if p != "" {
			blocks = append(blocks, p)
		}
	}
	return blocks
}

// within は今から limit 後と判定の期限の早いほう（期限のゼロ値は期限なし）。
func within(deadline time.Time, limit time.Duration) time.Time {
	until := time.Now().Add(limit)
	if !deadline.IsZero() && deadline.Before(until) {
		return deadline
	}
	return until
}

// reportedBaseline は、起動時にある保護領域の変更を実行後チェックの基準として記録する。全ツリーを実行後チェックと
// 同じ式で数えるので、あとで同じ変更が見つかっても新しい変更として数えない。
func reportedBaseline(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record) error {
	if conf.State == "" {
		return nil
	}
	c, err := checker(stderr, conf, root, record, nil, readRaw(conf, root))
	if err != nil {
		return err
	}
	c.CaptureBaseline(in.SessionID, record)
	return nil
}

// pruneAtStart は記録のローテートと、古い記録・終わったセッションの記録の削除をする。失敗は標準エラーに出す。
// 動かしたものの数を返す（記録の detail に残す）。
func pruneAtStart(stderr io.Writer, conf *config.Settings, root, session string) string {
	report := prune.Run(root, conf.Log, conf.State, session, prune.Options{})
	for _, problem := range report.Problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", problem)
	}
	return prune.Summary(report)
}
