package git

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/ospath"
)

// 読めなかった理由（ChildBase）。
var (
	ErrNoTrees     = errors.New("子か親のワークツリーが決まらない")
	ErrNoMergeBase = errors.New("子のブランチと親のブランチの共通の祖先を読めない")
)

// ref の頭。
const (
	refsHeads         = "refs/heads/"
	refsRemotes       = "refs/remotes/"
	refsOriginRemotes = "refs/remotes/origin/"
	originHEAD        = "HEAD"
)

// diffPathsArgs は 2 つの版の間で変わったパスを NUL 区切りで返す（改名と submodule は Committed と同じ理由で開く）。
var diffPathsArgs = []string{"diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z"}

func stateOptions(timeout time.Duration) Options {
	if timeout <= 0 {
		timeout = StateTimeout
	}
	return Options{Timeout: timeout}
}

// Head はいまの HEAD の sha。読めなければ空文字。ターンの始まりに記録し、終わりに「このターンで何が
// コミットに入ったか」を数える基準にする。期限は o で渡す（Timeout が 0 なら DefaultTimeout、Deadline も効く）。
func Head(top string, o Options) string {
	if top == "" {
		return ""
	}
	done := runGit(top, []string{"rev-parse", "HEAD"}, o)
	if !done.OK() {
		return ""
	}
	return ospath.Strip(done.Out)
}

// NotAncestorCode は IsAncestor の git が「祖先ではない」と答える終了コード（ほかの非 0 は読めない）。
const NotAncestorCode = 1

// IsAncestor は ancestor が descendant の祖先か（merge-base --is-ancestor）を git に聞く。
// OK() なら祖先、Code が NotAncestorCode なら祖先ではない。
func IsAncestor(cwd, ancestor, descendant string, o Options) Done {
	return runGit(cwd, []string{"merge-base", "--is-ancestor", ancestor, descendant}, o)
}

// Count は数を答える git（rev-list --count）の出力を読む。前後の空白を除いて数字だけでなければ偽。
func Count(out string) (int, bool) {
	s := ospath.Strip(out)
	if s == "" {
		return 0, false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return 0, false
		}
	}
	n, err := strconv.Atoi(s)
	return n, err == nil
}

// firstOut は merge-base などの答えの 1 行目。答えが無ければ空。
func firstOut(done Done) string {
	out := ospath.Strip(done.Out)
	if !done.OK() || out == "" {
		return ""
	}
	return ospath.Splitlines(out)[0]
}

// ForkBase は others（ターンの始まりに記録した別のツリーの HEAD）から、このツリーが分かれた点。
// ターンの途中で切ったワークツリーはターンの始まりの HEAD を持たないので、merge-base で代える。
// どれとも共通の祖先が無い・読めないときは空文字（数えられない側にする）。
func ForkBase(top string, others []string, timeout time.Duration) string {
	if top == "" {
		return ""
	}
	for _, other := range others {
		if found := firstOut(runGit(top, []string{"merge-base", "HEAD", other}, stateOptions(timeout))); found != "" {
			return found
		}
	}
	return ""
}

// ChildBase は子のワークツリーでの子の仕事の始まり（親のワークツリーの HEAD と子の HEAD の共通の祖先）。
//
// 着手のときの基準点から数えないのは、衝突を解くために子へ親を取り込んだとき、親に入っているほかの子の
// 変更が混ざるから。親へ合流した子が続きを積んだときは、合流した先頭が始まりになる。
// childHead が空でなければ、子の HEAD の代わりにその sha から求める。読めなければ理由のエラー。
func ChildBase(childTree, parentTree string, timeout time.Duration, childHead string) (string, error) {
	if childTree == "" || parentTree == "" {
		return "", ErrNoTrees
	}
	parent := Head(parentTree, stateOptions(timeout))
	if parent == "" {
		return "", fmt.Errorf("親のワークツリー（%s）の HEAD を読めない", parentTree)
	}
	if childHead == "" {
		childHead = "HEAD"
	}
	found := firstOut(runGit(childTree, []string{"merge-base", childHead, parent}, stateOptions(timeout)))
	if found == "" {
		return "", ErrNoMergeBase
	}
	return found, nil
}

func zPaths(top string, args []string, timeout time.Duration) (map[string]bool, bool) {
	o := stateOptions(timeout)
	o.RawPaths = true
	done := runGit(top, args, o)
	if !done.OK() {
		return nil, false
	}
	paths := map[string]bool{}
	for _, p := range strings.Split(done.Out, "\x00") {
		if p != "" {
			paths[p] = true
		}
	}
	return paths, true
}

// TakenFromRemote は base..head のうち、統合先（リモート追跡ブランチ）から取り込んだだけのパス。
//
// 子が統合先を merge すると、他人の変更のパスが差分に入る。範囲の検査とリスクの計測から外すのは、
// first-parent の merge のうち 2 つめ以降の親がリモート追跡ブランチに入っているものについて、
// merge が 1 つめの親から変えたパスで、head の中身が取り込んだ側と同じもの。衝突を解いて書き換えたパスと
// merge のあとに子が直したパスは残る。読めなかったときは何も外さない（数える側にする）。
func TakenFromRemote(top, base, head string, timeout time.Duration) map[string]bool {
	taken := map[string]bool{}
	if top == "" || base == "" || head == "" {
		return taken
	}
	merges := runGit(top, []string{"rev-list", "--merges", "--first-parent", "--parents", base + ".." + head}, stateOptions(timeout))
	if !merges.OK() {
		return taken
	}
	for _, line := range ospath.Splitlines(merges.Out) {
		shas := strings.Fields(line)
		if len(shas) < 3 {
			continue
		}
		merge, first := shas[0], shas[1]
		var brought map[string]bool // 読めなければ nil のまま、次の親でもう一度読む
		for _, other := range shas[2:] {
			onRemote := runGit(top, []string{"for-each-ref", "--contains", other, "--count=1", "--format=%(refname)", refsRemotes}, stateOptions(timeout))
			if !onRemote.OK() || ospath.Strip(onRemote.Out) == "" {
				continue
			}
			if brought == nil {
				brought, _ = zPaths(top, append(append([]string(nil), diffPathsArgs...), first, merge), timeout)
			}
			differ, ok := zPaths(top, append(append([]string(nil), diffPathsArgs...), other, head), timeout)
			if brought == nil || !ok {
				continue
			}
			for p := range brought {
				if !differ[p] {
					taken[p] = true
				}
			}
		}
	}
	return taken
}

// BranchNames は手元のブランチと origin のブランチの名前（refs/heads/ と refs/remotes/origin/ を外した表記）。
// origin/HEAD（既定のブランチを指す別名）は数えない。refname は quotePath の引用を受けないので、
// 日本語の名前もそのまま返る。読めなければ空。
func BranchNames(top string, timeout time.Duration) map[string]bool {
	names := map[string]bool{}
	if top == "" || !ospath.IsDir(top) {
		return names
	}
	done := runGit(top, []string{"for-each-ref", "--format=%(refname)", refsHeads, refsOriginRemotes}, stateOptions(timeout))
	if !done.OK() {
		return names
	}
	for _, line := range ospath.Splitlines(done.Out) {
		for _, prefix := range []string{refsHeads, refsOriginRemotes} {
			if name, ok := strings.CutPrefix(line, prefix); ok {
				if name != "" && !(prefix == refsOriginRemotes && name == originHEAD) {
					names[name] = true
				}
				break
			}
		}
	}
	return names
}

// CommittedText は HEAD に入っているその 1 本の中身と、読めたか。HEAD にそのパスが無ければ ("", false, true)。
//
// 呼び手は「読めなかった」を「変わっていない」として扱ってはいけない（git を止めるだけで、突き合わせを
// 前提にした除外が全部通る）。無いことの確かめに ls-tree をもう 1 回起こすのは、show の失敗が
// 「HEAD に無い」か「HEAD が無い」かを、言語設定で変わる git の文面を読まずに分けるため。
func CommittedText(top, path string, timeout time.Duration) (text string, present, readable bool) {
	done := runGit(top, []string{"show", "HEAD:" + path}, stateOptions(timeout))
	if done.OK() {
		return done.Out, true, true
	}
	if done.Failure != nil {
		return "", false, false
	}
	listed := runGit(top, []string{"ls-tree", "-z", "HEAD", "--", path}, stateOptions(timeout))
	if !listed.OK() {
		return "", false, false
	}
	return "", false, ospath.Strip(strings.Trim(listed.Out, "\x00")) == ""
}
