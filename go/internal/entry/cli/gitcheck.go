package cli

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/lifecycle/gitwrap"
)

// runGitCheck は `ccnavi git check <自分の呼び方> <ccnavi-sync.sh の呼び方>`（ccnavi-git.sh 用）。git の引数を標準入力から
// NUL 区切りで読み、通すかを答える: `allow`・`pass`、または `deny <識別子>` と次の行の拒否の文。
// 引数を ccnavi の引数として受けないのは、`-m` のような語を ccnavi 自身のオプションとして読まないため。
func runGitCheck(in *input, stdout, stderr io.Writer, words []string) int {
	if len(words) != 2 {
		fmt.Fprintln(stderr, "ccnavi: git check <自分の呼び方> <ccnavi-sync.sh の呼び方>（git の引数は標準入力に NUL 区切りで渡す）")
		return config.ExitError
	}
	data, err := io.ReadAll(in.buf)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: git の引数を読めない（%s）\n", err)
		return config.ExitError
	}
	args := strings.Split(strings.TrimSuffix(string(data), "\x00"), "\x00")
	if len(data) == 0 {
		args = nil
	}
	verdict, deny := gitwrap.Check(gitwrap.Names{Self: words[0], Sync: words[1]}, args)
	if deny != nil {
		fmt.Fprintf(stdout, "deny %s\n%s\n", deny.ID, deny.Message)
		return config.ExitOK
	}
	fmt.Fprintln(stdout, verdict)
	return config.ExitOK
}
