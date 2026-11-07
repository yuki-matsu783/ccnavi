package standing

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/ids"
)

// StopKind は止める理由の種別。案内（Guidance）は文面ではなくこれで分ける。値は文面ファイルの場合の名前。
type StopKind string

const (
	StopNone      StopKind = ""
	StopBusy      StopKind = "busy"      // 親のワークツリーに途中の操作がある
	StopMismatch  StopKind = "mismatch"  // HEAD のブランチ名を変換した識別子がフォルダ名と合わない
	StopAbsent    StopKind = "absent"    // 親のワークツリーがない
	StopAmbiguous StopKind = "ambiguous" // 同じ識別子の親が複数のリポジトリにある
)

// textStanding は止めたときの解き方の文の識別子。
const textStanding = "NEXT_STANDING"

// branchPlaceholder は HEAD から親のブランチ名を読めなかったときに文に出す名前。
const branchPlaceholder = "<ブランチ名>"

// Standing は親子のチケットの立ち位置。Stop は決まらないので止める理由（空なら止めない）、Kind はその種別。
// Home は親のワークツリー、Branch はその HEAD から読んだ親のブランチ名（読めなければ空）、Head は合わないときの
// ワークツリーの HEAD の生のブランチ名。
type Standing struct {
	Family string
	Repo   string
	Home   *tree.Tree
	Stop   string
	Branch string
	Kind   StopKind
	Head   string
}

// BranchName は文に出す親のブランチ名。HEAD から読めなければ `<ブランチ名>`。
func (s Standing) BranchName() string {
	if s.Branch == "" {
		return branchPlaceholder
	}
	return s.Branch
}

type key struct{ project, family string }

// Families は 1 回の判定の中で、親子のチケットの立ち位置を引く窓口。ワークツリーの一覧は 1 度だけ読む
// （hook のたびに何度も走る走査の中で、親子のチケットごとに並べ直さないため）。
type Families struct {
	projectsDir string
	root        string
	worktrees   []tree.Tree
	listed      bool
	standings   map[key]Standing
}

// NewFamilies は窓口を作る。
func NewFamilies(conf *config.Settings, root string) *Families {
	return &Families{projectsDir: conf.Projects, root: root, standings: map[key]Standing{}}
}

// Worktrees はワークツリーの一覧（1 度だけ読む）。
func (f *Families) Worktrees() []tree.Tree {
	if !f.listed {
		f.worktrees, f.listed = tree.Worktrees(f.root, f.projectsDir), true
	}
	return f.worktrees
}

// Standing はこの親子のチケットの立ち位置。
func (f *Families) Standing(family, project string) Standing {
	k := key{project, family}
	if st, ok := f.standings[k]; ok {
		return st
	}
	st := f.read(family, project)
	f.standings[k] = st
	return st
}

// StandingAny はリポジトリの分からない親子のチケットの立ち位置。同じ名前の親のワークツリーのあるリポジトリを
// 全部探し、2 つ以上にあればどれとも決めずに止める（ワークスペースの web-i0012 と、プロジェクト web の issue 12
// の親子のチケットなど）。どこにも無ければワークスペース自身として引く（親のワークツリーが無いので止まる）。
func (f *Families) StandingAny(family string) Standing {
	var repos []string
	for _, w := range f.Worktrees() {
		if r := tree.RepoKey(w.Project); w.Name == family && !containsString(repos, r) {
			repos = append(repos, r)
		}
	}
	switch len(repos) {
	case 0:
		return f.Standing(family, "")
	case 1:
		return f.Standing(family, tree.ProjectOfKey(repos[0]))
	}
	first := f.Standing(family, tree.ProjectOfKey(repos[0]))
	return Standing{
		Family: family, Repo: first.Repo, Kind: StopAmbiguous,
		Stop: "親子のチケット " + family + " が複数のリポジトリ（" + strings.Join(repos, ", ") + "）にある。どれか決まらない",
	}
}

func containsString(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// HomeTree は親のワークツリー（名前が親の識別子で、元が同じリポジトリで、HEAD のブランチ名を変換したものが名前と
// 一致するもの）。無ければ nil。
func (f *Families) HomeTree(family, project string) *tree.Tree {
	if work := f.named(family, project); work != nil && HeadBranch(work.Root, family) != "" {
		return work
	}
	return nil
}

// Branch は親のブランチ名（親のワークツリーの HEAD から読む）。読めない・合わないときは空。
func (f *Families) Branch(family, project string) string {
	if home := f.HomeTree(family, project); home != nil {
		return HeadBranch(home.Root, family)
	}
	return ""
}

func (f *Families) named(family, project string) *tree.Tree {
	for _, w := range f.Worktrees() {
		if pathcase.Fold(w.Name) == pathcase.Fold(family) && w.Project == project {
			return &w
		}
	}
	return nil
}

// read は親のワークツリーの状態を読む。在ればそれが正。無ければ同名のフォルダの途中の操作（busy）、HEAD の
// 食い違い（mismatch）、フォルダが無い（absent）の順に理由を決める。
func (f *Families) read(family, project string) Standing {
	repo := tree.RepoKey(project)
	branch := f.Branch(family, project)
	if home := f.HomeTree(family, project); home != nil {
		return Standing{Family: family, Repo: repo, Home: home, Branch: branch}
	}
	named := f.named(family, project)
	busy := ""
	if named != nil {
		busy = tree.BusyOf(named.Root)
	}
	where := strings.ReplaceAll(tree.WorktreesDir, ospath.Sep, "/") + "/" + family
	st := Standing{Family: family, Repo: repo, Branch: branch}
	switch {
	case busy != "":
		st.Kind = StopBusy
		st.Stop = "親のワークツリー（" + where + "）に途中の操作（" + busy + "）がある。" +
			"済ませるか取りやめるまで、どのチケットを正とするかが決まらない"
	case named != nil:
		st.Kind = StopMismatch
		st.Head, _ = tree.BranchOf(named.Root)
		st.Stop = "親のワークツリー（" + where + "）の HEAD のブランチ名を変換した識別子が" +
			"フォルダ名（" + family + "）と合わない。" +
			"どのチケットを正とするかが決まらない"
	default:
		st.Kind = StopAbsent
		st.Stop = "親のワークツリー（" + where + "）がありません。" +
			"ほかのツリーのコピーでは代用しない（どのチケットを正とするかが決まらない）"
	}
	return st
}

// HeadBranch はそのツリーの HEAD が指すブランチ名。変換した識別子が family と一致するときだけ返し、そうでなければ
// 空。NFC にそろえるのはフォルダ名（macOS の HFS+ は NFD で返す）の側だけで、HEAD のブランチ名はそろえない
// （NFD のブランチ名は使えない名前で、sh もバイト列のまま比べる）。ファイルだけを読む。
func HeadBranch(treeRoot, family string) string {
	head, ok := tree.BranchOf(treeRoot)
	if ok && head != "" && ids.BranchToID(head) == pyfmt.NFC(family) {
		return head
	}
	return ""
}

// SameTree は同じツリーのルートか（表記を揃えて比べる）。
func SameTree(a, b string) bool {
	return a != "" && b != "" && pathcase.SameNorm(a, b)
}

// Guidance は止めたときの解き方（1 行ずつ）。文は種別ごとに文面ファイルが持つ。呼び手は行の途中や hint の値にも
// 使うので、見出し（次: ）は外して文だけを返す。
func Guidance(root string, st Standing) []string {
	kind := st.Kind
	if kind != StopBusy && kind != StopMismatch && kind != StopAmbiguous {
		kind = StopAbsent
	}
	now := "（HEAD がブランチを指していない）"
	if st.Head != "" {
		now = "（今の HEAD: " + st.Head + "）"
	}
	said := texts.Say(root, textStanding, texts.Values{"name": st.Family, "branch": st.BranchName(), "now": now},
		texts.Variants{"next": string(kind)})
	return []string{strings.TrimPrefix(said, texts.NextLabel)}
}
