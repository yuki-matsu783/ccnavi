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
package main

import (
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"ccnavi/internal/harness/hostfixture"
	"ccnavi/internal/harness/hoststub"
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
