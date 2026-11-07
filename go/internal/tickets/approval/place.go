package approval

import (
	"errors"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

const (
	// FollowupKey は続きの子の目印（トップレベルの欄）。見た子の識別子の列。
	FollowupKey = "followup_of"
	// nowhereDir は親のワークツリーが決まらないときに読む、誰も作らない置き場（state の下）。
	nowhereDir = "no-parent-worktree"
	// workspaceLabel はワークスペースルートを文に出すときの名前。
	workspaceLabel = "ワークスペースルート"
)

// CopyPath は作業中の承認済みチケット（doing/）のパス。判定が読むのはここだけ。
func CopyPath(approvedDir, id string) string {
	return ospath.Join(approvedDir, string(place.Doing), id+config.MdExt)
}

// ClosedPath は閉じた承認済みチケット（done/）のパス。
func ClosedPath(approvedDir, id string) string {
	return ospath.Join(approvedDir, string(place.Done), id+config.MdExt)
}

// ParentPaths はツリーで識別子 id の親チケットが在りうるファイル（承認済みの doing/・done/、提案の todo/・review/）。
// 見る順に並ぶ。
func ParentPaths(conf *config.Settings, treeRoot, id string) []string {
	approvedDir := conf.ApprovedDir(treeRoot)
	return []string{
		CopyPath(approvedDir, id),
		ClosedPath(approvedDir, id),
		place.File(treeRoot, conf.Tickets, place.Todo, id),
		place.File(treeRoot, conf.Tickets, place.Review, id),
	}
}

// FrontOf はチケットのファイルの frontmatter。無い・読めなければ nil（チケットとしての検査は掛けない）。
func FrontOf(path string) *yaml.Map {
	text, ok := fsio.ReadText(path, fsio.Strict)
	if !ok {
		return nil
	}
	front, err := ticket.FrontMapping(text)
	if err != nil {
		return nil
	}
	return front
}

// ParentIn は paths を順に見て、最初に見つかった id の親チケット（`ticket: <id>` で parent が無い）の frontmatter を
// 返す。見るのは frontmatter の ticket と parent だけで、チケットとしての検査（範囲の欄など）は掛けない。親チケットの
// 見方は shask の確認と --lint で同じにするため、ここ 1 か所で決める。見つからなければ nil。在るのに読めない
// ファイルがあれば broken が真。
func ParentIn(paths []string, id string) (front *yaml.Map, broken bool) {
	for _, path := range paths {
		if !ospath.IsFile(path) {
			continue
		}
		found := FrontOf(path)
		if found == nil {
			broken = true
			continue
		}
		if ospath.Strip(found.Text("ticket")) == id && !pyfmt.Truthy(found.Get("parent")) {
			return found, false
		}
	}
	return nil, broken
}

// reviewDir はそのツリーの提案の置き場の review/。
func reviewDir(conf *config.Settings, treeRoot string) string {
	return place.Dir(treeRoot, conf.Tickets, place.Review)
}

// Trees は承認済みチケットを持ちうるツリー全部（ワークスペース、プロジェクト、ワークツリー）。
func Trees(conf *config.Settings, root string) []tree.Tree {
	return tree.AllTrees(root, conf.Projects)
}

// ErrNoParentWorktree は親のワークツリーが無い（HEAD のブランチ名がフォルダ名と合わない・止まっている）。
// ほかのツリーのコピーで代用しないので、書く先が決まらない。理由の文は NoParentError が持つ。
var ErrNoParentWorktree = errors.New("親のワークツリーが決まらない")

// NoParentError は書く先が決まらない理由（Error() は理由の文だけ）。errors.Is で ErrNoParentWorktree に当たる。
type NoParentError struct{ Reason string }

func (e *NoParentError) Error() string        { return e.Reason }
func (e *NoParentError) Is(target error) bool { return target == ErrNoParentWorktree }

// HomeDir は、この識別子の承認済みチケットを置く置き場を返す。親のワークツリーの置き場だけである。書く先も読む先も 1 つに決めるためである。子のワークツリーにも checkout されるが、そこへ書くと同じ識別子が 2 通りになってしまう。project はチケットのリポジトリである（ワークスペース自身なら空）。決まらなければ *NoParentError を返す。
func HomeDir(conf *config.Settings, root, id, parent, project string) (string, error) {
	return homeDir(conf, root, homeOf(id, parent), func(f *standing.Families, home string) standing.Standing {
		return f.Standing(home, project)
	})
}

// HomeDirRead は HomeDir の読む側。決まらなければ（フォルダが実在しても）存在しない置き場のパスを返し、読む側は
// 「何も無い」と読む。返したパスへは書かない（書くとそこに置き場ができる）。
func HomeDirRead(conf *config.Settings, root, id, parent, project string) string {
	where, err := HomeDir(conf, root, id, parent, project)
	if err != nil {
		return nowhere(conf, root, homeOf(id, parent))
	}
	return where
}

// HomeDirReadAny はリポジトリの分からないチケットの HomeDirRead。同じ名前の親のワークツリーのあるリポジトリを
// 全部探し、2 つ以上にあれば決めない。
func HomeDirReadAny(conf *config.Settings, root, id, parent string) string {
	home := homeOf(id, parent)
	where, err := homeDir(conf, root, home, func(f *standing.Families, home string) standing.Standing {
		return f.StandingAny(home)
	})
	if err != nil {
		return nowhere(conf, root, home)
	}
	return where
}

func homeOf(id, parent string) string {
	if parent != "" {
		return parent
	}
	return id
}

func homeDir(conf *config.Settings, root, home string, pick func(*standing.Families, string) standing.Standing) (string, error) {
	st := pick(standing.NewFamilies(conf, root), home)
	if st.Home == nil || st.Stop != "" {
		reason := st.Stop
		if reason == "" {
			reason = "親のワークツリー（.claude/worktrees/" + home + "）がありません"
		}
		return "", &NoParentError{reason}
	}
	return conf.ApprovedDir(st.Home.Root), nil
}

// nowhere は親のワークツリーが決まらないときに読む、存在しない置き場のパス。ワークツリーの置き場の外の、
// state の下の決まった名前にする（誰も作らない）。
func nowhere(conf *config.Settings, root, home string) string {
	base := conf.State
	if base == "" {
		base = ospath.Join(root, config.DefaultState)
	}
	return conf.ApprovedDir(ospath.Join(base, nowhereDir, home))
}

// SourcePath は提案の、そのツリーからの相対パス（区切りは "/"）。手元と Chrome で承認済みチケットの中身を同じに
// するため絶対パスは書かない。ツリーの外（ツリーが分からない）ときだけパスのまま返す。
func SourcePath(t *ticket.Ticket) string {
	if t.TreeRoot != "" && t.Path != "" {
		rel := tree.Relative(tree.Tree{Name: t.Tree, Root: t.TreeRoot}, t.Path)
		// 相対を作れない（Windows の別のドライブ）なら Relative は絶対パスを返す。パスのまま返す。
		if rel != "" && rel != ".." && !strings.HasPrefix(rel, "../") && !ospath.IsAbs(rel) {
			return rel
		}
	}
	return t.Path
}

// treePath はツリーの名前を、ワークスペースルートからのパスに直す。ルート自身は「ワークスペースルート」。
func treePath(conf *config.Settings, root, name, project string) string {
	var found []tree.Tree
	for _, t := range Trees(conf, root) {
		if t.Name == name {
			found = append(found, t)
		}
	}
	if len(found) == 0 {
		if name == "" {
			return workspaceLabel
		}
		return name
	}
	pick := found[0]
	for _, t := range found {
		if t.Project == project {
			pick = t
			break
		}
	}
	rel, ok := ospath.Relpath(pick.Root, root)
	if !ok {
		rel = pick.Root
	}
	rel = ospath.Slashed(rel)
	if rel == "." {
		return workspaceLabel
	}
	return rel
}
