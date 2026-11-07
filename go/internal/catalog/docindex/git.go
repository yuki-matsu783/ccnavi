package docindex

import (
	"errors"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
)

// gitTimeout は git に与える時間（ls-files と check-ignore の 1 回ずつ）。期限があれば残りで縮める。
const gitTimeout = 10 * time.Second

// minGitTimeout は期限の残りが無くても git に与える時間（0 にはしない）。
const minGitTimeout = 100 * time.Millisecond

// MdExt は索引に載せる文書の拡張子（大文字小文字を区別する）。
const MdExt = ".md"

// ErrNotRepository は基準のディレクトリが git のルートではない（`.git` が無い）。
var ErrNotRepository = errors.New("git のルートではない")

// GitError は git への問い合わせに失敗したことを表す（git が無い・期限切れ・リポジトリが壊れている）。
// git の外とも、無視されていないとも別に扱う。Error() は理由の文を返す。
type GitError struct{ Reason string }

func (e *GitError) Error() string { return e.Reason }

func gitFailure(done git.Done) error {
	switch {
	case done.Missing():
		return &GitError{"git が見つからない"}
	case done.TimedOut():
		return &GitError{"git が期限までに終わらなかった"}
	case done.Failure != nil:
		return &GitError{done.Failure.Error()}
	}
	lines := ospath.Splitlines(ospath.Strip(done.Stderr))
	if len(lines) == 0 {
		return &GitError{"git が " + strconv.Itoa(done.Code) + " で終わった"}
	}
	return &GitError{lines[len(lines)-1]}
}

// listed は、base の下で git が挙げる md（base からの相対、"/" 区切り）を返す。追跡しているものと、
// 無視されていない未追跡のものを含む。
func listed(base string, timeout time.Duration) ([]string, error) {
	if !ospath.DiskExists(ospath.Join(base, ".git")) {
		return nil, ErrNotRepository
	}
	// pathspec（`*.md`）は渡さず、ここで選ぶ。利用者の環境の GIT_LITERAL_PATHSPECS などで読み方が変わる。
	done := git.Run(base, []string{"ls-files", "-z", "--cached", "--others", "--exclude-standard"},
		git.Options{Timeout: timeout, PlainPathspecs: true})
	if !done.OK() {
		return nil, gitFailure(done)
	}
	seen := map[string]bool{}
	var found []string
	for _, p := range strings.Split(done.Out, "\x00") {
		if p == "" || !strings.HasSuffix(p, MdExt) || seen[p] {
			continue
		}
		seen[p] = true
		found = append(found, p)
	}
	return found, nil
}

// Ignored は paths（base からの相対）のうち git が無視するもの。問い合わせに失敗したら *GitError。
func Ignored(base string, paths []string, timeout time.Duration) (map[string]bool, error) {
	out := map[string]bool{}
	if len(paths) == 0 {
		return out, nil
	}
	// check-ignore は渡したパスを pathspec として読み、`:` で始まる名前を magic として 128 で止まる。
	// --literal-pathspecs も受けないので、頭に `./` を付けて magic と読ませない（返るパスも `./` 付き）。
	var input strings.Builder
	for _, p := range paths {
		input.WriteString("./" + p + "\x00")
	}
	stdin := input.String()
	done := git.Run(base, []string{"check-ignore", "-z", "--stdin"},
		git.Options{Timeout: timeout, Input: &stdin, PlainPathspecs: true})
	// 0 は 1 つ以上が無視、1 はどれも無視されない。ほかは問い合わせの失敗。
	if done.Failure != nil || (done.Code != 0 && done.Code != 1) {
		return nil, gitFailure(done)
	}
	for _, p := range strings.Split(done.Out, "\x00") {
		if p != "" {
			out[strings.TrimPrefix(p, "./")] = true
		}
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
