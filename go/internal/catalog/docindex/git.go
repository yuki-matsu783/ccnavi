package docindex

import (
	"errors"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fault"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
)

// ErrNotRepository は基準のディレクトリが git のルートではない（`.git` が無い）。
var ErrNotRepository = errors.New("git のルートではない")

// gitError は git への問い合わせに失敗したことを表す（git が無い・期限切れ・リポジトリが壊れている）。
// git の外とも、無視されていないとも別に扱う。Error() は理由の文を返す。
type gitError struct{ fault.Detail }

func gitFailure(done git.Done) error {
	switch {
	case done.Missing():
		return &gitError{fault.Plain("git が見つからない")}
	case done.TimedOut():
		return &gitError{fault.Plain("git が期限までに終わらなかった")}
	case done.Failure != nil:
		return &gitError{fault.Plain(done.Failure.Error())}
	}
	lines := ospath.Splitlines(ospath.Strip(done.Stderr))
	if len(lines) == 0 {
		return &gitError{fault.Plain("git が " + strconv.Itoa(done.Code) + " で終わった")}
	}
	return &gitError{fault.Plain(lines[len(lines)-1])}
}

// listed は、base の下で git が挙げる md（base からの相対、"/" 区切り）を返す。追跡しているものと、
// 無視されていない未追跡のものを含む。
func listed(base string, timeout time.Duration) ([]string, error) {
	if !tree.HasGit(base) {
		return nil, ErrNotRepository
	}
	// pathspec（`*.md`）は渡さず、ここで選ぶ。
	all, done := git.Listed(base, git.ListAll, git.Options{Timeout: timeout})
	if !done.OK() {
		return nil, gitFailure(done)
	}
	seen := map[string]bool{}
	var found []string
	for _, p := range all {
		if !pathcase.HasExt(p, config.MdExt) || seen[p] {
			continue
		}
		seen[p] = true
		found = append(found, p)
	}
	return found, nil
}

// Ignored は paths（base からの相対）のうち git が無視するもの。問い合わせに失敗したら理由のエラー。
func Ignored(base string, paths []string, timeout time.Duration) (map[string]bool, error) {
	found, done, ok := git.Ignored(base, paths, git.DotSlashed, git.Options{Timeout: timeout})
	if !ok {
		return nil, gitFailure(done)
	}
	out := map[string]bool{}
	for _, p := range found {
		out[p] = true
	}
	return out, nil
}

// Excluded は rel（"/" 区切り）が excluded のどれかの下にあり、kept（除外の中の例外）のどれの下にもないか。
// 大文字小文字は区別しない（`.CCNAVI/x.md` も `.ccnavi` の下）。
func Excluded(rel string, excluded, kept []string) bool {
	return within(rel, excluded) && !within(rel, kept)
}

func within(rel string, dirs []string) bool {
	folded := pathcase.Fold(rel)
	for _, d := range dirs {
		if dir := pathcase.Fold(d); folded == dir || strings.HasPrefix(folded, pathcase.Fold(d+"/")) {
			return true
		}
	}
	return false
}
