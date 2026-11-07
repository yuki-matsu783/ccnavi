package status

import (
	"strings"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/ticket"
)

// fileKind は置き場のファイルの git での姿。uncommitted はファイルそのものがまだコミットに無い（承認が運ばれていない）。
// modified はコミット済みのファイルに未コミットの変更がある（着手などで書いた欄）。
type fileKind int

const (
	fileUnknown fileKind = iota
	fileUncommitted
	fileModified
	fileUnpushed
	filePushed
	fileNoUpstream
)

// fileState はファイル 1 つの git での姿。upstream は pushed・unpushed のときの比べた先。
type fileState struct {
	kind     fileKind
	upstream string
}

// label は状態を、利用者に見せる文にする。
func (f fileState) label() string {
	switch f.kind {
	case fileUncommitted:
		return "未コミット"
	case fileModified:
		return "コミット済みのファイルに未コミットの変更がある（着手などで書いた欄）"
	case fileUnpushed:
		return "コミット済み・未push（" + f.upstream + " に未反映の変更があります）"
	case filePushed:
		return "push 済み（" + f.upstream + "）"
	case fileNoUpstream:
		return "コミット済み（リモート追跡参照がないため、push済みか確認できません）"
	}
	return "Gitから状態を取得できません。"
}

// fileStates はチケットのファイルごと（パスで引く）の git での姿。ツリーごとに git を数回だけ打つ。読めないツリーの
// ファイルは入れない（fileUnknown として読む）。
func fileStates(found []*ticket.Ticket) map[string]fileState {
	var order []string
	byTree := map[string][]*ticket.Ticket{}
	for _, t := range found {
		if t.TreeRoot == "" || t.Path == "" {
			continue
		}
		if _, ok := byTree[t.TreeRoot]; !ok {
			order = append(order, t.TreeRoot)
		}
		byTree[t.TreeRoot] = append(byTree[t.TreeRoot], t)
	}
	// ツリーごとに、(1) 未コミット・変更あり (2) リモート追跡の ref より先に進んでいるか、を調べて姿を決める。
	// 調べる順が姿の優先順になる（未コミット、変更あり、追跡先なし、確認できない、未 push、push 済み）。
	out := map[string]fileState{}
	for _, treeRoot := range order {
		rels := map[string]string{}
		var paths []string
		for _, t := range byTree[treeRoot] {
			rel := ospath.Slashed(ospath.RelOr(t.Path, treeRoot))
			if _, ok := rels[t.Path]; !ok {
				paths = append(paths, rel)
			}
			rels[t.Path] = rel
		}
		added, dirty, ok := dirtyPaths(treeRoot, paths)
		if !ok {
			continue
		}
		upstream := upstreamOf(treeRoot)
		var ahead map[string]bool
		aheadOK := true
		if upstream != "" {
			ahead, aheadOK = aheadPaths(treeRoot, upstream, paths)
		}
		for path, rel := range rels {
			switch {
			case added[rel]:
				out[path] = fileState{kind: fileUncommitted}
			case dirty[rel]:
				out[path] = fileState{kind: fileModified}
			case upstream == "":
				out[path] = fileState{kind: fileNoUpstream}
			case !aheadOK:
				out[path] = fileState{kind: fileUnknown}
			case ahead[rel]:
				out[path] = fileState{kind: fileUnpushed, upstream: upstream}
			default:
				out[path] = fileState{kind: filePushed, upstream: upstream}
			}
		}
	}
	return out
}

// gitOptions は git に渡す共通の設定。パスは引用せずに読み、時間の上限を付ける。
func gitOptions() git.Options { return git.Options{Timeout: git.ReadTimeout, RawPaths: true} }

// dirtyPaths は (まだコミットに無いパス, 未コミットの変更があるパス)。前者は未追跡と索引に足しただけのもの（`??`・`A`）と、
// 改名・コピーで入った新しいパス（`R`・`C`）。git を読めなければ偽。
func dirtyPaths(treeRoot string, rels []string) (added, found map[string]bool, ok bool) {
	done := git.Status(treeRoot, git.Porcelain{NUL: true, Untracked: git.UntrackedAll, Paths: rels}, gitOptions())
	if !done.OK() {
		return nil, nil, false
	}
	added, found = map[string]bool{}, map[string]bool{}
	// -z の出力は `XY <パス>` を NUL でつないだもの。改名とコピーは、元のパスが次の欄に続く。
	fields := strings.Split(done.Out, "\x00")
	for i := 0; i < len(fields); {
		item := fields[i]
		i++
		if len(item) < 4 {
			continue
		}
		found[item[3:]] = true
		if item[:2] == "??" || strings.ContainsRune("ARC", rune(item[0])) {
			added[item[3:]] = true
		}
		if item[0] == 'R' || item[0] == 'C' {
			// 改名とコピーは、元のパスが次の欄に続く。
			if i < len(fields) {
				found[fields[i]] = true
			}
			i++
		}
	}
	return added, found, true
}

// upstreamOf は今のブランチのリモート追跡の ref。設定が無ければ `origin/<ブランチ>` があればそれ。無ければ空。
func upstreamOf(treeRoot string) string {
	// 1. ブランチに設定されたリモート追跡の ref があればそれ。2. 無ければ、同名の origin のブランチが手元にあるときだけそれ。
	o := git.Options{Timeout: git.ReadTimeout}
	code, out := git.Output(treeRoot, []string{"rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"}, o)
	if code == 0 && ospath.Strip(out) != "" {
		return ospath.Strip(out)
	}
	branch, ok := tree.BranchOf(treeRoot)
	if !ok || branch == "" {
		return ""
	}
	if code, _ := git.Output(treeRoot, []string{"rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + branch}, o); code != 0 {
		return ""
	}
	return "origin/" + branch
}

// aheadPaths はリモート追跡の ref と分かれたあとに、手元のコミットで変えたパス。読めなければ偽。
func aheadPaths(treeRoot, upstream string, rels []string) (map[string]bool, bool) {
	// 三点の差分（共通の祖先から HEAD まで）で、手元だけの変更を取る。改名は検出せず、削除と追加として数える。
	args := append([]string{"diff", "--name-only", "-z", "--no-renames", upstream + "...HEAD", "--"}, rels...)
	done := git.Run(treeRoot, args, gitOptions())
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
