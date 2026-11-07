package retire

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"maps"
	"math/big"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/lifecycle/worktrees"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
)

// stateDir は state の下の片付けの記録の置き場。
const stateDir = "retire"

// kind は片付ける子の種類（合流したか取り消したか）。記録の JSON の値。
type kind string

const (
	merged    kind = "merged"
	cancelled kind = "cancelled"
)

// 記録の JSON のキー。
const (
	keyParent   = "parent"
	keyProject  = "project"
	keyBranch   = "branch"
	keyChildren = "children"
	keyMR       = "mr"
	keyTicket   = "ticket"
	keyKind     = "kind"
	keyMerged   = "merged_sha"
)

// child は片付ける子 1 つ。
type child struct {
	ticket    string
	kind      kind
	mergedSHA string
	project   string
}

// same は名前を、大文字小文字（と NFC/NFD）を区別せずに比べる。
func same(a, b string) bool { return pathcase.Fold(pyfmt.NFC(a)) == pathcase.Fold(pyfmt.NFC(b)) }

func recordDir(conf *config.Settings) string {
	if conf.State == "" {
		return ""
	}
	return ospath.Join(conf.State, stateDir)
}

// recordPath は親の片付けの記録の場所。大文字小文字だけが違う名前は同じ記録なので、在ればファイルの実際の表記で
// 返す。state の置き場が空なら空。
func recordPath(conf *config.Settings, parent string) string {
	base := recordDir(conf)
	if base == "" {
		return ""
	}
	want := parent + fsio.JSONExt
	existing, _ := ospath.DiskListdir(base)
	for _, entry := range existing {
		if same(entry, want) {
			return ospath.Join(base, entry)
		}
	}
	return ospath.Join(base, want)
}

func repoDir(conf *config.Settings, root, project string) string {
	if project == "" {
		return root
	}
	projects := ""
	if conf.Projects != "" {
		projects = ospath.Join(root, conf.Projects)
	}
	if p := tree.ProjectRoot(projects, project); p != "" {
		return p
	}
	return root
}

func closedChildren(conf *config.Settings, root, parent string) []child {
	var out []child
	for _, t := range worktrees.TidyTargets(conf, root, parent, 0) {
		k := merged
		if t.CancelledAt != "" {
			k = cancelled
		}
		out = append(out, child{t.ID, k, t.MergedSHA, t.Project})
	}
	return out
}

// heldRecord は前の回の記録と、読めなかった理由。無いのは普通なので空の記録と空の理由。あるのに読めない（壊れて
// いる・マップでない）ものは、上書きすると前の回の子の一覧と MR の番号を失うので、呼び手は書かずに止める。
func heldRecord(path string) (*pyjson.Object, string) {
	data, err := fsio.ReadJSON(path)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return &pyjson.Object{}, ""
		}
		return &pyjson.Object{}, err.Error()
	}
	obj, ok := data.(*pyjson.Object)
	if !ok {
		return &pyjson.Object{}, "JSON のマップではない"
	}
	if v, has := obj.Get(keyChildren); has {
		if _, list := v.([]any); !list {
			return &pyjson.Object{}, "children が配列ではない"
		}
	}
	return obj, ""
}

func heldEmpty() *pyjson.Object { return &pyjson.Object{} }

// mrText は MR の番号。正の整数だけ数える（無い・形が違うなら空）。
func mrText(value any) string {
	if _, isBool := value.(bool); isBool || value == nil {
		return ""
	}
	text := ospath.Strip(pyfmt.Str(value))
	if text == "" || strings.Trim(text, "0123456789") != "" {
		return ""
	}
	n, ok := new(big.Int).SetString(text, 10)
	if !ok || n.Sign() <= 0 {
		return ""
	}
	return text
}

// textOf は Python の str(value or "")。
func textOf(value any) string {
	if !pyfmt.Truthy(value) {
		return ""
	}
	return pyfmt.Str(value)
}

// unreadableClosed は親のワークツリーの閉じた承認済みチケットのうち読めないもの（理由の文）。読めない閉じた子は
// 子の一覧に入らず、そのまま退避へ進むと片付けの対象から知らないうちに外れる。
func unreadableClosed(conf *config.Settings, root, treeRoot string) []string {
	if treeRoot == "" {
		_, problems := approval.ScanAll(conf, root, true)
		return problems
	}
	var out []string
	done := string(place.Done)
	for _, u := range approval.UnreadableCopies(conf, treeRoot) {
		if u.Path == done || ospath.Basename(ospath.Dirname(u.Path)) == done || ospath.Basename(u.Path) == done {
			out = append(out, u.Message)
		}
	}
	return out
}

// Note は ready が子のチケットを退避へ移す前に片付けの記録を書く。答えは書けなかった理由（書けたら空）。
//
// 記録は `retire/<親>.json`（親・リポジトリ・ブランチ・MR の番号・閉じた子の一覧）。前の回（途中で止まった）の
// 一覧には足し、同じ子は今回の値で置き換える。mr が無ければ前の回の値を保つ。書かずに理由を返すのは、記録の
// 置き場が無いとき、前の回の記録が壊れているとき、読めない閉じたチケットがあるとき。呼び手（ready）は理由が
// あれば退避へ進まずに止める。mr は書かれたままの値（Python の mr: object。nil は無し）。
func Note(conf *config.Settings, root, parent, project, treeRoot string, mr any) string {
	path := recordPath(conf, parent)
	if path == "" {
		return "state の置き場が空なので、片付けの記録を書けない"
	}
	held, broken := heldRecord(path)
	if broken != "" {
		return "既存の片付けの記録 " + path + " を読めない（" + broken + "）。上書きしないので、直すか消してから打ち直す"
	}
	if unread := unreadableClosed(conf, root, treeRoot); len(unread) > 0 {
		return "読めない閉じたチケットがあり、片付ける子の一覧に入らない（" + strings.Join(unread[:min(len(unread), 3)], "; ") +
			"）。直してから打ち直す"
	}
	children := map[string]any{}
	if list, ok := held.Value(keyChildren).([]any); ok {
		for _, c := range list {
			if obj, isObj := c.(*pyjson.Object); isObj {
				if id, has := obj.Get(keyTicket); has {
					if name, isText := id.(string); isText {
						children[name] = obj
					}
				}
			}
		}
	}
	for _, c := range closedChildren(conf, root, parent) {
		children[c.ticket] = pyjson.NewObject(keyTicket, c.ticket, keyKind, string(c.kind), keyMerged, c.mergedSHA, keyProject, c.project)
	}
	var branch any = ""
	if treeRoot != "" {
		if name, ok := tree.BranchOf(treeRoot); ok {
			branch = name
		}
	}
	if !pyfmt.Truthy(branch) {
		if v, has := held.Get(keyBranch); has {
			branch = v
		} else {
			branch = ""
		}
	}
	list := []any{}
	for _, k := range slices.Sorted(maps.Keys(children)) {
		list = append(list, children[k])
	}
	data := pyjson.NewObject(keyParent, parent, keyProject, project, keyBranch, branch, keyChildren, list)
	kept := mrText(mr)
	if kept == "" {
		kept = mrText(held.Value(keyMR))
	}
	if kept != "" {
		n, _ := new(big.Int).SetString(kept, 10)
		if n.IsInt64() {
			data.Set(keyMR, int(n.Int64()))
		} else {
			data.Set(keyMR, n)
		}
	}
	format := fsio.JSONFormat{Indent: fsio.JSONIndentOne, FinalNewline: true, Newline: fsio.LF, How: fsio.Atomic}
	if failed := fsio.WriteJSONAs(path, data, format); failed != "" {
		return "片付けの記録を書けない（" + path + ": " + failed + "）"
	}
	return ""
}

// Entry は `sync retired` の 1 件。Incomplete が空でなければ列挙できない理由（ほかの欄は空）。
type Entry struct {
	Parent     string
	Repo       string
	MR         string
	Branch     string
	Incomplete string
}

// Entries は片付けの記録の列挙。読むだけで、git にも触れない。数えるのはファイル名の幹と JSON の parent が一致
// するものだけ（記録を手で置き換えて別の親を指させない）。読めない・mr が無い・ブランチが空白を含むものは
// Incomplete で理由を言う。
func Entries(conf *config.Settings) []Entry {
	directory := recordDir(conf)
	if directory == "" {
		return nil
	}
	names, err := ospath.DiskListdir(directory)
	if err != nil {
		return nil
	}
	var out []Entry
	for _, name := range names {
		stem, ok := strings.CutSuffix(name, fsio.JSONExt)
		if !ok || strings.Contains(name, ".tmp.") {
			continue
		}
		path := ospath.Join(directory, name)
		if ospath.IsLink(path) || !ospath.IsFile(path) || !ids.IsValidID(stem) {
			continue
		}
		data, err := fsio.ReadJSON(path)
		obj, isObj := data.(*pyjson.Object)
		if err != nil || !isObj {
			reason := "JSON のマップではない"
			if err != nil {
				reason = err.Error()
			}
			out = append(out, Entry{Parent: stem, Incomplete: "記録を読めない（" + reason + "）"})
			continue
		}
		if !same(textOf(obj.Value(keyParent)), stem) {
			continue
		}
		if v, has := obj.Get(keyChildren); has {
			if _, list := v.([]any); !list {
				out = append(out, Entry{Parent: stem, Incomplete: "記録の children が配列ではない"})
				continue
			}
		}
		project, branch := textOf(obj.Value(keyProject)), textOf(obj.Value(keyBranch))
		switch mr := mrText(obj.Value(keyMR)); {
		case mr == "":
			out = append(out, Entry{Parent: stem, Incomplete: "記録に MR の番号（mr）が無い"})
		case spaced(project) || spaced(branch):
			out = append(out, Entry{Parent: stem, Incomplete: "記録のリポジトリかブランチが空白を含む"})
		default:
			out = append(out, Entry{Parent: stem, Repo: tree.RepoKey(project), MR: mr, Branch: branch})
		}
	}
	return out
}

func spaced(text string) bool { return strings.ContainsFunc(text, ospath.IsSpace) }

// RunAbandon は `worktree retire --abandon <親>`。放棄した親（MR をマージせずに閉じた親）の記録を消す。消すと
// sync はその親を片付け待ちとして知らせなくなる。親のワークツリーが残っている間は、まだ放棄していないとみなして
// 断る。ユーザがターミナルで打つ。
func RunAbandon(stdout, stderr io.Writer, conf *config.Settings, root, parent string) int {
	path := recordPath(conf, parent)
	if path == "" {
		fmt.Fprint(stderr, "ccnavi: state の置き場が空なので、片付けの記録を消せません\n")
		return 1
	}
	if where := tree.WorktreePath(root, parent); ospath.DiskLexists(where) {
		fmt.Fprintf(stderr, "ccnavi: 親 %s のワークツリー（%s）が残っているので、記録は消しません。"+
			"放棄するなら、先にワークツリーを片付けてください\n", parent, where)
		return 1
	}
	if !ospath.DiskLexists(path) {
		fmt.Fprintf(stderr, "ccnavi: 親 %s の片付けの記録はありません\n", parent)
		return 1
	}
	if failed := fsio.Unlink(path); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: 片付けの記録を消せませんでした（%s: %s）\n", path, failed)
		return 1
	}
	fmt.Fprintf(stdout, "親 %s の片付けの記録を消しました。ブランチは残っています\n", parent)
	return 0
}

// childrenFrom は記録の子の一覧のうち、識別子と種類が読めるもの。
func childrenFrom(record *pyjson.Object) []child {
	var out []child
	list, _ := record.Value(keyChildren).([]any)
	for _, c := range list {
		obj, ok := c.(*pyjson.Object)
		if !ok || !pyfmt.Truthy(obj.Value(keyTicket)) {
			continue
		}
		k, _ := obj.Value(keyKind).(string)
		if kind(k) != merged && kind(k) != cancelled {
			continue
		}
		out = append(out, child{pyfmt.Str(obj.Value(keyTicket)), kind(k), textOf(obj.Value(keyMerged)), textOf(obj.Value(keyProject))})
	}
	return out
}
