package cli

import (
	"fmt"
	"io"
	"os"
	"strings"

	"ccnavi/internal/entry/shask"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/lifecycle/gitwrap"
)

// gitCheckUsage は `ccnavi git check` の使い方。
const gitCheckUsage = "ccnavi: git check <自分の呼び方> <ccnavi-sync.sh の呼び方> <ccnavi-start.sh の呼び方> <自分の呼び方（引用済み）> <ccnavi-worktree.sh の呼び方（引用済み）>（git の引数は標準入力に NUL 区切りで渡す）"

// runGitCheck は `ccnavi git check <呼び方>...`（ccnavi-git.sh 用）。git の引数を標準入力から NUL 区切りで読み、git の状態を見て
// 通すかを答える。1 行目は `allow`・`pass`（続きの判定はラッパーが持つ）、または `deny <識別子>` と次の行の拒否の文。
// allow に続けて、`prepend <語>`（git の引数の前に足す）・`warn <文>`（出力の後ろに出す）・`gate-top <パス>` と
// `gate-branch <ブランチ>`（push の前に、リモートに親のブランチが在るかを確かめる）・`target <統合先>`（統合先を
// チェックアウトしているユーザのチェックアウト）の行が付くことがある。
// 引数を ccnavi の引数として受けないのは、`-m` のような語を ccnavi 自身のオプションとして読まないため。
func runGitCheck(in *input, stdout, stderr io.Writer, root string, conf *config.Settings, words []string) int {
	if len(words) != 5 {
		fmt.Fprintln(stderr, gitCheckUsage)
		return config.ExitError
	}
	cwd, err := os.Getwd()
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: 作業場所を読めない（%s）\n", err)
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
	env := &gitwrap.Env{
		Names: gitwrap.Names{Self: words[0], Sync: words[1], Start: words[2], SelfQ: words[3], WorktreeQ: words[4]},
		Root:  root, Cwd: cwd,
		ParentTree: func(top, name string) int { return shask.IsParentTree(root, conf, top, name) },
	}
	a := gitwrap.Judge(env, args)
	if a.Deny != nil {
		fmt.Fprintf(stdout, "deny %s\n%s\n", a.Deny.ID, a.Deny.Message)
		return config.ExitOK
	}
	lines := []string{a.Verdict}
	for _, word := range a.Prepend {
		lines = append(lines, "prepend "+word)
	}
	for _, kv := range [][2]string{{"warn", a.Warn}, {"gate-top", a.GateTop}, {"gate-branch", a.GateBranch}, {"target", a.Target}} {
		if kv[1] != "" {
			lines = append(lines, kv[0]+" "+kv[1])
		}
	}
	fmt.Fprintln(stdout, strings.Join(lines, "\n"))
	return config.ExitOK
}
