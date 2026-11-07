package tree

import (
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
)

// WorktreesDir はワークツリーの置き場（ワークスペースルートからの相対）。セッションの道具と権限が
// 届くよう、ワークスペースルートの中に置く。
var WorktreesDir = ospath.Join(".claude", "worktrees")

// WorktreesPath はワークスペースルート root のワークツリーの置き場の絶対パス。
func WorktreesPath(root string) string { return ospath.Join(root, WorktreesDir) }

// Main はワークスペースルートの名前（空）。
const Main = ""

// Self はワークスペース自身の手元の状態（退避・退役の記録・ロック）を分ける名前。
const Self = "self"

// Kind はツリーの種類。
type Kind string

const (
	KindMain     Kind = "main"
	KindProject  Kind = "project" // projects/ の直下にある、自分の git を持つリポジトリ
	KindWorktree Kind = "worktree"
)

// Tree はツリー 1 つ。Project は、ワークスペースなら空、プロジェクトならその名前、ワークツリーなら
// 元リポジトリのプロジェクトの名前（ワークスペースから切ったものなら空）。
type Tree struct {
	Name    string
	Root    string
	Project string
	Kind    Kind
}

// IsMain はチケットを持たないツリー（ワークスペースルートとプロジェクト）か。
func (t Tree) IsMain() bool { return t.Kind != KindWorktree }

// RepoKey は手元の状態を分ける名前。ワークスペース自身は Self。
func RepoKey(project string) string {
	if project == "" {
		return Self
	}
	return project
}

// ProjectOfKey は RepoKey の逆。
func ProjectOfKey(repo string) string {
	if repo == Self {
		return ""
	}
	return repo
}

// MainTree はワークスペースルートのツリー。
func MainTree(root string) Tree {
	return Tree{Name: Main, Root: resolved(root), Kind: KindMain}
}

// Projects は置き場の直下で `.git`（ディレクトリでもファイルでもよい）を持つディレクトリ。深さ 1。
func Projects(projectsDir string) []Tree {
	if projectsDir == "" {
		return nil
	}
	names, _ := ospath.DiskListdir(projectsDir)
	var found []Tree
	for _, name := range names {
		candidate := ospath.Join(projectsDir, name)
		if ospath.IsDir(candidate) && HasGit(candidate) {
			found = append(found, Tree{Name: name, Root: resolved(candidate), Project: name, Kind: KindProject})
		}
	}
	return found
}

// worktreeCandidates はワークツリーの置き場の下のディレクトリと、元リポジトリの候補。
func worktreeCandidates(root, projectsDir string) (names []string, base string, owners []Tree) {
	base = ospath.Join(root, WorktreesDir)
	names, err := ospath.DiskListdir(base)
	if err != nil {
		return nil, base, nil
	}
	return names, base, append([]Tree{MainTree(root)}, Projects(projectsDir)...)
}

// Worktrees は相互参照が成り立つワークツリーの一覧。元リポジトリはワークスペースでもプロジェクトでもよい。
func Worktrees(root, projectsDir string) []Tree {
	names, base, owners := worktreeCandidates(root, projectsDir)
	var found []Tree
	for _, name := range names {
		candidate := ospath.Join(base, name)
		if !ospath.IsDir(candidate) {
			continue
		}
		if owner, ok := OwnerOf(candidate, owners); ok {
			found = append(found, Tree{Name: pyfmt.NFC(name), Root: resolved(candidate), Project: owner.Project, Kind: KindWorktree})
		}
	}
	return found
}

// ProjectlessWorktree はワークツリーの形なのに元のプロジェクトを見つけられないもの。中のパスは
// ワークスペースルートとして判定されるので、その穴を診断で名指しするために使う（判定には使わない）。
type ProjectlessWorktree struct {
	Name string
	Path string
	// Gitdir は `.git` ファイルに書かれたとおりの先、GitdirExists はその先が在るか。
	Gitdir       string
	GitdirExists bool
}

// Said は `--lint` と `--explain` で共通の文面。
func (w ProjectlessWorktree) Said() string {
	why := w.Gitdir + " の先が見つからない"
	if w.GitdirExists {
		why = w.Gitdir + " は在るが、そこからこのワークツリーへ戻る参照が成り立たない"
	}
	return "ワークツリー " + w.Name + " は元のプロジェクトがありません（" + why + "）。" +
		"このパスはワークスペースルートとして判定され、そのプロジェクトの deny が効いていない。" +
		"ワークツリーを削除するか、プロジェクトを戻してください"
}

// WorktreesWithoutProject は `.git` が `gitdir:` で始まるファイルなのに元を見つけられないワークツリー。
// 実行前チェックの経路からは呼ばない（走査が期限を圧迫する）。
func WorktreesWithoutProject(root, projectsDir string) []ProjectlessWorktree {
	names, base, owners := worktreeCandidates(root, projectsDir)
	var found []ProjectlessWorktree
	for _, name := range names {
		candidate := ospath.Join(base, name)
		if !ospath.IsDir(candidate) {
			continue
		}
		if _, ok := OwnerOf(candidate, owners); ok {
			continue
		}
		written, ok := writtenGitdir(candidate)
		if !ok {
			continue
		}
		target := written
		if !ospath.IsAbs(written) {
			target = ospath.Join(candidate, written)
		}
		found = append(found, ProjectlessWorktree{pyfmt.NFC(name), candidate, written, ospath.DiskExists(target)})
	}
	return found
}

const gitdirPrefix = "gitdir:"

// writtenGitdir は `.git` ファイルの `gitdir:` に書かれた先（書かれたとおり）。ファイルでない・読めない・
// 形が違えば ok が偽。
func writtenGitdir(treeRoot string) (string, bool) {
	gitfile := ospath.Join(treeRoot, DotGit)
	if !ospath.IsFile(gitfile) {
		return "", false
	}
	text, err := ospath.ReadDiskText(gitfile)
	if err != nil {
		return "", false
	}
	line := ospath.Strip(text)
	if !strings.HasPrefix(line, gitdirPrefix) {
		return "", false
	}
	return ospath.Strip(line[len(gitdirPrefix):]), true
}

// OwnerOf はこのワークツリーの元リポジトリ（相互参照が成り立つもの）。
func OwnerOf(candidate string, owners []Tree) (Tree, bool) {
	for _, owner := range owners {
		if IsWorktreeOf(owner.Root, candidate) {
			return owner, true
		}
	}
	return Tree{}, false
}

// AllTrees はワークスペースルート、プロジェクト、ワークツリーの順。
func AllTrees(root, projectsDir string) []Tree {
	trees := append([]Tree{MainTree(root)}, Projects(projectsDir)...)
	return append(trees, Worktrees(root, projectsDir)...)
}

// ProjectRoot はこの名前の git プロジェクトルート（無くても返す）。空の名前はワークスペース（空）。
func ProjectRoot(projectsDir, project string) string {
	if project == "" || projectsDir == "" {
		return ""
	}
	return ospath.Join(projectsDir, project)
}

// IsWorktreeOf は candidate が root のワークツリーであることを相互参照で確かめる。
func IsWorktreeOf(root, candidate string) bool {
	gitdir, ok := writtenGitdir(candidate)
	if !ok {
		return false
	}
	if !ospath.IsAbs(gitdir) {
		gitdir = ospath.Join(candidate, gitdir)
	}
	registry := key(ospath.Join(root, GitWorktrees))
	if !strings.HasPrefix(key(gitdir), registry+ospath.Sep) {
		return false
	}
	back, err := ospath.ReadDiskText(ospath.Join(gitdir, "gitdir"))
	if err != nil {
		return false
	}
	return key(ospath.Strip(back)) == key(ospath.Join(candidate, DotGit))
}

// TreeOf はこのパスが属するツリー。ワークスペースルートの外なら ok が偽（判定を持たない）。
func TreeOf(root, full, projectsDir string) (Tree, bool) {
	if full == "" {
		return Tree{}, false
	}
	target := key(full)
	var best Tree
	found := false
	for _, t := range AllTrees(root, projectsDir) {
		base := pathcase.Fold(t.Root)
		inside := target == base || strings.HasPrefix(target, base+ospath.Sep)
		if inside && (!found || utf8.RuneCountInString(t.Root) > utf8.RuneCountInString(best.Root)) {
			best, found = t, true
		}
	}
	return best, found
}

// PartsUnder は full が base の下（か同じ）なら、base からの残りの名前を返す。外なら ok が偽。
// 突き合わせは大文字小文字を区別しないが、返す表記は full のまま（小文字にすると、チケットが
// `README.md` と書いた範囲に `readme.md` を当てて永久に当たらない）。
func PartsUnder(base, full string) ([]string, bool) {
	baseParts := strings.Split(strings.TrimRight(resolved(base), ospath.Sep), ospath.Sep)
	parts := strings.Split(strings.TrimRight(resolved(full), ospath.Sep), ospath.Sep)
	if len(parts) < len(baseParts) {
		return nil, false
	}
	for i, want := range baseParts {
		if pathcase.Fold(want) != pathcase.Fold(parts[i]) {
			return nil, false
		}
	}
	return parts[len(baseParts):], true
}

// Relative はツリーのルートからの相対（区切りは "/"、ルートそのものなら空）。外なら `..` で辿る形。
func Relative(t Tree, full string) string {
	if rest, ok := PartsUnder(t.Root, full); ok {
		return strings.Join(rest, "/")
	}
	rel, ok := ospath.Relpath(resolved(full), t.Root)
	if !ok {
		rel = resolved(full)
	}
	return filepath.ToSlash(rel)
}

// WorktreePath はこの名前のワークツリーの場所。大文字小文字だけが違う名前は同じ場所なので、在れば
// ディレクトリの実際の表記で返す。
func WorktreePath(root, name string) string {
	base := ospath.Join(root, WorktreesDir)
	existing, _ := ospath.DiskListdir(base)
	folded := pathcase.Fold(pyfmt.NFC(name))
	for _, entry := range existing {
		if pathcase.Fold(pyfmt.NFC(entry)) == folded {
			return ospath.Join(base, entry)
		}
	}
	return ospath.Join(base, name)
}

// Lookup はワークツリーの名前で引く。大文字小文字はどの機械でも区別しない。綴りまで同じキーを先に返し、
// 無ければ畳んで同じキーのうち名前順で最初のもの（Go の map は順序を持たないので順を決めておく）。
func Lookup[V any](index map[string]V, name string) (V, bool) {
	if found, ok := index[name]; ok {
		return found, true
	}
	keys := make([]string, 0, len(index))
	for k := range index {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	folded := pathcase.Fold(name)
	for _, k := range keys {
		if pathcase.Fold(k) == folded {
			return index[k], true
		}
	}
	var zero V
	return zero, false
}

// resolved は行き着く先。表記の大文字小文字は元のまま。
func resolved(path string) string { return ospath.Normpath(ospath.Realpath(path)) }

// key は同じ場所かを比べるためのキー。
func key(path string) string { return pathcase.Fold(resolved(path)) }
