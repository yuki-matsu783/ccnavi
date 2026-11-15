// Package dirs は ccnavi-dirs.sh の本体。セッションの頭で、projects/ に clone 済みの外リポジトリを新しい順に最大 10 件、
// 文脈へ入れる（出力はモデルに届く）。
package dirs

import (
	"cmp"
	"fmt"
	"io"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
)

// limit は出す件数の上限。
const limit = 10

// project は並べる 1 件。
type project struct {
	name string
	at   int64 // 最新コミットの時刻（無い・読めなければ 0）
}

// Report は root/projects/ の直下で `.git`（ディレクトリでもファイルでも）を持つものを、最新コミットの時刻が新しい順
// （同じなら名前の順。コミットが無い・読めないものは末尾）に、プロジェクト名だけ出す。11 件以上あるときは、残りの件数だけ
// 添える。該当が無ければ何も出さない。
func Report(stdout io.Writer, root string) {
	base := ospath.Join(root, "projects")
	names, _ := ospath.DiskListdir(base)
	var found []project
	for _, name := range names {
		dir := ospath.Join(base, name)
		// 隠しディレクトリ（. で始まる名前）は数えない（`ls -d */` と同じ）。
		if strings.HasPrefix(name, ".") || !ospath.IsDir(dir) || !tree.HasGit(dir) {
			continue
		}
		at, _ := strconv.ParseInt(strings.TrimSpace(gitOut(dir)), 10, 64)
		found = append(found, project{name, at})
	}
	if len(found) == 0 {
		return
	}
	slices.SortStableFunc(found, func(a, b project) int {
		return cmp.Or(cmp.Compare(b.at, a.at), cmp.Compare(a.name+"/", b.name+"/"))
	})
	fmt.Fprintln(stdout, "案内: projects/ のプロジェクト")
	for _, p := range found[:min(limit, len(found))] {
		fmt.Fprintln(stdout, "  - "+p.name)
	}
	if len(found) > limit {
		fmt.Fprintf(stdout, "案内: ほかに %d 件あります。\n", len(found)-limit)
	}
}

// gitOut は最新コミットの時刻（UNIX 秒）。読めなければ空。
func gitOut(dir string) string {
	done := git.Run(dir, []string{"log", "-1", "--format=%ct", "HEAD"}, git.Options{})
	if !done.OK() {
		return ""
	}
	return done.Out
}
