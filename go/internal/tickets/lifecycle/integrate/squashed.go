package integrate

import (
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/place"
)

// UnsetK は取り込みの記録の k（fast-forward で載せたコミット）がまだ無いことを表す値。
const UnsetK = "-"

type doneCopy struct {
	name   string
	fields map[string]string
}

// SquashedPaths は base..HEAD のコミットのうち、integrate の squash（か版 4 の K）と git で確かめたものだけが変えたパス
// （相対）。ターンの終わりの検査が使う。integrate-done は squash をコミットしてから片付けるので、外さないと正規の
// 取り込みが毎回報告に並ぶ。記録は `.git` の中にありエージェントにも書けるので、内容は信用せず git の状態で確かめる。
// 返すのは、確かめたコミットが変え、それ以外のコミットが 1 度も変えていないパスだけ。読めないものがあれば空。
// conf・root・project は、記録の名前がチケットに結び付くか（copyIsBound）を引く材料。project は top のツリーのプロジェクト。
func SquashedPaths(conf *config.Settings, root, project, top, base string) map[string]bool {
	if top == "" || base == "" {
		return nil
	}
	ddir, ok := git.Text(top, []string{"rev-parse", "--git-path", DoneDir}, git.Options{})
	if !ok || ddir == "" {
		return nil
	}
	if !ospath.IsAbs(ddir) {
		ddir = ospath.Join(top, ddir)
	}
	names, err := ospath.DiskListdir(ddir)
	if err != nil {
		return nil
	}
	// 1. 取り込み済みの記録（.git/ccnavi-done 以下）を読む。
	// branch が記録の名前と一致するものが squash の記録、version が直接作業の版のものが版 4 の記録。
	var records, direct []doneCopy
	for _, n := range names {
		if strings.HasPrefix(n, ".") {
			continue
		}
		r := doneCopy{n, ReadRecord(ospath.Join(ddir, n))}
		if r.fields["branch"] == n {
			records = append(records, r)
		}
		if r.fields["version"] == tree.DirectVersion {
			direct = append(direct, r)
		}
	}
	if len(records) == 0 && len(direct) == 0 {
		return nil
	}
	listed := git.Run(top, []string{"rev-list", "--first-parent", "--no-merges", "--parents", base + "..HEAD"}, git.Options{})
	if !listed.OK() {
		return nil
	}
	// 2. base..HEAD の各コミットが変えたパスを、確かめた取り込み（squashed）とそれ以外（other）に分ける。
	// 3. 取り込み以外のコミットが 1 度でも変えたパスは、取り込みの成果から外す。
	squashed, other := map[string]bool{}, map[string]bool{}
	for _, line := range strings.Split(listed.Out, "\n") {
		words := strings.Fields(line)
		if len(words) == 0 {
			continue
		}
		sha, parents := words[0], words[1:]
		paths, ok := git.CommitPaths(top, sha, git.Options{})
		if !ok {
			return nil
		}
		into := other
		bound := func(name string) bool { return copyIsBound(conf, root, project, top, name) }
		if len(parents) == 1 && (isSquash(top, sha, parents[0], records, bound) || isDirectK(top, sha, parents[0], direct, bound)) {
			into = squashed
		}
		for p := range paths {
			into[p] = true
		}
	}
	for p := range other {
		delete(squashed, p)
	}
	return squashed
}

// isDirectK はコミットが版 4 の取り込みの K（integrate-done が fast-forward で載せたコミット）か。K の親が記録の head、
// K の木が記録の c_tree で、記録に k があればそれが K 自身で、名前がチケットに結び付かないこと。
func isDirectK(top, sha, parent string, records []doneCopy, bound func(string) bool) bool {
	treeSHA, ok := git.TreeOf(top, sha, git.Options{})
	if !ok {
		return false
	}
	for _, r := range records {
		if r.fields["head"] != parent || r.fields["c_tree"] != treeSHA {
			continue
		}
		k, has := r.fields["k"]
		if !has {
			k = UnsetK
		}
		if k != "" && k != UnsetK && k != sha {
			continue
		}
		if bound(r.name) {
			continue
		}
		return true
	}
	return false
}

// isSquash はコミットが、どれかの記録の squash だと git で確かめられるか。C の親が記録の base、C のツリーが
// 記録の tree、記録の branch_sha（B）が HEAD の祖先でなく、B を含むブランチと stash が無く、merge-tree の結果が C の
// ツリーと下書きの置き場を除いて同じこと。
func isSquash(top, sha, parent string, records []doneCopy, bound func(string) bool) bool {
	treeSHA, ok := git.TreeOf(top, sha, git.Options{})
	if !ok {
		return false
	}
	for _, r := range records {
		if r.fields["base"] != parent || r.fields["tree"] != treeSHA {
			continue
		}
		tip := r.fields["branch_sha"]
		if tip == "" || bound(r.name) {
			continue
		}
		if got, ok := git.Text(top, []string{"rev-parse", "--verify", "--quiet", tip + "^{commit}"}, git.Options{}); !ok || got != tip {
			continue
		}
		// 祖先でなければ NotAncestorCode。それ以外（起こせない・読めない・祖先）は外さない。
		if ancestor := git.IsAncestor(top, tip, "HEAD", git.Options{}); ancestor.Failure != nil || ancestor.Code != git.NotAncestorCode {
			continue
		}
		holders := git.Run(top, []string{"for-each-ref", "--format=%(refname)", "--contains", tip, "refs/heads", "refs/stash"}, git.Options{})
		if !holders.OK() || ospath.Strip(holders.Out) != "" {
			continue
		}
		merged := git.Run(top, []string{"merge-tree", "--write-tree", parent, tip}, git.Options{})
		if !merged.OK() || ospath.Strip(merged.Out) == "" {
			continue
		}
		first, _, _ := strings.Cut(merged.Out, "\n")
		if sameButHandoff(top, ospath.Strip(first), treeSHA) {
			return true
		}
	}
	return false
}

// sameButHandoff は 2 つのツリーの差が下書きの置き場の下だけ（差が無いのも含む）か。integrate は squash のあとで
// ブランチが持ち込んだ下書きを index から外すので、その分だけ違う。読めないときは偽。
func sameButHandoff(top, left, right string) bool {
	if left == "" || right == "" {
		return false
	}
	if left == right {
		return true
	}
	paths, ok := git.TreeDiffPaths(top, left, right, git.Options{})
	if !ok {
		return false
	}
	prefix := place.Handoff + "/"
	for p := range paths {
		if !strings.HasPrefix(p, prefix) {
			return false
		}
	}
	return true
}

// copyIsBound はその名前のチケットに結び付くか（boundCheck と同じ材料。設定の置き場・全ツリー・退避を大文字小文字を
// 畳んで見る）、origin に同名のブランチが在るか。結び付くなら片付けた記録のコピーを信用しない。片付けの後は
// ワークツリーが無く boundCheck の親のブランチの確かめが効かないので、親のブランチは参照で見分ける。決められない
// ものは結び付くと読む（外さずに報告する側に倒す）。
func copyIsBound(conf *config.Settings, root, project, top, name string) bool {
	if reason, unsure := boundCheck(conf, root, tree.Tree{Name: name, Project: project}, time.Time{}); reason != "" || unsure {
		return true
	}
	return git.Run(top, []string{"show-ref", "--verify", "--quiet", "refs/remotes/origin/" + name}, git.Options{}).OK()
}
