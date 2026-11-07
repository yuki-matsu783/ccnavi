package tree

import (
	"strings"

	"ccnavi/internal/infra/ospath"
)

// git の状態をファイルだけから読む。読めないときは「分からない」（空か ok が偽）を返し、呼び手が代わりを決める。

// dotGit はツリーのルートの git の印。ふつうはディレクトリ、ワークツリーと submodule ではファイル。
const dotGit = ".git"

// HasGit は dir の直下に `.git`（ディレクトリでもファイルでもよい。リンクは先を見る）があるか。git のツリーの
// ルートの目安で、git は起こさない。
func HasGit(dir string) bool { return ospath.DiskExists(ospath.Join(dir, dotGit)) }

// GitDir はこのツリーの git ディレクトリ。`.git` がディレクトリならそれ、ファイルなら `gitdir:` の先
// （相対ならツリーから）。分からなければ ok が偽。
func GitDir(treeRoot string) (string, bool) {
	dotgit := ospath.Join(treeRoot, dotGit)
	if !ospath.IsFile(dotgit) {
		return dotgit, ospath.IsDir(dotgit)
	}
	gitdir, ok := writtenGitdir(treeRoot)
	if !ok {
		return "", false
	}
	if !ospath.IsAbs(gitdir) {
		gitdir = ospath.Join(treeRoot, gitdir)
	}
	return gitdir, true
}

// commonGitDir は git ディレクトリの共通の側（リンクされたワークツリーなら `commondir` の先）。
// refs/remotes と config はこちらにある。`commondir` が無い・読めない・空なら gitdir のまま。
func commonGitDir(gitdir string) string {
	text, err := ospath.ReadDiskText(ospath.Join(gitdir, "commondir"))
	if err != nil {
		return gitdir
	}
	common := ospath.Strip(text)
	if common == "" {
		return gitdir
	}
	return ospath.Normpath(ospath.Join(gitdir, common))
}

// headFile は git ディレクトリの中の HEAD の名前。
const headFile = "HEAD"

// HeadText はこのツリーの `HEAD` の中身（前後の空白を除く）。読めなければ ok が偽。
// HEAD をファイルから読むのはここだけにする。
func HeadText(treeRoot string) (string, bool) {
	gitdir, ok := GitDir(treeRoot)
	if !ok {
		return "", false
	}
	text, err := ospath.ReadDiskText(ospath.Join(gitdir, headFile))
	if err != nil {
		return "", false
	}
	return ospath.Strip(text), true
}

// OriginRepo は `origin` の URL が指す `owner/repo`（パスの最後の 2 段。GitLab の入れ子のグループも
// 最後の 2 段に縮める）。読めなければ空。
func OriginRepo(repoRoot string) string {
	gitdir, ok := GitDir(repoRoot)
	if !ok {
		return ""
	}
	text, err := ospath.ReadDiskText(ospath.Join(commonGitDir(gitdir), "config"))
	if err != nil {
		return ""
	}
	inOrigin := false
	for _, line := range ospath.Splitlines(text) {
		line = ospath.Strip(line)
		compact := strings.ReplaceAll(line, " ", "")
		switch {
		case strings.HasPrefix(line, "["):
			inOrigin = compact == `[remote"origin"]`
		case inOrigin && strings.HasPrefix(compact, "url="):
			_, url, _ := strings.Cut(line, "=")
			return repoOfURL(ospath.Strip(url))
		}
	}
	return ""
}

func repoOfURL(url string) string {
	scheme := strings.Contains(url, "://")
	rest := url
	if scheme {
		_, rest, _ = strings.Cut(url, "://")
	}
	// `git@host:group/proj`（ssh の短い形）は `:` の後ろ、`host/group/proj` は最初の `/` の後ろがパス。
	var path string
	host, _, _ := strings.Cut(rest, "/")
	if !scheme && strings.Contains(host, ":") {
		_, path, _ = strings.Cut(rest, ":")
	} else {
		_, path, _ = strings.Cut(rest, "/")
	}
	path = strings.TrimSuffix(strings.TrimSuffix(path, "/"), ".git")
	var parts []string
	for _, p := range strings.Split(path, "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	if len(parts) < 2 {
		return ""
	}
	return strings.Join(parts[len(parts)-2:], "/")
}

const originHeadPrefix = "ref: refs/remotes/origin/"

// OriginHead は `origin/HEAD` が指すブランチ名（`ccnavi_default_branch` が symbolic-ref で読むものと同じ）。
// 無ければ空。リンクされたワークツリーでも共通の git ディレクトリを読む。
func OriginHead(repoRoot string) string {
	gitdir, ok := GitDir(repoRoot)
	if !ok {
		return ""
	}
	text, err := ospath.ReadDiskText(ospath.Join(commonGitDir(gitdir), "refs", "remotes", "origin", headFile))
	if err != nil {
		return ""
	}
	text = ospath.Strip(text)
	if !strings.HasPrefix(text, originHeadPrefix) {
		return ""
	}
	return ospath.Strip(text[len(originHeadPrefix):])
}

// BusyMarks は途中の操作（merge・cherry-pick・revert・rebase）の目印（git ディレクトリの中の名前）。
var BusyMarks = []string{"MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"}

// BusyOf はこのツリーで途中の操作があればその目印の名前。無ければ空。
func BusyOf(treeRoot string) string {
	gitdir, ok := GitDir(treeRoot)
	if !ok {
		return ""
	}
	for _, name := range BusyMarks {
		if ospath.DiskLexists(ospath.Join(gitdir, name)) {
			return name
		}
	}
	return ""
}

const headsPrefix = "ref: refs/heads/"

// BranchOf はこのツリーが今いるブランチの名前。切り離した HEAD・空・読めないものは ok が偽
// （呼び手はツリーの名前で代える）。
func BranchOf(treeRoot string) (string, bool) {
	head, ok := HeadText(treeRoot)
	if !ok || !strings.HasPrefix(head, headsPrefix) {
		return "", false
	}
	name := ospath.Strip(head[len(headsPrefix):])
	return name, name != ""
}
