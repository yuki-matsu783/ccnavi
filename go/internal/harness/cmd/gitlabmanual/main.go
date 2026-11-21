// gitlabmanual は、本物の GitLab（Docker）を手で立てて 1 周する道具の入口である。自動テストは呼ばない。手順は `.agents/docs/gitlab-manual.md`。
//
//	cd go && go run ./internal/harness/cmd/gitlabmanual <サブコマンド> [引数…]
//
//	up [--pull] [--rotate]   Docker の GitLab CE を起動して、応答するまで待つ（gitlabdocker）
//	status                   状態・版・トークンの有効性
//	down [--purge]           止めて消す（--purge でボリュームと状態のファイルも）
//	probe                    動いている GitLab に使い捨てのプロジェクトを作り、sh と実行ファイルを 1 周させる（gitlabprobe）
//	record-contract [--dry-run]
//	                         本物に requests.json の列を送り、応答の形を tests/contract/gitlab/recorded.json に録る（contractrecord）
//	tools issue <出力の置き場> [--title 題] [--root 根]
//	tools agree <出力の置き場> <識別子>... [--root 根]
//	tools leak  <出力の置き場> [--root 根]
//	                         docker-gitlab のシナリオを人間の操作の step で止めたときの操作者の道具（tools.go）
//
// リポジトリの中で打つ（go/go.mod を持つ親を根にする）。
package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"ccnavi/internal/harness/contractrecord"
	cs "ccnavi/internal/harness/contractshape"
	"ccnavi/internal/harness/gitlabapi"
	"ccnavi/internal/harness/gitlabdocker"
	"ccnavi/internal/harness/gitlabprobe"
	"ccnavi/internal/infra/env"
)

const usage = `使い方: gitlabmanual <サブコマンド>
  up [--pull] [--rotate]            Docker の GitLab CE を起動して、応答するまで待つ
  status                            状態・版・トークンの有効性
  down [--purge]                    止めて消す
  probe                             sh と実行ファイルを実物の GitLab で 1 周する（--help で説明）
  record-contract [--dry-run]       応答の形を recorded.json に録る
  tools issue|agree|leak <出力の置き場> …  シナリオを人間の操作の step で止めたときの道具`

func main() {
	cwd, err := os.Getwd()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	os.Exit(run(os.Args[1:], cwd, os.Environ(), os.Stdout, os.Stderr))
}

func getenvFrom(environ []string) func(string) string {
	return func(name string) string {
		for i := len(environ) - 1; i >= 0; i-- {
			if v, ok := strings.CutPrefix(environ[i], name+"="); ok {
				return v
			}
		}
		return ""
	}
}

// run は引数を振り分ける。終了コードを返す。
func run(args []string, cwd string, environ []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, usage)
		return 2
	}
	if args[0] == "-h" || args[0] == "--help" {
		fmt.Fprintln(stdout, usage)
		return 0
	}
	root, err := gitlabdocker.FindRoot(cwd)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	home, _ := os.UserHomeDir()
	getenv := getenvFrom(environ)
	tool := &gitlabdocker.Tool{Root: root, Home: home, Getenv: getenv, Environ: environ, Out: stdout, Err: stderr}
	rest := args[1:]
	switch args[0] {
	case "up":
		fs := newFlags("up", stderr)
		pull := fs.Bool("pull", false, "イメージが無いとき取得する")
		rotate := fs.Bool("rotate", false, "トークンを作り直す")
		if code, ok := parse(fs, rest); !ok {
			return code
		}
		return tool.Up(*pull, *rotate)
	case "status":
		if code, ok := parse(newFlags("status", stderr), rest); !ok {
			return code
		}
		return tool.Status()
	case "down":
		fs := newFlags("down", stderr)
		purge := fs.Bool("purge", false, "ボリュームと状態のファイルも消す")
		if code, ok := parse(fs, rest); !ok {
			return code
		}
		return tool.Down(*purge)
	case "probe":
		return gitlabprobe.Main(rest, root, probeEnviron(tool, environ, getenv), stdout, stderr)
	case "record-contract":
		fs := newFlags("record-contract", stderr)
		dry := fs.Bool("dry-run", false, "置かずに標準出力へ")
		if code, ok := parse(fs, rest); !ok {
			return code
		}
		return recordContract(tool, getenv, *dry, stdout, stderr)
	case "tools":
		return runTools(tool, rest, root, environ, stdout, stderr)
	}
	fmt.Fprintf(stderr, "知らないサブコマンド: %s\n%s\n", args[0], usage)
	return 2
}

func newFlags(name string, stderr io.Writer) *flag.FlagSet {
	fs := flag.NewFlagSet(name, flag.ContinueOnError)
	fs.SetOutput(stderr)
	return fs
}

func parse(fs *flag.FlagSet, args []string) (int, bool) {
	if err := fs.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return 0, false
		}
		return 2, false
	}
	if fs.NArg() > 0 {
		fmt.Fprintf(fs.Output(), "余計な引数: %v\n", fs.Args())
		return 2, false
	}
	return 0, true
}

// probeEnviron は probe に渡す環境。環境変数に無いトークンは、up が書いたトークンのファイルで補う。接続先は同じポートの Docker の GitLab。
func probeEnviron(tool *gitlabdocker.Tool, environ []string, getenv func(string) string) []string {
	token, reviewer, _ := contractrecord.ConfigFromEnv(tool.TokensPath(), getenv)
	out := append([]string{}, environ...)
	if getenv(env.ProbeGitLab) == "" {
		out = append(out, env.ProbeGitLab+"="+tool.Base())
	}
	return append(out, env.GitLabToken+"="+token, env.ProbeReviewerToken+"="+reviewer)
}

func recordContract(tool *gitlabdocker.Tool, getenv func(string) string, dry bool, stdout, stderr io.Writer) int {
	cs.Root = tool.Root // 契約のデータ（tests/contract/gitlab）の根
	base := getenv(env.ProbeGitLab)
	if base == "" {
		base = tool.Base()
	}
	token, reviewer, id := contractrecord.ConfigFromEnv(tool.TokensPath(), getenv)
	return contractrecord.Record(contractrecord.Config{
		Root: tool.Root, API: gitlabapi.NewClient(base),
		Token: token, ReviewerToken: reviewer, ReviewerID: id,
		Image: gitlabdocker.Image, RunningImage: tool.RunningImage,
		Keep:   getenv(env.ProbeKeep) == "1",
		Stdout: stdout, Stderr: stderr, DryRun: dry,
	})
}
