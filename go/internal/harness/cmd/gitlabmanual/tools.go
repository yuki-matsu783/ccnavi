package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"ccnavi/internal/harness/gitlabapi"
	"ccnavi/internal/harness/gitlabdocker"
	"ccnavi/internal/harness/scenario/actors"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
)

// agreeTimeout は承認の代行の 1 回の上限。
const agreeTimeout = 120 * time.Second

// patPattern は端末の出力から伏せるトークンの形。
var patPattern = regexp.MustCompile(`glpat-[A-Za-z0-9_-]+`)

// runTools は docker-gitlab のシナリオを人間の操作の step で止めて続けるときの、操作者の道具。
//
//	issue: 使い捨てのプロジェクトに、人間役のトークンで issue を 1 件作る（human_api に無い操作）。
//	agree: スタブの git の環境（host.Host.StepEnv）の中で、擬似端末から y を送って承認を代行する。ユーザ専用の承認の sh は hook が止めるので、
//	       ユーザの端末で実行する。素の shell で打たない。
//	leak:  トークンの値が記録に出ていないかを、一致したファイル名だけで報告する（値は出さない）。
//
// --root はワークスペースルート（既定は、リポジトリの根）。トークンの値は端末に出さない。
func runTools(tool *gitlabdocker.Tool, args []string, repoRoot string, environ []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "使い方: gitlabmanual tools issue|agree|leak <出力の置き場> …")
		return 2
	}
	fs := newFlags("tools "+args[0], stderr)
	root := fs.String("root", repoRoot, "ワークスペースルート")
	title := fs.String("title", "README の末尾に 1 行を足す", "issue の題（issue だけ）")
	// フラグは位置引数の後ろにも置ける
	var positional []string
	rest := args[1:]
	for len(rest) > 0 {
		if err := fs.Parse(rest); err != nil {
			return 2
		}
		rest = fs.Args()
		if len(rest) > 0 {
			positional = append(positional, rest[0])
			rest = rest[1:]
		}
	}
	if len(positional) == 0 {
		fmt.Fprintln(stderr, "出力の置き場（state.json のある所）が要る")
		return 2
	}
	out, ids := positional[0], positional[1:]
	t := tools{tool: tool, root: *root, out: out, environ: environ, stdout: stdout, stderr: stderr}
	switch args[0] {
	case "issue":
		return t.issue(*title)
	case "agree":
		if len(ids) == 0 {
			fmt.Fprintln(stderr, "承認する識別子（親・子）が要る")
			return 2
		}
		return t.agree(ids)
	case "leak":
		return t.leak()
	}
	fmt.Fprintf(stderr, "知らない道具: %s\n", args[0])
	return 2
}

type tools struct {
	tool           *gitlabdocker.Tool
	root, out      string
	environ        []string
	stdout, stderr io.Writer
}

// hostOf は runner の出力の置き場の state.json から、用意した相手を読む。
func (t tools) hostOf() (*host.Host, error) {
	data, err := os.ReadFile(filepath.Join(t.out, "state.json"))
	if err != nil {
		return nil, err
	}
	var st core.State
	if err := json.Unmarshal(data, &st); err != nil {
		return nil, fmt.Errorf("state.json を読めない: %w", err)
	}
	if st.Host == nil {
		return nil, errors.New("state.json に host が無い")
	}
	return st.Host, nil
}

func (t tools) tokens() map[string]string { return gitlabapi.ReadEnvFile(t.tool.TokensPath()) }

func (t tools) issue(title string) int {
	h, err := t.hostOf()
	if err != nil {
		fmt.Fprintln(t.stderr, err)
		return 1
	}
	token := t.tokens()[gitlabapi.ReviewerTokenKey]
	if token == "" {
		fmt.Fprintf(t.stderr, "トークンのファイル %s に %s が無い\n", t.tool.TokensPath(), gitlabapi.ReviewerTokenKey)
		return 1
	}
	status, body := gitlabapi.NewClient(t.tool.Base()).CreateIssue(token, h.ProjectID, title, "")
	fmt.Fprintf(t.stdout, "status %d iid %v\n", status, gitlabapi.AsMap(body)["iid"])
	if status >= 200 && status < 300 {
		return 0
	}
	return 1
}

func (t tools) agree(ids []string) int {
	h, err := t.hostOf()
	if err != nil {
		fmt.Fprintln(t.stderr, err)
		return 1
	}
	token := t.tokens()[gitlabapi.TokenKey]
	if token == "" {
		fmt.Fprintf(t.stderr, "トークンのファイル %s に %s が無い\n", t.tool.TokensPath(), gitlabapi.TokenKey)
		return 1
	}
	env := host.Environ(h.StepEnv(t.environ, token))
	argv := append([]string{"sh", filepath.ToSlash(filepath.Join(t.root, ".ccnavi", "scripts", "ccnavi-user-agree.sh"))}, ids...)
	code, text, err := actors.RunOnTerminal(argv, t.root, env, []string{"y"}, agreeTimeout)
	// 出力に混ざるトークンは伏せる
	fmt.Fprint(t.stdout, patPattern.ReplaceAllString(text, "glpat-***"))
	switch {
	case errors.Is(err, actors.ErrNoTerminal):
		fmt.Fprintln(t.stderr, "擬似端末を作れない（script コマンドが無いか Windows）。ユーザの端末で ccnavi-user-agree.sh を打つ")
		return 1
	case err != nil:
		fmt.Fprintln(t.stderr, err)
		return 1
	}
	fmt.Fprintf(t.stdout, "\n終了コード: %d\n", code)
	return code
}

func (t tools) leak() int {
	values := map[string]string{}
	for k, v := range t.tokens() {
		v = strings.Trim(strings.TrimSpace(v), `'"`)
		if strings.Contains(k, "TOKEN") && v != "" {
			values[k] = v
		}
	}
	if len(values) == 0 {
		fmt.Fprintf(t.stderr, "トークンのファイル %s から値を読めない。gitlabmanual up で作る\n", t.tool.TokensPath())
		return 2
	}
	targets := []string{t.out, filepath.Join(t.root, "logs"), filepath.Join(t.root, "projects", "test-repo", ".git", "config")}
	hits := map[string][]string{}
	for _, target := range targets {
		_ = filepath.Walk(target, func(path string, info os.FileInfo, err error) error {
			if err != nil || info.IsDir() {
				return nil
			}
			data, err := os.ReadFile(path)
			if err != nil {
				return nil
			}
			for k, v := range values {
				if strings.Contains(string(data), v) {
					hits[k] = append(hits[k], path)
				}
			}
			return nil
		})
	}
	names := map[string]string{gitlabapi.TokenKey: "エージェント役", gitlabapi.ReviewerTokenKey: "人間役"}
	found := false
	for _, k := range sortedKeys(values) {
		name := names[k]
		if name == "" {
			name = k
		}
		files := hits[k]
		fmt.Fprintf(t.stdout, "%s: %d 件\n", name, len(files))
		for i, f := range files {
			if i == 20 {
				break
			}
			fmt.Fprintf(t.stdout, "   %s\n", f)
		}
		found = found || len(files) > 0
	}
	if found {
		return 1
	}
	return 0
}

func sortedKeys(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
