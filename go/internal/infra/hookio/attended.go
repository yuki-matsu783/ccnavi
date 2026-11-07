package hookio

import (
	"os"
	"strings"

	"ccnavi/internal/infra/env"
)

const (
	// unattendedValue は Claude Code が付き添いなしと示す値。
	unattendedValue = "0"
	// sdkEntrypoint は SDK から起こした実行の入口の名前の頭。
	sdkEntrypoint = "sdk"
)

// Unattended は非対話のセッションか。Claude Code が付き添いなしと示したとき（環境変数が "0"）だけ真。
// 未設定・"1"・読めない値は対話として扱う（非対話と誤ると、聞くべきところで自走するので害が大きい）。
// 見るのは Claude Code が文書にしていない環境変数なので、hook の判定はどれもこれを呼ぶ。
func Unattended() bool { return unattended(os.Getenv) }

func unattended(getenv func(string) string) bool {
	return getenv(env.ClaudeSessionAttended) == unattendedValue
}

// Headless は見ている人がいない実行（`claude -p`・SDK）か。Unattended より広く、入口が SDK の実行も含める。
// 放置したセッションの引き継ぎの待機だけが使う（待機がプロセスを引き止めるので、疑わしければ除く側に倒す）。
// hook の判定は聞くべきところで自走しないよう狭い Unattended を使う。Python も 2 つを分けている。
func Headless(getenv func(string) string) bool {
	return unattended(getenv) || strings.HasPrefix(getenv(env.ClaudeCodeEntrypoint), sdkEntrypoint)
}
