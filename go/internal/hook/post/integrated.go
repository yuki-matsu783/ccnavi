package post

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/place"
)

const (
	// integrateDoneDir は `ccnavi-git.sh integrate-done` が片付けの直前に残す、取り込みの記録のコピーの置き場（git-path）。
	integrateDoneDir = "ccnavi-integrate-done"
	// directVersion は fast-forward で載せた取り込み（K）の記録の版。
	directVersion = "4"
	// unsetK は記録の k が無いことを表す値。
	unsetK = "-"
	// ticketExt はチケットのファイルの拡張子。
	ticketExt = ".md"
)

// boundPlaces は、その名前のチケットが在れば取り込みを外さない置き場（integrate が断る「チケットに結び付く」判定の一部）。
var boundPlaces = []string{
	config.DefaultApproved + "/" + string(place.Doing),
	config.DefaultApproved + "/" + string(place.Done),
	config.DefaultTickets + "/" + string(place.Todo),
	config.DefaultTickets + "/" + string(place.Review),
}

type doneRecord struct {
	name   string
	fields map[string]string
}

// integratedPaths は base..HEAD のコミットのうち、integrate の squash（か版 4 の K）と git で確かめたものだけが変えたパス
// （相対）。ターンの終わりの検査が使う。integrate-done は squash をコミットしてから片付けるので、外さないと正規の
// 取り込みが毎回報告に並ぶ。記録は `.git` の中にありエージェントにも書けるので、内容は信用せず git の状態で確かめる。
// 返すのは、確かめたコミットが変え、それ以外のコミットが 1 度も変えていないパスだけ。読めないものがあれば空。
func integratedPaths(top, base string) map[string]bool {
	if top == "" || base == "" {
		return nil
	}
	ddir, ok := git.Text(top, []string{"rev-parse", "--git-path", integrateDoneDir}, git.Options{})
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
	var records, direct []doneRecord
	for _, n := range names {
		if strings.HasPrefix(n, ".") {
			continue
		}
		r := doneRecord{n, readRecord(ospath.Join(ddir, n))}
		if r.fields["branch"] == n {
			records = append(records, r)
		}
		if r.fields["version"] == directVersion {
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
	squashed, other := map[string]bool{}, map[string]bool{}
	for _, line := range strings.Split(listed.Out, "\n") {
		words := strings.Fields(line)
		if len(words) == 0 {
			continue
		}
		sha, parents := words[0], words[1:]
		paths, ok := commitPaths(top, sha)
		if !ok {
			return nil
		}
		into := other
		if len(parents) == 1 && (isIntegrateCommit(top, sha, parents[0], records) || isDirectCommit(top, sha, parents[0], direct)) {
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

// commitPaths はそのコミットが親から変えたパス（改名は切る。git.Committed と同じ）。
func commitPaths(top, sha string) (map[string]bool, bool) {
	done := git.Run(top, []string{"diff-tree", "--no-commit-id", "--root", "-r", "--no-renames",
		"--ignore-submodules=none", "--name-only", "-z", sha}, git.Options{RawPaths: true})
	if !done.OK() {
		return nil, false
	}
	out := map[string]bool{}
	for _, p := range strings.Split(done.Out, "\x00") {
		if p != "" {
			out[p] = true
		}
	}
	return out, true
}

func treeOf(top, sha string) (string, bool) {
	return git.Text(top, []string{"rev-parse", "--verify", sha + "^{tree}"}, git.Options{})
}

// isDirectCommit はコミットが版 4 の取り込みの K（integrate-done が fast-forward で載せたコミット）か。K の親が記録の head、
// K の木が記録の c_tree で、記録に k があればそれが K 自身で、名前がチケットに結び付かないこと。
func isDirectCommit(top, sha, parent string, records []doneRecord) bool {
	treeSHA, ok := treeOf(top, sha)
	if !ok {
		return false
	}
	for _, r := range records {
		if r.fields["head"] != parent || r.fields["c_tree"] != treeSHA {
			continue
		}
		k, has := r.fields["k"]
		if !has {
			k = unsetK
		}
		if k != "" && k != unsetK && k != sha {
			continue
		}
		if ticketBound(top, r.name) {
			continue
		}
		return true
	}
	return false
}

// isIntegrateCommit はコミットが、どれかの記録の squash だと git で確かめられるか。C の親が記録の base、C のツリーが
// 記録の tree、記録の branch_sha（B）が HEAD の祖先でなく、B を含むブランチと stash が無く、merge-tree の結果が C の
// ツリーと下書きの置き場を除いて同じこと。
func isIntegrateCommit(top, sha, parent string, records []doneRecord) bool {
	treeSHA, ok := treeOf(top, sha)
	if !ok {
		return false
	}
	for _, r := range records {
		if r.fields["base"] != parent || r.fields["tree"] != treeSHA {
			continue
		}
		tip := r.fields["branch_sha"]
		if tip == "" || ticketBound(top, r.name) {
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
	diff := git.Run(top, []string{"diff-tree", "-r", "--no-renames", "--name-only", "-z", left, right}, git.Options{RawPaths: true})
	if !diff.OK() {
		return false
	}
	prefix := place.Handoff + "/"
	for _, p := range strings.Split(diff.Out, "\x00") {
		if p != "" && !strings.HasPrefix(p, prefix) {
			return false
		}
	}
	return true
}

// ticketBound はその名前のチケットが承認済みか提案として在るか、origin に同名のブランチが在るか。片付けの後は
// ワークツリーが無いので、親のブランチは参照で見分ける。手元の退避は見ない（記録を偽造して外させないため）。
func ticketBound(top, name string) bool {
	for _, at := range boundPlaces {
		for _, base := range []string{top, ospath.Join(top, tree.WorktreesDir, name)} {
			if ospath.DiskLexists(ospath.Join(base, at, name+ticketExt)) {
				return true
			}
		}
	}
	return git.Run(top, []string{"show-ref", "--verify", "--quiet", "refs/remotes/origin/" + name}, git.Options{}).OK()
}

// readRecord は `<キー> <値>` を 1 行 1 項目で並べた記録を読む（sh の ccnavi_record_get と同じ形。同じキーは最初が勝つ）。
func readRecord(path string) map[string]string {
	out := map[string]string{}
	text, ok := fsio.ReadText(path, fsio.Replace)
	if !ok {
		return out
	}
	for _, line := range ospath.Splitlines(text) {
		key, value, _ := strings.Cut(line, " ")
		if _, seen := out[key]; key != "" && !seen {
			out[key] = value
		}
	}
	return out
}
