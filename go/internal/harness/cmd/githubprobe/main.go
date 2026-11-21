// githubprobe は、実物の GitHub に sh 3 本と exe を適用して 1 周する（tests/manual/github/probe_github.py の写し）。
// ユーザが手で走らせる道具で、自動テストは呼ばない。tests/manual/gitlab の GitLab 版（cmd/gitlabmanual の probe）の GitHub 版。
//
// 使い捨てのブランチと PR と issue を、実験場のリポジトリ（既定 yuki-matsu783/test-repo。PUBLIC）に作る。
// 終わったら PR と issue は閉じ（public なので履歴に残る）、ブランチは消す。ここに秘密や個人情報を書き込まない。
// ほかのリポジトリには書かない。ccnavi 本体のリポジトリ（このチェックアウトの origin）は、指定しても拒む。
//
// 用意するもの
//
//   - 実験場への書き込み権限があるトークン。GITHUB_TOKEN、無ければ `gh auth token` から取る
//
//   - gh（認証済み）。sh は gh があればそれを使う。curl の経路は、gh を PATH から外して同じ読みをやり直して確かめる
//
//   - 組み立て済みの exe。CCNAVI_ENTRY_PATH で絶対パスを渡す（無ければ dist/ccnavi/ccnavi）。このチェックアウトより古い exe だと食い違うので、起動時に版を出す
//
//   - jq と curl（sh が使う）
//
//     cd go && CCNAVI_ENTRY_PATH=/path/to/ccnavi go run ./internal/harness/cmd/githubprobe
//
// 一人では確かめられないもの: 人間役もトークンの持ち主と同じアカウント。GitHub は自分の PR に approve も変更要求も出させないので、
// CHANGES_REQUESTED を実物で読む段階は無い（読み方は fixture の試験が見る）。別のアカウントのトークンを CCNAVI_PROBE_REVIEWER_TOKEN に
// 置けば、その段階も走る。
//
// 分かること: sh の HTTP 経路（gh と curl・jq・ページング・投稿・認証）と、GitHub が返す JSON（特に GraphQL の reviewThreads）の形が sh の読み方と合っているか。
// 結果と生の JSON は CCNAVI_PROBE_OUT（既定は一時ディレクトリ）に残る。
package main

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"ccnavi/internal/infra/env"
)

func main() { os.Exit(run(os.Stdout, os.Stderr)) }

// run は起動の確かめ（実験場・トークン・exe・版）をしてから、実測を回す。止める条件は false の代わりに 1 を返す。
func run(stdout, stderr io.Writer) int {
	root, err := findRoot()
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	environ := envFromOS()
	repo, ok := environ[env.ProbeRepo]
	if !ok {
		repo = defaultRepo
	}
	own := ownRepo(gitOut(root, "remote", "get-url", "origin"))
	if err := checkTarget(repo, own, environ[env.ProbeRepoOK] != ""); err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	token := pickToken(environ[env.GitHubToken], ghAuthToken)
	if token == "" {
		fmt.Fprintln(stderr, "GITHUB_TOKEN が要る（gh auth login 済みなら gh auth token から取れる）")
		return 1
	}
	exe, err := exePath(environ[env.EntryPath], root, isFile)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	notes, err := staleCheck(exeVersion(exe), gitOut(root, "rev-parse", "--short", "HEAD"), environ[env.ProbeStrictExe] != "")
	for _, n := range notes {
		fmt.Fprintln(stdout, n)
	}
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}

	out := environ[env.ProbeOut]
	if out == "" {
		dir, err := os.MkdirTemp("", "ccnavi-probe-")
		if err != nil {
			fmt.Fprintln(stderr, err)
			return 1
		}
		out = dir
	}
	stamp := time.Now().Format("0102-150405")
	out = filepath.Join(out, "ghrun-"+stamp)
	if err := os.MkdirAll(out, 0o755); err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	logFile, err := os.Create(filepath.Join(out, "steps.log"))
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	defer logFile.Close()

	p := &probe{
		stdout:        stdout,
		stderr:        stderr,
		log:           logFile,
		out:           out,
		root:          filepath.Join(out, "repo"),
		exe:           exe,
		repo:          repo,
		owner:         strings.SplitN(repo, "/", 2)[0],
		token:         token,
		reviewerToken: environ[env.ProbeReviewerToken],
		env:           probeEnv(environ, token, exe),
		stamp:         stamp,
		srcRoot:       root,
	}
	return p.guarded()
}

// guarded は実測を回す。途中で落ちても、作った PR・ブランチ・課題は片付ける。
func (p *probe) guarded() (code int) {
	defer func() {
		if r := recover(); r != nil {
			p.cleanupSafe()
			fmt.Fprintln(p.stderr, r)
			code = 1
		}
	}()
	p.flow()
	return p.finish()
}

// finish は片付けをして、結果の一覧を出す。失敗が 1 つでもあれば 1。
func (p *probe) finish() int {
	p.cleanupSafe()
	p.say("\n==== まとめ ====")
	failed := 0
	for _, c := range p.checks {
		if c.ok {
			p.say("PASS  " + c.name)
		} else {
			failed++
			p.say("FAIL  " + c.name)
		}
	}
	p.say(fmt.Sprintf("\n%d / %d PASS。置き場: %s", len(p.checks)-failed, len(p.checks), p.out))
	if failed > 0 || len(p.cleanupNG) > 0 {
		return 1
	}
	return 0
}

// cleanupSafe は片付けをする。片付けが途中で落ちても、手で片付ける物を出して続ける。
func (p *probe) cleanupSafe() {
	defer func() {
		if r := recover(); r != nil {
			p.say(fmt.Sprintf("片付けに失敗: %v。%v %v %v を手で片付ける", r, p.madePRs, p.madeBrs, p.madeIssue))
		}
	}()
	p.cleanup()
}

// cleanup は作った PR と issue を閉じ（public なので履歴には残る）、ブランチを消す。実験場の中だけ。
// 各呼び出しの状態を見て、失敗は cleanupNG に挙げる。
func (p *probe) cleanup() {
	for _, n := range p.madePRs {
		st, _ := p.api("PATCH", fmt.Sprintf("/repos/%s/pulls/%d", p.repo, n), "", map[string]any{"state": "closed"}, "cleanup-pr")
		p.say(fmt.Sprintf("片付け: PR #%d を閉じる -> %d", n, st))
		if st != 200 {
			p.cleanupNG = append(p.cleanupNG, fmt.Sprintf("PR #%d (%d)", n, st))
		}
	}
	for _, b := range p.madeBrs {
		st, _ := p.api("DELETE", fmt.Sprintf("/repos/%s/git/refs/heads/%s", p.repo, b), "", nil, "cleanup-branch")
		p.say(fmt.Sprintf("片付け: ブランチ %s を消す -> %d", b, st))
		if st != 204 && st != 422 { // 422 は既に無い
			p.cleanupNG = append(p.cleanupNG, fmt.Sprintf("ブランチ %s (%d)", b, st))
		}
	}
	for _, n := range p.madeIssue {
		st, _ := p.api("PATCH", fmt.Sprintf("/repos/%s/issues/%d", p.repo, n), "", map[string]any{"state": "closed", "state_reason": "not_planned"}, "cleanup-issue")
		p.say(fmt.Sprintf("片付け: issue #%d を閉じる -> %d", n, st))
		if st != 200 {
			p.cleanupNG = append(p.cleanupNG, fmt.Sprintf("issue #%d (%d)", n, st))
		}
	}
	if len(p.cleanupNG) > 0 {
		p.say("片付けに失敗したもの（手で片付ける）: " + strings.Join(p.cleanupNG, ", "))
		return
	}
	p.say(fmt.Sprintf("片付け: PR %v と issue %v を閉じた", p.madePRs, p.madeIssue))
	p.say(fmt.Sprintf("片付け: ブランチ %v を削除した", p.madeBrs))
}

// findRoot は作業ディレクトリから上って go/go.mod を持つ所（リポジトリの根）を探す。
func findRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go", "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("リポジトリの根（go/go.mod の親）が見つからない")
		}
		dir = parent
	}
}

// gitOut は dir で git を起こした標準出力（前後の空白を落とす）。失敗は空。
func gitOut(dir string, args ...string) string {
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// ghAuthToken は gh auth token の値。gh が無い・失敗したら空。
func ghAuthToken() string {
	out, err := exec.Command("gh", "auth", "token").Output()
	if err != nil {
		return ""
	}
	return string(out)
}

// exeVersion は exe の --version の標準出力（一時の置き場で起こす。読めなければ空）。
func exeVersion(exe string) string {
	cmd := exec.Command(exe, "--version")
	cmd.Dir = os.TempDir()
	var out bytes.Buffer
	cmd.Stdout = &out
	if err := cmd.Run(); err != nil {
		return ""
	}
	return strings.TrimSpace(out.String())
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}
