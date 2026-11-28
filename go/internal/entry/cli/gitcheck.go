package cli

import (
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"ccnavi/internal/entry/shask"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/gitlog"
	"ccnavi/internal/tickets/lifecycle/gitwrap"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/lifecycle/land"
	"ccnavi/internal/tickets/lifecycle/refusal"
)

// gitCheckUsage は `ccnavi git check` の使い方。
const gitCheckUsage = "ccnavi: git check <自分の呼び方> <ccnavi-sync.sh の呼び方> <ccnavi-start.sh の呼び方> <自分の呼び方（引用済み）> <ccnavi-worktree.sh の呼び方（引用済み）>（git の引数は標準入力に NUL 区切りで渡す）"

// runGitCheck は `ccnavi git check <呼び方>...`（ccnavi-git.sh 用）。git の引数を標準入力から NUL 区切りで読み、git の状態を見て
// 通すかを答える。1 行目は `allow`・`pass`（続きの判定はラッパーが持つ）、または `deny <識別子>` と次の行の拒否の文。
// allow に続けて、`prepend <語>`（git の引数の前に足す）・`warn <文>`（出力の後ろに出す）・`gate-top <パス>` と
// `gate-branch <ブランチ>`（push の前に、リモートに親のブランチが在るかを確かめる）・`target <統合先>`（統合先を
// チェックアウトしているユーザのチェックアウト）の行が付くことがある。`integrate`・`integrate-done` には、ロックを分ける
// 名前の `repo <名前>` の行が付く。
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
	// 直接作業の取り込みは、ラッパーがロックを取ってから `git integrate` を呼ぶ。ロックを分ける名前（cwd のツリーの
	// プロジェクト。ワークスペースは self）をここで答える。
	if len(args) > 0 && (args[0] == "integrate" || args[0] == "integrate-done") {
		here, _ := tree.TreeOf(root, cwd, integrate.ProjectsDir(conf, root))
		lines = append(lines, "repo "+tree.RepoKey(here.Project))
	}
	fmt.Fprintln(stdout, strings.Join(lines, "\n"))
	return config.ExitOK
}

// gitIntegrateUsage は `ccnavi git integrate` の使い方。
const gitIntegrateUsage = "ccnavi: git integrate <自分の呼び方> <自分の呼び方（引用済み）> <ccnavi-worktree.sh の呼び方（引用済み）> <ccnavi-ticket.sh の呼び方（引用済み）> <一覧の行数> -- integrate|integrate-done <引数>..."

// runGitIntegrate は `ccnavi git integrate <呼び方>... -- <git の引数>...`（ccnavi-git.sh 用）。直接作業の取り込み
// （integrate・integrate --abort・integrate-done）を行い、出力をそのまま標準出力・標準エラーに書く。終了コードは 0（済んだ）・
// 1（止めた）・2（拒否）。ロックはラッパーが取ってから呼ぶ。標準入力が端末かは、ここで自分で見る（ラッパーの言葉を
// 信じると、実行ファイルを直に打って「ターミナルから打つ」決まりを迂回できるため）。
func runGitIntegrate(in *input, stdout, stderr io.Writer, root string, conf *config.Settings, words []string) int {
	const names = 5
	if len(words) <= names || words[names] != "integrate" && words[names] != "integrate-done" {
		fmt.Fprintln(stderr, gitIntegrateUsage)
		return config.ExitError
	}
	lines, err := strconv.Atoi(words[4])
	if err != nil {
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
		Root:  root, Cwd: cwd, Conf: conf, Terminal: in.terminal(), MaxLines: lines,
	}, stdout, stderr, words[names:])
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

// runGitIntegration は `ccnavi git integration <ツリー>`（sh の ccnavi_integration・ccnavi_integration_hint）。そのツリーが
// 属するリポジトリの統合先が決まれば `integration <名前>`、決まらなければ `hint <案内の 1 文>` を書く
// （決め方は integrate.IntegrationName、案内は integrate.IntegrationHint）。
func runGitIntegration(stdout io.Writer, top string) int {
	if name := integrate.IntegrationName(top); name != "" {
		fmt.Fprintf(stdout, "integration %s\n", name)
		return config.ExitOK
	}
	fmt.Fprintf(stdout, "hint %s\n", integrate.IntegrationHint(top))
	return config.ExitOK
}

// runGitRefusal は `ccnavi git refusal <ファイル>`（sh の ccnavi_git_refusal）。git の標準エラーを書いたファイルから、
// git が断った理由の 1 文（refusal.Of）を `refusal <文>` で書く。ファイルが読めなければ空の出力として読む。
func runGitRefusal(stdout io.Writer, path string) int {
	text, _ := os.ReadFile(path)
	fmt.Fprintf(stdout, "refusal %s\n", strings.NewReplacer("\r", " ", "\n", " ").Replace(refusal.Of(string(text))))
	return config.ExitOK
}

// gitReportUsage は `ccnavi git report` の使い方。
const gitReportUsage = "ccnavi: git report <終了コード> <出力のファイル> <行数> <失敗の行数> <残す本数> <自分の呼び方（引用済み）> <警告> <統合先>（サブコマンドと git の引数は標準入力に NUL 区切りで渡す）"

// runGitReport は `ccnavi git report …`（ccnavi-git.sh 用）。ラッパーが打った git の出力（ファイル）を記録に残し、要約と
// 本文の一部を標準出力に書く（gitlog.Report）。記録の置き場は、cwd がプロジェクトの中なら logs/<プロジェクト>/、ほかは
// logs/。書けなければ理由を標準エラーに書いて 1。
func runGitReport(in *input, stdout, stderr io.Writer, root string, conf *config.Settings, words []string) int {
	if len(words) != 8 {
		fmt.Fprintln(stderr, gitReportUsage)
		return config.ExitError
	}
	nums := make([]int, 0, 4)
	for _, w := range []string{words[0], words[2], words[3], words[4]} {
		n, err := strconv.Atoi(w)
		if err != nil {
			fmt.Fprintln(stderr, gitReportUsage)
			return config.ExitError
		}
		nums = append(nums, n)
	}
	args, ok := nulArgs(in, stderr)
	if !ok || len(args) == 0 {
		fmt.Fprintln(stderr, gitReportUsage)
		return config.ExitError
	}
	cwd, err := os.Getwd()
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: 作業場所を読めない（%s）\n", err)
		return config.ExitError
	}
	output, err := os.ReadFile(words[1])
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: git の出力 %s を読めない（%s）\n", words[1], err)
		return config.ExitError
	}
	logDir := ospath.Join(root, "logs")
	if t, ok := tree.TreeOf(root, cwd, integrate.ProjectsDir(conf, root)); ok && t.Project != "" {
		logDir = ospath.Join(logDir, t.Project)
	}
	_, _, err = gitlog.Report(stdout, gitlog.Input{
		Root: root, Cwd: cwd, LogDir: logDir, Sub: args[0], Args: args[1:], Status: nums[0], Output: output,
		MaxLines: nums[1], FailLines: nums[2], Keep: nums[3], SelfQ: words[5], Warn: words[6], Target: words[7],
	})
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: git の記録を書けない（%s）\n", err)
		return config.ExitError
	}
	return config.ExitOK
}
