// teststub は、テストが sh の中から起こすスタブを 1 本にまとめた実行ファイルである。
//
//	teststub <サブコマンド> <引数…>
//
// 起こし方は PATH に置く sh の 2 行（`#!/bin/sh` と `exec '<teststub>' <サブコマンド> "$@"`）。harness/exe が指紋で組んで取っておく
// （exe.TeststubPath）。サブコマンドの表は commands で、足すときは 1 行足す。
//
//	curl         routes.json で答える curl（hoststub.Curl）
//	gitlab-host  見本を返す GitLab のスタブ。`gitlab-host curl <引数…>`（hostfixture.GitlabCurl）
//	github-host  見本を返す GitHub のスタブ。`github-host curl|gh <引数…>`（hostfixture.GithubCurl・GithubGh）
//	userform     `user` の入口の並びを実行ファイルと同じに検べる（samples.UserformMain）
//	oprecord     操作記録と、その操作ID を入れた計画を書く（samples.OprecordMain）
//	claude       `claude -p` の台本スタブ（fakeclaude.Run）。台本は環境変数 CCNAVI_FAKE_CLAUDE_SCRIPT
//	claude-hook  契約の場面の hook（fakeclaude.RecordHook）。`claude-hook <記録の置き場> <目印>`
package main

import (
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"ccnavi/internal/harness/fakeclaude"
	"ccnavi/internal/harness/hostfixture"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/samples"
)

// command は 1 つのサブコマンド。args はサブコマンドの名前より後ろ。
type command func(args []string, stdin io.Reader, stdout, stderr io.Writer) int

// commands はサブコマンドの表。
var commands = map[string]command{
	"curl": func(a []string, in io.Reader, out, errw io.Writer) int {
		return hoststub.Curl(a, in, out, errw, os.Getenv)
	},
	"gitlab-host": hostCommand("gitlab-host", map[string]hostFunc{"curl": hostfixture.GitlabCurl}),
	"github-host": hostCommand("github-host", map[string]hostFunc{"curl": hostfixture.GithubCurl, "gh": hostfixture.GithubGh}),
	"userform": func(a []string, _ io.Reader, _, errw io.Writer) int {
		return samples.UserformMain(a, errw)
	},
	"oprecord": func(a []string, _ io.Reader, _, errw io.Writer) int {
		return samples.OprecordMain(a, errw)
	},
	"claude": func(a []string, _ io.Reader, out, errw io.Writer) int {
		cwd, err := os.Getwd()
		if err != nil {
			fmt.Fprintln(errw, err)
			return 1
		}
		return fakeclaude.Run(a, os.Environ(), cwd, out, errw)
	},
	"claude-hook": func(a []string, in io.Reader, out, errw io.Writer) int {
		return fakeclaude.RecordHook(a, in, out, errw, os.Environ())
	},
}

type hostFunc func(args []string, stdin io.Reader, stdout, stderr io.Writer, getenv hostfixture.Getenv) int

// hostCommand は `<名前> <curl|gh> <引数…>` を振り分ける。
func hostCommand(name string, tools map[string]hostFunc) command {
	return func(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
		if len(args) > 0 {
			if run, ok := tools[args[0]]; ok {
				return run(args[1:], stdin, stdout, stderr, os.Getenv)
			}
		}
		var names []string
		for n := range tools {
			names = append(names, n)
		}
		sort.Strings(names)
		fmt.Fprintf(stderr, "使い方: teststub %s %s <引数>...\n", name, strings.Join(names, "|"))
		return 2
	}
}

func main() { os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }

func run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	if len(args) > 0 {
		if cmd, ok := commands[args[0]]; ok {
			return cmd(args[1:], stdin, stdout, stderr)
		}
	}
	var names []string
	for n := range commands {
		names = append(names, n)
	}
	sort.Strings(names)
	fmt.Fprintf(stderr, "使い方: teststub %s <引数>...\n", strings.Join(names, "|"))
	return 2
}
