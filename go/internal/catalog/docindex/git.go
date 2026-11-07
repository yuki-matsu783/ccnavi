package docindex

import (
	"errors"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
)

// MdExt は索引に載せる文書の拡張子（大文字小文字を区別する）。
const MdExt = ".md"

// ErrNotRepository は基準のディレクトリが git のルートではない（`.git` が無い）。
var ErrNotRepository = errors.New("git のルートではない")

// gitError は git への問い合わせに失敗したことを表す（git が無い・期限切れ・リポジトリが壊れている）。
// git の外とも、無視されていないとも別に扱う。Error() は理由の文を返す。
type gitError struct{ reason string }

func (e *gitError) Error() string { return e.reason }

func gitFailure(done git.Done) error {
	switch {
	case done.Missing():
		return &gitError{"git が見つからない"}
	case done.TimedOut():
		return &gitError{"git が期限までに終わらなかった"}
	case done.Failure != nil:
		return &gitError{done.Failure.Error()}
	}
	lines := ospath.Splitlines(ospath.Strip(done.Stderr))
	if len(lines) == 0 {
		return &gitError{"git が " + strconv.Itoa(done.Code) + " で終わった"}
	}
	return &gitError{lines[len(lines)-1]}
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
		if !strings.HasSuffix(p, MdExt) || seen[p] {
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
func Excluded(rel string, excluded, kept []string) bool {
	return within(rel, excluded) && !within(rel, kept)
}

func within(rel string, dirs []string) bool {
	for _, d := range dirs {
		if rel == d || strings.HasPrefix(rel, d+"/") {
			return true
		}
	}
	return false
}
