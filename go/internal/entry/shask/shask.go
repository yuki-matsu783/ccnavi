package shask

import (
	"fmt"
	"io"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/lifecycle/branch"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/sync/handoff"
)

// head は答えの頭の行。
const head = "tree 1"

// answer は yes・no で答える行の値。unreadable は、置き場に名前のファイルが在るのに frontmatter を読めず、親かどうかを
// 決められなかったこと（止める側の検査は親として、ツリーを動かす側は親でないとして扱う。呼ぶ側が決める）。
type answer string

const (
	yes        answer = "yes"
	no         answer = "no"
	unreadable answer = "unreadable"
)

func yesNo(b bool) answer {
	if b {
		return yes
	}
	return no
}

const (
	mdExt = ".md"
	// none は mr・stage が無いときの値。
	none = "-"
)

// requestedMark はフェーズのレビューを頼んだ印（`<N>.requested`）のファイル名。
var requestedMark = regexp.MustCompile(`^[0-9]+\.requested$`)

// Answer は command（`tree <問い> …`）に答え、終了コード（config.ExitOK・ExitError）と、問いの形に当たったかを返す。
// 当たらなければ何も書かない（cli がほかの形として読む）。
func Answer(stdout, stderr io.Writer, root string, conf *config.Settings, command []string) (int, bool) {
	if len(command) < 3 || command[0] != "tree" {
		return 0, false
	}
	verb, args := command[1], command[2:]
	var ok bool
	switch {
	case verb == "parent" && len(args) == 2:
		ok = parentTree(stdout, stderr, root, conf, args[0], args[1])
	case verb == "bound" && len(args) == 2:
		ok = boundTree(stdout, stderr, root, conf, args[0], args[1])
	case verb == "leftover" && len(args) == 2:
		ok = leftoverTree(stdout, stderr, root, conf, args[0], args[1])
	case verb == "base-bound" && len(args) == 2:
		ok = baseBoundTree(stdout, stderr, root, conf, args[0], args[1])
	case verb == "guarded" && len(args) >= 2:
		ok = guardedTree(stdout, stderr, root, conf, args[0], args[1:])
	case verb == "handoff-pending" && len(args) <= 2:
		ok = handoffPendingTree(stdout, stderr, args[0], args[1:])
	default:
		return 0, false
	}
	if ok {
		return config.ExitOK, true
	}
	return config.ExitError, true
}

func identOK(stderr io.Writer, verb, value string) bool {
	if ids.IsValidID(value) && !strings.Contains(value, "..") {
		return true
	}
	fmt.Fprintf(stderr, "ccnavi: %s の名前 %s は有効な識別子の形式ではありません。\n", verb, pyfmt.Repr(value))
	return false
}

func treeOK(stderr io.Writer, verb, path string) bool {
	if path != "" && ospath.IsDir(path) {
		return true
	}
	fmt.Fprintf(stderr, "ccnavi: %s のツリー %s が見つかりません。\n", verb, pyfmt.Repr(path))
	return false
}

// treeOf は sh が渡したツリーのパスを、名前を付けたワークツリーとして読む。登録されていないディレクトリでも、そのパスの
// 置き場を見る。プロジェクトは、ワークスペースかプロジェクトのワークツリーなら元リポジトリから決める。
func treeOf(root string, conf *config.Settings, path, name string) tree.Tree {
	full := ospath.Abspath(path)
	owners := append([]tree.Tree{tree.MainTree(root)}, tree.Projects(projectsDir(root, conf))...)
	owner, _ := tree.OwnerOf(full, owners)
	return tree.Tree{Name: name, Root: full, Project: owner.Project, Kind: tree.KindWorktree}
}

func projectsDir(root string, conf *config.Settings) string {
	if conf.Projects == "" || ospath.IsAbs(conf.Projects) {
		return conf.Projects
	}
	return ospath.Join(root, conf.Projects)
}

func underWorktrees(root, path string) bool {
	base := pathcase.Fold(ospath.Normpath(ospath.Join(root, tree.WorktreesDir)))
	full := pathcase.Fold(ospath.Normpath(ospath.Abspath(path)))
	return strings.HasPrefix(full, base+ospath.Sep)
}

func writeLines(w io.Writer, lines ...string) {
	fmt.Fprint(w, strings.Join(lines, "\n")+"\n")
}

func orNone(s string) string {
	if s == "" {
		return none
	}
	return s
}

// parentTree は `tree parent`。頭に続けて parent・approved・requested・archived・started・mr の 6 行。
func parentTree(stdout, stderr io.Writer, root string, conf *config.Settings, path, name string) bool {
	if !identOK(stderr, "tree parent", name) || !treeOK(stderr, "tree parent", path) {
		return false
	}
	work := treeOf(root, conf, path, name)
	held := holdsParent(conf, work)
	approved, mr, started := approvedParent(conf, work)
	// 手元の退避を見るのは `.claude/worktrees/<名前>` の形のときだけ（退避のリポジトリはワークツリーの元リポジトリで
	// 決まる）。
	archived := no
	if underWorktrees(root, path) {
		if copy, ok := archive.ArchivedFields(root, work.Project, name); ok && copy.Ticket == name && copy.Parent == "" {
			archived, held = yes, yes
			if mr == "" {
				mr = archivedMR(root, work.Project, name)
			}
		}
	}
	writeLines(stdout, head,
		"parent "+string(held),
		"approved "+string(approved),
		"requested "+string(requested(conf, work)),
		"archived "+string(archived),
		"started "+string(started),
		"mr "+orNone(mr))
	return true
}

// boundTree は `tree bound`。頭に続けて bound yes|no、結び付くなら why <理由>。
func boundTree(stdout, stderr io.Writer, root string, conf *config.Settings, path, name string) bool {
	if !identOK(stderr, "tree bound", name) || !treeOK(stderr, "tree bound", path) {
		return false
	}
	why := integrate.BoundReason(conf, root, treeOf(root, conf, path, name))
	lines := []string{head, "bound " + string(yesNo(why != ""))}
	if why != "" {
		lines = append(lines, "why "+why)
	}
	writeLines(stdout, lines...)
	return true
}

// leftoverTree は `tree leftover`。頭に続けて leftover・proposal・doing・mr・stage。stage は親の段階で、doing/ に在れば
// doing（改版の提案が todo/ にあっても）、なければ review/ なら review、todo/ なら todo、どれも無ければ "-"。
// 親のワークツリーでなければ（読めないときも）leftover no で、案内を出さない向きに倒す。
func leftoverTree(stdout, stderr io.Writer, root string, conf *config.Settings, path, name string) bool {
	if !identOK(stderr, "tree leftover", name) || !treeOK(stderr, "tree leftover", path) {
		return false
	}
	work := treeOf(root, conf, path, name)
	approvedDir := conf.ApprovedDir(work.Root)
	proposal, mr := no, ""
	found := map[place.State]bool{}
	for _, c := range []struct {
		stage place.State
		held  bool
		path  string
	}{
		{place.Doing, false, approval.CopyPath(approvedDir, name)},
		{place.Todo, true, ospath.Join(place.Dir(work.Root, conf.Tickets, place.Todo), name+mdExt)},
		{place.Review, true, ospath.Join(place.Dir(work.Root, conf.Tickets, place.Review), name+mdExt)},
	} {
		front := frontOf(c.path)
		if front == nil || pathcase.Fold(ospath.Strip(front.Text("ticket"))) != pathcase.Fold(name) ||
			pyfmt.Truthy(front.Get("parent")) {
			continue
		}
		found[c.stage] = true
		if c.held {
			proposal = yes
		}
		if mr == "" {
			mr = mrOf(front)
		}
	}
	stage := none
	for _, s := range []place.State{place.Doing, place.Review, place.Todo} {
		if found[s] {
			stage = string(s)
			break
		}
	}
	doing := 0
	if proposal == yes || mr != "" || holdsParent(conf, work) == yes {
		doing = childrenInDoing(approvedDir, name)
	}
	writeLines(stdout, head,
		"leftover "+string(yesNo(proposal == yes || mr != "" || doing > 0)),
		"proposal "+string(proposal),
		"doing "+strconv.Itoa(doing),
		"mr "+orNone(mr),
		"stage "+stage)
	return true
}

// childrenInDoing は doing/ にある、この親の子（parent: が親の識別子）の数。読めないファイルは数えない。
func childrenInDoing(approvedDir, parent string) int {
	directory := ospath.Join(approvedDir, string(place.Doing))
	names, err := ospath.DiskListdir(directory)
	if err != nil {
		return 0
	}
	count := 0
	for _, entry := range names {
		if !strings.HasSuffix(entry, mdExt) {
			continue
		}
		front := frontOf(ospath.Join(directory, entry))
		if front != nil && pathcase.Fold(ospath.Strip(front.Text("parent"))) == pathcase.Fold(parent) {
			count++
		}
	}
	return count
}

// baseBoundTree は `tree base-bound`。頭に続けて bound yes|no|unknown、yes なら why <理由>。
func baseBoundTree(stdout, stderr io.Writer, root string, conf *config.Settings, path, name string) bool {
	if name == "" || strings.ContainsAny(name, "\n\x00") {
		fmt.Fprintf(stderr, "ccnavi: tree base-bound のブランチ %s は有効な名前ではありません。\n", pyfmt.Repr(name))
		return false
	}
	if !treeOK(stderr, "tree base-bound", path) {
		return false
	}
	bound, why := branch.BaseBound(conf, root, path, name)
	lines := []string{head, "bound " + string(bound)}
	if why != "" {
		lines = append(lines, "why "+why)
	}
	writeLines(stdout, lines...)
	return true
}

// guardedTree は `tree guarded`。頭に続けて guarded yes|no、守る対象に当たるパスごとに path <パス>。パスはチェックアウト
// からの相対で、自己防衛が守る対象と、行き着く先まで解いたパスを大文字小文字を畳んで比べる。
func guardedTree(stdout, stderr io.Writer, root string, conf *config.Settings, path string, rels []string) bool {
	if !treeOK(stderr, "tree guarded", path) {
		return false
	}
	guarded := map[string]bool{}
	for _, t := range selfguard.Targets(root, conf.Rules, conf.Bin, ruleload.LayerFiles(conf, root), conf.Projects) {
		guarded[pathcase.Fold(ospath.Realpath(t.Path))] = true
	}
	var hits []string
	for _, rel := range rels {
		if rel != "" && guarded[pathcase.Fold(ospath.Realpath(ospath.Join(path, filepath.FromSlash(rel))))] {
			hits = append(hits, rel)
		}
	}
	lines := []string{head, "guarded " + string(yesNo(len(hits) > 0))}
	for _, rel := range hits {
		lines = append(lines, "path "+rel)
	}
	writeLines(stdout, lines...)
	return true
}

// handoffPendingTree は `tree handoff-pending`。頭に続けて pending <件数> と、取り込んでいない下書きごとに path <パス>。
// 起点（start の 1 つめ）があれば、元があり中身が違う写しごとに foreign <パス>、元の無い下書きごとに note <パス>。
func handoffPendingTree(stdout, stderr io.Writer, path string, start []string) bool {
	if !treeOK(stderr, "tree handoff-pending", path) {
		return false
	}
	root := ospath.Abspath(path)
	left := handoff.Pending(root)
	var foreign, notes []string
	if len(start) > 0 {
		var ok bool
		if foreign, notes, ok = handoff.Survey(root, start[0]); !ok {
			fmt.Fprintf(stderr, "ccnavi: tree handoff-pending の起点 %s の木を読めません。\n", pyfmt.Repr(start[0]))
			return false
		}
	}
	lines := []string{head, "pending " + strconv.Itoa(len(left))}
	for _, group := range []struct {
		key   string
		paths []string
	}{{"path", left}, {"foreign", foreign}, {"note", notes}} {
		for _, rel := range group.paths {
			lines = append(lines, group.key+" "+rel)
		}
	}
	writeLines(stdout, lines...)
	return true
}

// frontOf はチケットの frontmatter。無い・読めなければ nil（チケットとしての検査は掛けない）。
func frontOf(path string) *yaml.Map {
	text, ok := fsio.ReadText(path, fsio.Strict)
	if !ok {
		return nil
	}
	return frontOfText(text)
}

func frontOfText(text string) *yaml.Map {
	front, err := ticket.FrontMapping(text)
	if err != nil {
		return nil
	}
	return front
}

// mrOf は親チケットの mr: 欄。正の整数だけ数える（無い・形が違うなら空）。
func mrOf(front *yaml.Map) string {
	value := front.Get("mr")
	if _, isBool := value.(bool); isBool || value == nil {
		return ""
	}
	text := ospath.Strip(pyfmt.Str(value))
	if !pyfmt.IsDigit(text) {
		return ""
	}
	if n, err := pyfmt.ParseBigInt(text, 10); err != nil || n.Sign() <= 0 {
		return ""
	}
	return text
}

// isParentOf は frontmatter が name の親チケット（ticket: が name で parent: が無い）か。
func isParentOf(front *yaml.Map, name string) bool {
	return ospath.Strip(front.Text("ticket")) == name && !pyfmt.Truthy(front.Get("parent"))
}

// holdsParent はそのツリーの置き場に ticket: <名前> の親の承認済みチケットか提案があるか。チケットとしての検査（範囲の
// 欄など）は掛けず、frontmatter の ticket と parent だけを見る。
func holdsParent(conf *config.Settings, work tree.Tree) answer {
	approvedDir := conf.ApprovedDir(work.Root)
	paths := []string{
		approval.CopyPath(approvedDir, work.Name),
		approval.ClosedPath(approvedDir, work.Name),
		ospath.Join(place.Dir(work.Root, conf.Tickets, place.Todo), work.Name+mdExt),
		ospath.Join(place.Dir(work.Root, conf.Tickets, place.Review), work.Name+mdExt),
	}
	held, _, _ := parentIn(paths, work.Name)
	return held
}

// approvedParent は承認済みの置き場（doing/・done/）に親チケットが在るかと、その mr: 欄、着手済み（started_at が
// 空でない。start は push してから完了するので、着手済みなら一度は push した）か。提案は数えない（push のゲートが
// 見るのは承認された親だけ）。
func approvedParent(conf *config.Settings, work tree.Tree) (answer, string, answer) {
	approvedDir := conf.ApprovedDir(work.Root)
	return parentIn([]string{approval.CopyPath(approvedDir, work.Name), approval.ClosedPath(approvedDir, work.Name)}, work.Name)
}

// parentIn は paths のうち最初に見つかった name の親チケットについて (yes, mr, started)。無ければ no、在るのに
// 読めないものがあれば unreadable。
func parentIn(paths []string, name string) (answer, string, answer) {
	broken := false
	for _, path := range paths {
		if !ospath.IsFile(path) {
			continue
		}
		front := frontOf(path)
		if front == nil {
			broken = true
			continue
		}
		if isParentOf(front, name) {
			return yes, mrOf(front), yesNo(ospath.Strip(front.Text("started_at")) != "")
		}
	}
	if broken {
		return unreadable, "", no
	}
	return no, "", no
}

// requested は approved/phases/<名前>/ に <N>.requested が 1 つ以上あるか。
func requested(conf *config.Settings, work tree.Tree) answer {
	names, err := ospath.DiskListdir(ospath.Join(conf.ApprovedDir(work.Root), place.PhasesDir, work.Name))
	if err != nil {
		return no
	}
	return yesNo(slices.ContainsFunc(names, requestedMark.MatchString))
}

// archivedMR は手元の退避の親チケットの mr: 欄。無い・読めないなら空。大文字小文字だけが違う識別子の退避も同じものと
// して引く。
func archivedMR(root, project, ident string) string {
	name, ok := archive.ArchivedName(root, project, ident)
	if !ok {
		return ""
	}
	data, ok := archive.ArchivedBytes(root, project, string(place.Done)+"/"+name+mdExt)
	if !ok {
		return ""
	}
	text, err := fsio.Decode(data, fsio.Strict)
	if err != nil {
		return ""
	}
	if front := frontOfText(text); front != nil {
		return mrOf(front)
	}
	return ""
}
