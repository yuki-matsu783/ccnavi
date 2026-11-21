package world

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"strings"
	"time"

	"ccnavi/internal/harness/scenario/host"
)

// GitTimeout は git 1 回の上限。
const GitTimeout = 120 * time.Second

// gitDrops は cwd で対象を決めさせるため、git の向き先を変える環境変数。
var gitDrops = []string{
	"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
	"GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS",
}

// DropGitVars は environ（`名前=値` の並び）から、git の向き先を変える変数を外す。
func DropGitVars(environ []string) []string {
	out := make([]string, 0, len(environ))
next:
	for _, kv := range environ {
		name, _, _ := strings.Cut(kv, "=")
		for _, d := range gitDrops {
			if name == d {
				continue next
			}
		}
		out = append(out, kv)
	}
	return out
}

// Git は本物の git を 1 回実行する（host.GitFunc）。環境は親の環境から向き先の変数を外し、o.Env を足す。
// 起動できなかったときは終了コード -1 で、標準エラーに理由を入れる。
func Git(args []string, o host.GitOpts) host.GitResult {
	ctx, cancel := context.WithTimeout(context.Background(), GitTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Dir = o.Dir
	environ := DropGitVars(os.Environ())
	for k, v := range o.Env {
		environ = append(environ, k+"="+v)
	}
	cmd.Env = environ
	if o.Input != "" {
		cmd.Stdin = strings.NewReader(o.Input)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	res := host.GitResult{Stdout: stdout.String(), Stderr: stderr.String()}
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			res.Code = ee.ExitCode()
		} else {
			res.Code, res.Stderr = -1, err.Error()
		}
	}
	return res
}

func firstLine(text string) string {
	text = strings.TrimSpace(text)
	if text == "" {
		return ""
	}
	return strings.SplitN(text, "\n", 2)[0]
}

func out(r host.GitResult) string { return strings.TrimSpace(r.Stdout) }

func hostOpts(dir string) host.GitOpts { return host.GitOpts{Dir: dir} }
