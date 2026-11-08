package config

import (
	"fmt"
	"io"
	"slices"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/pyfmt"
)

// 終了コード。
const (
	ExitOK    = 0 // 判定を書いた、あるいは言うことが無かった
	ExitError = 1 // 使い方の誤り、または読めない設定
	ExitBlock = 2 // 判定を書けなかったので拒否にする
	// ExitAnswerNo は問われたことへの「いいえ」。1 と分けるのは、打ち方の誤りと提案の失敗を読む側が区別するため。
	ExitAnswerNo = 3
	// ExitPartial はターミナルの `--agree` が承認済みチケットを一部だけ置いて失敗したとき。
	// 呼び出す sh はこの値のときだけ、置かれたぶんをコミットして push する。
	ExitPartial = 4
)

// Mode は判定をどう扱うか（どう判定するかは決めない）。判定のモードも、戻す働きと Stop の AI 判定の
// 設定も同じ 3 値を取る。dry-run と enable は同じ経路を通るので、dry-run の報告は enable で起きることと一致する。
type Mode string

const (
	Disable Mode = "disable" // 判定しない
	DryRun  Mode = "dry-run" // 判定して報告する。一般の保護は手を出さず、ゲートと補助は止める
	Enable  Mode = "enable"  // 判定を呼び出しに適用する
)

// ResolveMode は設定された値をモードにする。フラグ > 設定（環境か上書き設定ファイル）。値が無ければ enable。
//
// disable を認めるのは、フラグでも設定ファイルでもなく、セッションを起動したユーザの環境から来たときだけ。
// ほかはエージェントが書ける場所で、その off を認めると監視される側が監視を止められる。
// 読めない値も enable にするが、黙ると打ち間違いが意図した選択に見えるので stderr に言う。
func ResolveMode(stderr io.Writer, flagValue string, conf *Settings) Mode {
	source, value := env.Mode, conf.Mode
	if flagValue != "" {
		source, value = "--mode", flagValue
	}
	switch Mode(pyfmt.Lower(value)) {
	case DryRun:
		return DryRun
	case Disable:
		fromFile := Mode(pyfmt.Lower(conf.ModeDeclaredInFile))
		fromEnv := Mode(pyfmt.Lower(conf.ModeFromEnvironment))
		if flagValue == "" && fromEnv == Disable && fromFile != Disable {
			return Disable
		}
		fmt.Fprintf(stderr, "ccnavi: %s=%s is ignored because it comes from inside the project; "+
			"start the session with %s=%s in the environment instead\n", source, Disable, env.Mode, Disable)
		return Enable
	case "", Enable:
		return Enable
	}
	fmt.Fprintf(stderr, "ccnavi: %s=%s is not a mode; using %s. Valid modes are %s, %s and %s\n",
		source, pyfmt.Quote(value), Enable, Disable, DryRun, Enable)
	return Enable
}

// EffectiveSetting は CCNAVI_MODE を掛けたあとの戻す働きの設定。判定を適用しないモードでは戻す側も
// ファイルに触らない（dry-run の約束の外で、頼んでいないファイル操作を起こさない）。
func EffectiveSetting(mode, declared Mode) Mode {
	if declared == Disable {
		return Disable
	}
	if mode == Enable {
		return declared
	}
	return DryRun
}

// HoldKind は止めている箇所の分類。dry-run のときの扱いを分類で決める。
type HoldKind string

const (
	// General はルールファイルの deny・ask など一般の保護。dry-run は報告だけ。
	General HoldKind = "general"
	// Gate はチケット制御のゲート。dry-run でも止める。
	Gate HoldKind = "gate"
	// Aux はゲートを外す抜け道（生の git、環境変数の指定など）を止める補助。dry-run でも止める。
	Aux HoldKind = "aux"
)

// dryRunHead は dry-run の報告の頭。
const dryRunHead = "[ccnavi dry-run] "

// DryRunWould は、dry-run で enable なら何をしていたかを言う頭の句（`[ccnavi dry-run] enable would have <what>`）。
// 実行前（止めた・聞いた）と実行後（差し戻した）の報告が同じ形で言う。
func DryRunWould(what string) string { return dryRunHead + string(Enable) + " would have " + what }

// HoldsInDryRun は dry-run でも止める分類か。
func HoldsInDryRun(kind HoldKind) bool { return kind == Gate || kind == Aux }

// FailClosedEvent はツールの呼び出しを持たないイベントが判定に達せなかったときの終了コード。
// enable は止め、dry-run は通す（自分の失敗で作業を止めない）。
func FailClosedEvent(mode Mode) int {
	if mode == DryRun {
		return ExitOK
	}
	return ExitBlock
}

// FailClosed は PreToolUse の呼び出しが判定に達せなかったときの終了コード。safe は設定を読まずに
// 「通してよい」と確かめた呼び出し。
//
//   - 空（payload が読めずツールが分からない）: 止める
//   - 報告用のツール: モードに関係なく通す
//   - 書き込みとサブエージェントの起動: dry-run でも止める
//   - 読み取りとシェル: safe のときだけ通す
//   - 知らないツール: enable は止め、dry-run は通す
func FailClosed(mode Mode, tool string, safe bool) int {
	switch {
	case tool == "":
		return ExitBlock
	case slices.Contains(reportTools, tool):
		return ExitOK
	case slices.Contains(strictTools, tool):
		return ExitBlock
	case slices.Contains(readTools, tool) || slices.Contains(shellTools, tool):
		if safe {
			return ExitOK
		}
		return ExitBlock
	}
	return FailClosedEvent(mode)
}
