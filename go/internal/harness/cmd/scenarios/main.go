// scenarios は、受入シナリオを回す実行器の入口である（tests/scenarios/runner.py の写し）。手順と決まりは
// .agents/docs/acceptance-scenarios.md。普段のテストには含めない。人間に確認を取ってから、明確な指示があったときだけ回す。
//
//	go run ./internal/harness/cmd/scenarios preflight <シナリオ>
//	go run ./internal/harness/cmd/scenarios run <シナリオ> [--confirmed] [--agent script] [--root <ルート>] [--out <置き場>]
//	go run ./internal/harness/cmd/scenarios run-stub <ディレクトリ or glob>...
//	go run ./internal/harness/cmd/scenarios run-all <...> --max-total-usd <USD> [--confirmed <目印>] [--root <ルート>] [--out <置き場>]
//	go run ./internal/harness/cmd/scenarios next <出力の置き場>
//	go run ./internal/harness/cmd/scenarios restore <出力の置き場>
//	go run ./internal/harness/cmd/scenarios judge <出力の置き場> [<判定の表のファイル>] [--accept]
//
// 終了コードと各サブコマンドの意味は harness/scenario/runner の doc。go/ で打つ。
// 組んだ ccnavi・teststub は、環境変数 CCNAVI_TEST_EXE・CCNAVI_TEST_STUB_EXE があればそれ、無ければ go build で組む。
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/harness/scenario/runner"
	"ccnavi/internal/harness/scenario/world"
	"ccnavi/internal/infra/env"
)

func main() { os.Exit(run(os.Args[1:])) }

// args は argparse と同じく、位置引数とオプションを入れ替えて書ける。
type args struct {
	pos   []string
	flags map[string]string
}

func parse(argv []string, bools, values []string) (args, error) {
	a := args{flags: map[string]string{}}
	has := func(list []string, s string) bool {
		for _, x := range list {
			if x == s {
				return true
			}
		}
		return false
	}
	for i := 0; i < len(argv); i++ {
		s := argv[i]
		switch {
		case strings.HasPrefix(s, "--") && has(bools, s[2:]):
			a.flags[s[2:]] = "true"
		case strings.HasPrefix(s, "--") && has(values, strings.SplitN(s[2:], "=", 2)[0]):
			name, val, ok := strings.Cut(s[2:], "=")
			if !ok {
				if i+1 >= len(argv) {
					return a, fmt.Errorf("%s には値が要る", s)
				}
				i++
				val = argv[i]
			}
			a.flags[name] = val
		case strings.HasPrefix(s, "--"):
			return a, fmt.Errorf("知らないオプション: %s", s)
		default:
			a.pos = append(a.pos, s)
		}
	}
	return a, nil
}

func run(argv []string) int {
	if len(argv) == 0 {
		fmt.Fprintln(os.Stderr, "使い方: scenarios preflight|run|run-stub|run-all|next|restore|judge ...（先頭の説明を見る）")
		return 2
	}
	cmd, rest := argv[0], argv[1:]
	repoRoot, goDir, err := locate()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	tools := &toolchain{goDir: goDir}
	r := &runner.Runner{
		RepoRoot: repoRoot,
		Manager: &host.Manager{
			Git: world.Git,
			// hoststub.Build は testing を取るので、入口の実行ファイルからは置けない（bare・fake-gitlab・docker-gitlab は回せない）
			BuildStub: func(string, bool) (hoststub.Stub, error) {
				return hoststub.Stub{}, fmt.Errorf("この入口からは PATH のスタブを置けない（hoststub が testing に依存している。setup.host が real のシナリオだけ回せる）")
			},
		},
		Ccnavi: tools.ccnavi, Teststub: tools.teststub, Entry: true,
	}
	need := func(a args, n int, usage string) bool {
		if len(a.pos) < n {
			fmt.Fprintln(os.Stderr, "使い方: scenarios "+usage)
			return false
		}
		return true
	}
	switch cmd {
	case "preflight":
		a, err := parse(rest, nil, []string{"root"})
		if err != nil || !need(a, 1, "preflight <シナリオ> [--root <ルート>]") {
			return 2
		}
		return r.Preflight(a.pos[0], a.flags["root"])
	case "run":
		a, err := parse(rest, []string{"confirmed"}, []string{"root", "out", "agent"})
		if err != nil || !need(a, 1, "run <シナリオ> [--confirmed] [--agent claude|script]") {
			return 2
		}
		if ag := a.flags["agent"]; ag != "" && ag != core.AgentClaude && ag != core.AgentScript {
			fmt.Fprintln(os.Stderr, "--agent は claude か script です")
			return 2
		}
		return r.Run(runner.RunOptions{Scenario: a.pos[0], Confirmed: a.flags["confirmed"] != "", Root: a.flags["root"], Out: a.flags["out"], Agent: a.flags["agent"]})
	case "run-stub":
		a, err := parse(rest, nil, nil)
		if err != nil || !need(a, 1, "run-stub <ディレクトリ or glob>...") {
			return 2
		}
		return r.RunStubCommand(a.pos)
	case "run-all":
		a, err := parse(rest, nil, []string{"max-total-usd", "confirmed", "root", "out"})
		if err != nil || !need(a, 1, "run-all <...> --max-total-usd <USD> [--confirmed <目印>]") {
			return 2
		}
		usd, perr := strconv.ParseFloat(a.flags["max-total-usd"], 64)
		if perr != nil {
			fmt.Fprintln(os.Stderr, "--max-total-usd は数で必須です")
			return 2
		}
		o := runner.RunAllOptions{Scenarios: a.pos, MaxTotalUSD: usd, Root: a.flags["root"], Out: a.flags["out"]}
		if c, ok := a.flags["confirmed"]; ok {
			o.Confirmed = &c
		}
		return r.RunAll(o)
	case "next", "restore":
		a, err := parse(rest, nil, nil)
		if err != nil || !need(a, 1, cmd+" <出力の置き場>") {
			return 2
		}
		if cmd == "next" {
			return r.Next(a.pos[0])
		}
		return r.Restore(a.pos[0])
	case "judge":
		a, err := parse(rest, []string{"accept"}, nil)
		if err != nil || !need(a, 1, "judge <出力の置き場> [<判定の表のファイル>] [--accept]") {
			return 2
		}
		file := ""
		if len(a.pos) > 1 {
			file = a.pos[1]
		}
		return r.Judge(a.pos[0], file, a.flags["accept"] != "")
	}
	fmt.Fprintln(os.Stderr, "知らないサブコマンド: "+cmd)
	return 2
}

// locate はリポジトリの根と go/ を、作業ディレクトリから上って探す。
func locate() (repoRoot, goDir string, err error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go", "go.mod")); err == nil {
			return dir, filepath.Join(dir, "go"), nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", "", fmt.Errorf("リポジトリの根（go/go.mod の親）が見つからない。go/ の中で打つ")
		}
		dir = parent
	}
}

// toolchain は組んだ ccnavi・teststub を用意する。
type toolchain struct {
	goDir string
	dir   string
}

func (t *toolchain) ccnavi() (string, error) {
	return t.get(env.TestExe, "./cmd/ccnavi", "ccnavi")
}

func (t *toolchain) teststub() (string, error) {
	return t.get(env.TestStubExe, "./internal/harness/cmd/teststub", "teststub")
}

func (t *toolchain) get(variable, pkg, name string) (string, error) {
	if p := os.Getenv(variable); p != "" {
		return filepath.Abs(p)
	}
	if t.dir == "" {
		d, err := os.MkdirTemp("", "ccnavi-scenarios-")
		if err != nil {
			return "", err
		}
		t.dir = d
	}
	out := filepath.Join(t.dir, name)
	if runtime.GOOS == "windows" {
		out += ".exe"
	}
	if _, err := os.Stat(out); err == nil {
		return out, nil
	}
	cmd := exec.Command("go", "build", "-o", out, pkg)
	cmd.Dir = t.goDir
	if raw, err := cmd.CombinedOutput(); err != nil {
		return "", fmt.Errorf("go build %s に失敗した: %v\n%s", pkg, err, raw)
	}
	return out, nil
}
