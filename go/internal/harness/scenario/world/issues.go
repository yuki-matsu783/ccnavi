package world

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/harness/scenario/core"
	modelhost "ccnavi/internal/tickets/model/host"
)

// IssuePlaceholder は prompt の中で、作った issue の番号に置き換える印。
const IssuePlaceholder = "{issue}"

// issueTimeout は gh・glab 1 回の上限。
const issueTimeout = 120 * time.Second

// HostCLI はホストの種類から CLI の名前を返す。GitLab は glab、GitHub は gh。
func HostCLI(kind modelhost.Kind) (string, error) {
	switch kind {
	case modelhost.GitHub:
		return "gh", nil
	case modelhost.GitLab:
		return "glab", nil
	}
	return "", fmt.Errorf("origin からホストの種類を判別できない")
}

// IssueCreateArgs は issue を作る引数の列（CLI の名前から始まる）。
func IssueCreateArgs(kind modelhost.Kind, issue core.IssueSpec) ([]string, error) {
	cli, err := HostCLI(kind)
	if err != nil {
		return nil, err
	}
	if kind == modelhost.GitHub {
		return []string{cli, "issue", "create", "--title", issue.Title, "--body", issue.Body}, nil
	}
	return []string{cli, "issue", "create", "--title", issue.Title, "--description", issue.Body, "--yes"}, nil
}

// IssueCloseArgs は issue を閉じる引数の列（CLI の名前から始まる）。
func IssueCloseArgs(kind modelhost.Kind, number int) ([]string, error) {
	cli, err := HostCLI(kind)
	if err != nil {
		return nil, err
	}
	return []string{cli, "issue", "close", strconv.Itoa(number)}, nil
}

// IssueNumber は issue 作成の出力から、issue の番号を取る。URL の行（GitHub は `.../issues/12`、GitLab は `.../-/issues/12`）の
// 末尾の数字。glab は URL の前後に説明の行を出すので、最終行ではなく URL の行を探す。
func IssueNumber(output string) (int, error) {
	lines := strings.Split(strings.TrimSpace(output), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimRight(strings.TrimSpace(lines[i]), "/")
		if !strings.Contains(line, "://") || !strings.Contains(line, "/issues/") {
			continue
		}
		if n, err := strconv.Atoi(line[strings.LastIndex(line, "/")+1:]); err == nil && n >= 0 {
			return n, nil
		}
	}
	return 0, fmt.Errorf("issue の番号が取れない: %s", strings.TrimSpace(output))
}

// RepoHostKind はリポジトリの origin から、ホストの種類（読めなければ空）を返す。
func RepoHostKind(repoDir string) modelhost.Kind {
	r := Git([]string{"-C", repoDir, "remote", "get-url", "origin"}, hostOpts(""))
	if r.Code != 0 {
		return ""
	}
	kind, _ := modelhost.Origin(strings.TrimSpace(r.Stdout))
	return kind
}

func runCLI(args []string, dir string) (string, string, int, error) {
	ctx, cancel := context.WithTimeout(context.Background(), issueTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, args[0], args[1:]...)
	cmd.Dir = dir
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if ee, ok := err.(*exec.ExitError); ok {
		return stdout.String(), stderr.String(), ee.ExitCode(), nil
	}
	return stdout.String(), stderr.String(), 0, err
}

// MakeIssue は setup の issue の欄どおりに、test-repo へ issue を新しく 1 つ作る。番号を返す。
func MakeIssue(issue core.IssueSpec, root string) (int, error) {
	dir := filepath.Join(root, filepath.FromSlash(issue.Repo))
	args, err := IssueCreateArgs(RepoHostKind(dir), issue)
	if err != nil {
		return 0, err
	}
	stdout, stderr, code, err := runCLI(args, dir)
	if err != nil {
		return 0, err
	}
	if code != 0 {
		return 0, fmt.Errorf("%s が終了コード %d: %s", strings.Join(args[:3], " "), code, strings.TrimSpace(stderr))
	}
	return IssueNumber(stdout)
}

// CloseIssue は回したあとに、作った issue を閉じる（setup の issue の close: true のときだけ）。
// 後片付けの途中なので、閉じられなくても止めず、出力に言うだけ。
func CloseIssue(issue core.IssueSpec, number int, root string, say func(string)) {
	dir := filepath.Join(root, filepath.FromSlash(issue.Repo))
	args, err := IssueCloseArgs(RepoHostKind(dir), number)
	if err != nil {
		say(fmt.Sprintf("issue #%d を閉じられなかった: %v", number, err))
		return
	}
	_, stderr, code, err := runCLI(args, dir)
	switch {
	case err != nil:
		say(fmt.Sprintf("issue #%d を閉じられなかった: %v", number, err))
	case code != 0:
		say(fmt.Sprintf("issue #%d を閉じられなかった: %s", number, strings.TrimSpace(stderr)))
	}
}

// FillIssue は prompt の {issue} を、作った issue の番号に置き換える。番号が 0（無い）ならそのまま。
func FillIssue(text string, number int) string {
	if number == 0 {
		return text
	}
	return strings.ReplaceAll(text, IssuePlaceholder, strconv.Itoa(number))
}
