package tree

import (
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
)

// 直接作業の記録（`<git の共通ディレクトリ>/ccnavi-direct/<名前>`。書くのは ccnavi-worktree.sh）を読む。
// 答えるのは「横で作業する指定がいま有効か」だけ。作業ワークツリーを消せば、記録が残っていても無効になる。

// DirectDir は記録の置き場（git の共通ディレクトリからの相対）。
const DirectDir = "ccnavi-direct"

// DirectVersion は記録の頭（`version 4`）の版。
const DirectVersion = "4"

// DirectRecord は有効と確かめた記録 1 つ。
type DirectRecord struct {
	Name       string
	MainRoot   string
	BaseBranch string
}

// ParseDirect は `<キー> <値>` の行をマップにする。空行と形の違う行は飛ばし、同じキーは後が勝つ。
func ParseDirect(text string) map[string]string {
	out := map[string]string{}
	for _, line := range ospath.Splitlines(text) {
		k, v, _ := strings.Cut(ospath.Strip(line), " ")
		if k != "" {
			out[k] = ospath.Strip(v)
		}
	}
	return out
}

// CommonDir はこのツリーの git の共通ディレクトリの実パス。分からなければ ok が偽。
func CommonDir(treeRoot string) (string, bool) {
	gitdir, ok := GitDir(treeRoot)
	if !ok || !ospath.IsDir(gitdir) {
		return "", false
	}
	return ospath.Normpath(ospath.Realpath(commonGitDir(gitdir))), true
}

func samePath(a, b string) bool {
	return pathcase.Fold(ospath.Normpath(a)) == pathcase.Fold(ospath.Normpath(b))
}

// validDirect は記録が有効か。すべてを満たすときだけ真。
//   - version が合い、side_by_side が yes
//   - main_tree が記録を置いたリポジトリのユーザのチェックアウトと同じ場所
//   - common_dir がそのチェックアウトの共通ディレクトリと同じ（大文字小文字を区別しない）
//   - 作業ワークツリーが在り、リンクでなく、そのリポジトリのワークツリーとして登録され、同じ名前のブランチを開いている
func validDirect(root string, main Tree, common, name string, fields map[string]string) bool {
	if fields["version"] != DirectVersion || fields["side_by_side"] != "yes" {
		return false
	}
	if fields["main_tree"] == "" || !samePath(ospath.Realpath(fields["main_tree"]), ospath.Realpath(main.Root)) {
		return false
	}
	if fields["common_dir"] == "" || !samePath(ospath.Realpath(fields["common_dir"]), common) {
		return false
	}
	if _, ok := fields["base_branch"]; !ok {
		return false
	}
	work := WorktreePath(root, name)
	if !ospath.IsDir(work) || ospath.IsLink(work) || !IsWorktreeOf(main.Root, work) {
		return false
	}
	workCommon, ok := CommonDir(work)
	if !ok || !samePath(workCommon, common) {
		return false
	}
	branch, ok := BranchOf(work)
	return ok && pathcase.Fold(pyfmt.NFC(branch)) == pathcase.Fold(pyfmt.NFC(name))
}

// DirectRecords はこのユーザのチェックアウトの、有効な横で作業する記録。
func DirectRecords(root string, main Tree) []DirectRecord {
	common, ok := CommonDir(main.Root)
	if !ok {
		return nil
	}
	base := ospath.Join(common, DirectDir)
	names, _ := ospath.DiskListdir(base)
	var out []DirectRecord
	for _, name := range names {
		path := ospath.Join(base, name)
		if !ospath.IsFile(path) || strings.HasPrefix(name, ".") {
			continue
		}
		text, err := ospath.ReadDiskText(path)
		if err != nil {
			continue
		}
		fields := ParseDirect(text)
		if validDirect(root, main, common, name, fields) {
			out = append(out, DirectRecord{name, main.Root, fields["base_branch"]})
		}
	}
	return out
}

// SideBySideRoots は有効な記録があるユーザのチェックアウト（ワークスペース自身かプロジェクト）。
func SideBySideRoots(root, projectsDir string) []Tree {
	var out []Tree
	for _, m := range append([]Tree{MainTree(root)}, Projects(projectsDir)...) {
		if len(DirectRecords(root, m)) > 0 {
			out = append(out, m)
		}
	}
	return out
}
