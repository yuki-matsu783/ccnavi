package branch

import (
	"errors"
	"io/fs"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// Bound は BaseBound の答え。sh がこの値を読む。
type Bound string

const (
	Yes     Bound = "yes"
	No      Bound = "no"
	Unknown Bound = "unknown"
)

const mdExt = ".md"

func sameName(a, b string) bool { return pathcase.Fold(pyfmt.NFC(a)) == pathcase.Fold(pyfmt.NFC(b)) }

// owner はユーザのチェックアウトに当たるワークスペースかプロジェクト。どれでもなければ偽。
func owner(conf *config.Settings, root, mainRoot string) (tree.Tree, bool) {
	projects := ""
	if conf.Projects != "" {
		projects = ospath.Join(root, conf.Projects)
	}
	want := pathcase.Real(mainRoot)
	for _, t := range append([]tree.Tree{tree.MainTree(root)}, tree.Projects(projects)...) {
		if pathcase.Real(t.Root) == want {
			return t, true
		}
	}
	return tree.Tree{}, false
}

func relSlashed(root, path string) string {
	if rel, ok := ospath.Relpath(path, root); ok {
		return ospath.Slashed(rel)
	}
	return ospath.Slashed(path)
}

// frontText は frontmatter の欄を Python の str(front.get(key) or "").strip() の形で読む。
func frontText(value any) string {
	if !pyfmt.Truthy(value) {
		return ""
	}
	return ospath.Strip(pyfmt.Str(value))
}

// BaseBound は直接作業の取り込み先のブランチ（mainRoot のチェックアウトの branch）がチケットに結び付くか
// （`ccnavi tree base-bound`）。理由は Yes のときだけ。
//
// 識別子の衝突はワークスペース全体のチケット・提案で見る。取り込み先との結び付きは取り込み先のリポジトリだけを
// 見る（その承認済みチケットの branch: か識別子、またはその退避の同じ名前の親）。ほかのリポジトリの同じ名前の
// ブランチでは断らない。デフォルトブランチにチケットのファイルが残っているだけでは結び付くと読まない。置き場や
// frontmatter を読めず決められないときは Unknown で、呼ぶ側は断る。
func BaseBound(conf *config.Settings, root, mainRoot, branch string) (Bound, string) {
	ident := ids.BranchToID(branch)
	home, ok := owner(conf, root, mainRoot)
	if !ok {
		return Unknown, ""
	}
	for _, baseRoot := range integrate.TreeRoots(conf, root) {
		approved := conf.ApprovedDir(baseRoot)
		proposals := place.ProposalsRoot(baseRoot, conf.Tickets)
		for _, at := range []struct {
			base  string
			state place.State
		}{{approved, place.Doing}, {approved, place.Done}, {proposals, place.Todo}, {proposals, place.Review}} {
			if found := integrate.NamedFile(ospath.Join(at.base, string(at.state)), ident); found != "" {
				return Yes, "チケットか提案（" + relSlashed(root, found) + "）の名前がブランチの識別子と同じ"
			}
		}
	}
	if archived, ok := archive.ArchivedFields(root, home.Project, ident); ok && archived.Parent == "" {
		return Yes, "取り込み先のリポジトリの退避（ready 済み）に同じ名前の親がある"
	}
	approved := conf.ApprovedDir(home.Root)
	unsure := false
	for _, state := range []place.State{place.Doing, place.Done} {
		directory := ospath.Join(approved, string(state))
		entries, err := ospath.DiskListdir(directory)
		if err != nil {
			if !errors.Is(err, fs.ErrNotExist) {
				unsure = true
			}
			continue
		}
		for _, e := range entries {
			if !strings.HasSuffix(e, mdExt) {
				continue
			}
			path := ospath.Join(directory, e)
			if !ospath.IsFile(path) {
				continue
			}
			text, ok := fsio.ReadText(path, fsio.Strict)
			front, err := ticket.FrontMapping(text)
			if !ok || err != nil || front == nil {
				unsure = true
				continue
			}
			tied := frontText(front.Get("branch"))
			if (tied != "" && sameName(tied, branch)) || sameName(frontText(front.Get("ticket")), ident) {
				return Yes, "取り込み先のリポジトリの承認済みチケット（" + relSlashed(root, path) + "）に結び付く"
			}
		}
	}
	if unsure {
		return Unknown, ""
	}
	return No, ""
}
