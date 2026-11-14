package cli

import (
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"ccnavi/internal/entry/shask"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/lifecycle/gitwrap"
	"ccnavi/internal/tickets/lifecycle/land"
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
	args, ok := nulArgs(in, stderr)
	if !ok {
		return config.ExitError
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

// gitIntegrateUsage は `ccnavi git integrate` の使い方。
const gitIntegrateUsage = "ccnavi: git integrate <自分の呼び方> <自分の呼び方（引用済み）> <ccnavi-worktree.sh の呼び方（引用済み）> <ccnavi-ticket.sh の呼び方（引用済み）> <terminal|pipe> <一覧の行数>（integrate または integrate-done で始まる git の引数は標準入力に NUL 区切りで渡す）"

// runGitIntegrate は `ccnavi git integrate <呼び方>...`（ccnavi-git.sh 用）。直接作業の取り込み（integrate・integrate --abort・
// integrate-done）を行い、出力をそのまま標準出力・標準エラーに書く。終了コードは 0（済んだ）・1（止めた）・2（拒否）。
// ロックはラッパーが取ってから呼ぶ。
func runGitIntegrate(in *input, stdout, stderr io.Writer, root string, conf *config.Settings, words []string) int {
	args, ok := nulArgs(in, stderr)
	lines, err := strconv.Atoi(wordAt(words, 5))
	if len(words) != 6 || !ok || err != nil || len(args) == 0 || args[0] != "integrate" && args[0] != "integrate-done" {
		fmt.Fprintln(stderr, gitIntegrateUsage)
		return config.ExitError
	}
	cwd, err := os.Getwd()
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: 作業場所を読めない（%s）\n", err)
		return config.ExitError
	}
	return land.Run(land.Input{
		Names: land.Names{Self: words[0], SelfQ: words[1], WorktreeQ: words[2], TicketQ: words[3]},
		Root:  root, Cwd: cwd, Conf: conf, Terminal: words[4] == "terminal", MaxLines: lines,
	}, stdout, stderr, args)
}

// wordAt は words[i]（無ければ空）。
func wordAt(words []string, i int) string {
	if i < len(words) {
		return words[i]
	}
	return ""
}

// nulArgs は標準入力から NUL 区切りの git の引数を読む。読めなければ理由を書いて偽。
func nulArgs(in *input, stderr io.Writer) ([]string, bool) {
	data, err := io.ReadAll(in.buf)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: git の引数を読めない（%s）\n", err)
		return nil, false
	}
	if len(data) == 0 {
		return nil, true
	}
	return strings.Split(strings.TrimSuffix(string(data), "\x00"), "\x00"), true
}
