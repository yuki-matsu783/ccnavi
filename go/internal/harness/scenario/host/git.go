package host

import (
	"fmt"
	"strings"
)

// GitOpts は GitFunc の入力。
type GitOpts struct {
	// Dir は作業ディレクトリ。
	Dir string
	// Input は標準入力。
	Input string
	// Env は足す環境変数（本物の git の環境に重ねる）。
	Env map[string]string
}

// GitResult は GitFunc の結果。
type GitResult struct {
	Code           int
	Stdout, Stderr string
}

// GitFunc は本物の git を 1 回実行する関数（呼び手が渡す。スタブを通さない）。
type GitFunc func(args []string, o GitOpts) GitResult

func (r GitResult) out() string { return strings.TrimSpace(r.Stdout) }

// realOrigin は対象の origin の URL（設定の生の値）。`remote get-url` は利用者の url.*.insteadOf で書き換えた後を返し、
// insteadOf は連鎖しないので、bare へ向ける -c には設定の生の値を使う。
func realOrigin(top string, git GitFunc) (string, error) {
	r := git([]string{"config", "--get", "remote.origin.url"}, GitOpts{Dir: top})
	if r.Code != 0 || r.out() == "" {
		return "", fmt.Errorf("対象 %s に origin が無い。bare の相手を作れない", top)
	}
	return r.out(), nil
}

// makeBare は対象の origin（refs/remotes/origin/*）と同じ内容の bare を作る。ネットワークには出ない。
// origin の remote-tracking ref が無ければ、対象のローカルブランチから作る。
func makeBare(top, bare string, git GitFunc) error {
	if r := git([]string{"init", "--bare", "-q", bare}, GitOpts{Dir: top}); r.Code != 0 {
		return fmt.Errorf("bare を作れない: %s", strings.TrimSpace(r.Stderr))
	}
	specs := [][]string{
		{"+" + remoteRefs + "/*:refs/heads/*", "^" + remoteRefs + "/HEAD"},
		{"+refs/heads/*:refs/heads/*"},
	}
	for _, spec := range specs {
		args := append([]string{"fetch", "-q", "--no-tags", top}, spec...)
		args = append(args, "+refs/tags/*:refs/tags/*")
		if r := git(args, GitOpts{Dir: bare}); r.Code != 0 {
			return fmt.Errorf("bare へ取り込めない: %s", strings.TrimSpace(r.Stderr))
		}
		if heads := git([]string{"for-each-ref", "--format=%(refname)", "refs/heads"}, GitOpts{Dir: bare}); len(strings.Fields(heads.Stdout)) > 0 {
			break
		}
	}
	// bare の HEAD は、対象の origin/HEAD が指すブランチ（無ければ main）。
	name := ""
	if r := git([]string{"symbolic-ref", "--short", remoteRefs + "/HEAD"}, GitOpts{Dir: top}); r.Code == 0 {
		name = strings.TrimPrefix(r.out(), "origin/")
	}
	if name == "" || git([]string{"rev-parse", "-q", "--verify", "refs/heads/" + name}, GitOpts{Dir: bare}).Code != 0 {
		name = "main"
	}
	git([]string{"symbolic-ref", "HEAD", "refs/heads/" + name}, GitOpts{Dir: bare})
	return nil
}

// readRemoteRefs は対象の refs/remotes/origin/*（HEAD を除く）。回したあとに戻すために、回す前の値を残す。
func readRemoteRefs(top string, git GitFunc) map[string]string {
	out := map[string]string{}
	r := git([]string{"for-each-ref", "--format=%(refname) %(objectname)", remoteRefs}, GitOpts{Dir: top})
	for _, line := range strings.Split(r.Stdout, "\n") {
		ref, sha, ok := strings.Cut(strings.TrimSpace(line), " ")
		if ok && ref != remoteRefs+"/HEAD" {
			out[ref] = sha
		}
	}
	return out
}

// restoreRemoteRefs は fetch・push で動いた refs/remotes/origin/* を、回す前の値に戻す。
func restoreRemoteRefs(top string, before map[string]string, git GitFunc) error {
	now := readRemoteRefs(top, git)
	var cmds []string
	for ref := range now {
		if _, ok := before[ref]; !ok {
			cmds = append(cmds, "delete "+ref)
		}
	}
	for ref, sha := range before {
		if now[ref] != sha {
			cmds = append(cmds, "update "+ref+" "+sha)
		}
	}
	if len(cmds) == 0 {
		return nil
	}
	if r := git([]string{"update-ref", "--stdin"}, GitOpts{Dir: top, Input: strings.Join(cmds, "\n") + "\n"}); r.Code != 0 {
		return fmt.Errorf("%s", strings.TrimSpace(r.Stderr))
	}
	return nil
}
