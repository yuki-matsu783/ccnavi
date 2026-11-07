package events

import (
	"fmt"
	"io"
	"strings"
	"time"

	"ccnavi/internal/hook/judge"
	"ccnavi/internal/hook/outcome"
	"ccnavi/internal/hook/subagent"
	"ccnavi/internal/hook/turngate"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/turn"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/lifecycle/branch"
	"ccnavi/internal/tickets/lifecycle/stop"
	"ccnavi/internal/tickets/sync/mirror"
)

// atPrompt は UserPromptSubmit。ターンの基準を取り直し、ユーザの手と言い切れる回は前のターンの終わりからの変更を
// 受け入れる。返した文はモデルの文脈に入るので、言うことがある回だけ 1 つの additionalContext にまとめて返す。対象は、
// 設定ファイルを戻した知らせ・ミラーの知らせ・issue と MR の指定への指示・サブエージェントの知らせ・手で動かした子の
// 確定の案内。止めはしない。承認されたことは言わない（拡張とエージェントの status が言う）。
//
// バックグラウンド作業の完了通知と、前のターンの終わりに動かしている作業が残っていた回は、変更をユーザの手と区別できないので
// 受け入れない。代わりに、次のツール呼び出しの前と同じ照合をして、消えた設定ファイルを戻す。
func atPrompt(stdout, stderr io.Writer, mode config.Mode, conf *config.Settings, root string, in hookio.Input,
	record *audit.Record) (int, error) {
	now := time.Now()
	turn.Begin(conf.State, in.SessionID, now)
	// ターン分離のゲートが読むのは、ユーザの発言と言い切れるものだけ（ターンの印とは別）。
	if hookio.IsUserPrompt(in) {
		turn.NotePrompt(conf.State, in.SessionID, now)
		turngate.DropBroken(conf.State, in.SessionID)
	}
	byUser := !in.FromBackgroundTask() && !turn.Busy(conf.State, in.SessionID)
	// ミラーは受け入れより前に書く。書いた分も受け入れられ、次のツール呼び出しが戻さない。
	mirrored := ""
	step := selfguard.Before
	if byUser {
		mirrored = strings.Join(mirror.Sync(conf, root), "\n")
		step = selfguard.Accept
	}
	found := judge.GuardOutcomes(mode, conf, root, in, record, step)
	guarded := ""
	if len(found) > 0 {
		if byUser {
			// 受け入れの結果は「戻る」と結ぶ知らせに合わないので、モデルには言わず、失敗だけを標準エラーに残す。
			for _, o := range found {
				if o.Action == selfguard.ActionFailed {
					fmt.Fprintf(stderr, "ccnavi: %s: %s\n", o.Target.Label, o.Detail)
				}
			}
		} else {
			guarded = selfguard.Report(root, found)
		}
	}
	raw := readRaw(conf, root)
	c, err := checker(stderr, conf, root, record, nil, raw)
	if err != nil {
		return config.ExitOK, err
	}
	c.AtPrompt(in, record, byUser)
	hint := branch.PromptContext(stderr, conf, root, in.Prompt, in.Cwd, in.SessionID)
	// 範囲を確かめられないまま終わったサブエージェントの印は、親がツールを使わずにターンを終えたらここまで残る。
	unchecked, outcomes := "", ""
	var told []*outcome.Outcome
	if conf.TicketsEnabled() {
		unchecked = subagent.UncheckedScope(conf.State, in)
		outcomes, told = subagent.Notices(conf.State, in)
	}
	manual := manualConfirmNote(stderr, conf, root, in, raw)
	if text := pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, guarded, mirrored, hint, unchecked, outcomes, manual); text != "" {
		if err := hookio.WriteContext(stdout, hookio.UserPromptSubmit, text, ""); err != nil {
			return config.ExitOK, err
		}
	}
	// 書き終えてから「通知済み」にする（書く前に止まったら次の機会に出し直す）。
	outcome.Confirm(told)
	return config.ExitOK, nil
}

// manualConfirmNote は、ユーザが子を手で done/ に動かして共有していたら、次の発言の前に confirm を案内する文。
// Stop の促しと同じ記録を使う。ここで言えば、Stop では繰り返さない。発言の内容を読まずに出すので、Stop の促しより弱く言う。
func manualConfirmNote(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, raw *approval.Raw) string {
	if !conf.TicketsEnabled() || in.AgentID != "" || conf.State == "" {
		return ""
	}
	found := stop.PendingAtStop(conf, root, in.Cwd, raw)
	if found == nil || found.Step != stop.StepConfirm || stop.NudgedBefore(conf.State, in.SessionID, found) {
		return ""
	}
	if failed := stop.RememberNudge(conf.State, in.SessionID, found); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: 手動移動の案内を残せないので、案内しない: %s\n", failed)
		return ""
	}
	reviewSh := config.ScriptCommand(root, config.ReviewWrapper)
	return fmt.Sprintf(hookio.Head+"親 %s のフェーズ %d は、ユーザが子 %s を手で done/ に動かして共有しています"+
		"（レビュー完了確認待ち）。この発言が別の用件なら触れずに進めてよい。"+
		"確定するなら '%s confirm --phase %d' を実行する。"+
		"移動の前に付いた未解決の指摘だけが版つきで受け入れられ、移動の後に付いたものがあれば止まる。",
		found.Parent.ID, found.Number, strings.Join(found.Children, ", "), reviewSh, found.Number)
}
