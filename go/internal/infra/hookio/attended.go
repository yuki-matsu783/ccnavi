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

// 付き添いの有無は 2 つの関数で見る。誤ったときの害の向きが逆なので、疑わしい実行（SDK から起こしたが
// Claude Code が "0" と示していないもの）をどちらに数えるかが違う。
//   - Unattended（hook の判定）: 疑わしければ付き添いありに数える。付き添いなしと誤ると、ユーザに聞くべきところで
//     確認を出さずに止めたり自走したりする。SDK の上にも、確認に人が答えるアプリがある。
//   - Headless（引き継ぎの待機）: 疑わしければ付き添いなしに数える。付き添いありと誤ると、見ている人がいない
//     `claude -p` のプロセスを待機が引き止める。
// 見るのは Claude Code が文書にしていない環境変数なので、付き添いの判定はどれもこの 2 つを通す。

// Unattended は非対話のセッションか。Claude Code が付き添いなしと示したとき（環境変数が "0"）だけ真。
// 未設定・"1"・読めない値は対話として扱う。
func Unattended() bool { return unattended(os.Getenv) }

func unattended(getenv func(string) string) bool {
	return getenv(env.ClaudeSessionAttended) == unattendedValue
}

// Headless は見ている人がいない実行（`claude -p`・SDK）か。Unattended が真のときと、入口が SDK のとき真。
func Headless() bool { return headless(os.Getenv) }

func headless(getenv func(string) string) bool {
	return unattended(getenv) || strings.HasPrefix(getenv(env.ClaudeCodeEntrypoint), sdkEntrypoint)
}
