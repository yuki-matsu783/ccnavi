package archive

import (
	"bytes"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// flowSuffixes は flows/ の子のフローの拡張子。
var flowSuffixes = []string{".yml", ".yaml"}

// tempName は書きかけの一時ファイル（fsio の `.<名前>.<一意>.part`）。移さない。Python の search の `$` は末尾の
// 改行 1 つの前でも当たるので `\n?\z` にする。
var tempName = regexp.MustCompile(`\A\..*\.part(\.[^/]*)?\n?\z`)

// readyPlaces は ready のマーカーに載せてよい、承認済みの領域の下の置き場。
var readyPlaces = []string{string(place.Done) + "/", place.PhasesDir + "/", place.FlowsDir + "/"}

// archiveParts は退避の置き場のワークスペースルートからの名前の並び。
func archiveParts(more ...string) []string {
	return append(strings.Split(place.ArchiveDir, ospath.Sep), more...)
}

func top(root string) string { return ospath.Join(root, place.ArchiveDir) }

// BaseDir はそのリポジトリの退避の置き場（承認済みの領域と同じ構成を持つ）。
func BaseDir(root, project string) string { return ospath.Join(top(root), tree.RepoKey(project)) }

// linkState はルートの下の parts の道にリンクがあるか（途中が無ければ Missing）。
func linkState(root string, parts []string) pathmatch.LinkState {
	state, _ := pathmatch.Scan(root, parts, true)
	return state
}

func regular(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.Mode().IsRegular()
}

// repoNames は退避のあるリポジトリの名前（self とプロジェクトの名前）。リンクは除く。
func repoNames(root string) []string {
	if root == "" || linkState(root, archiveParts()) != pathmatch.Clean {
		return nil
	}
	names, err := ospath.DiskListdir(top(root))
	if err != nil {
		return nil
	}
	var out []string
	for _, name := range names {
		if info, err := os.Lstat(ospath.Join(top(root), name)); err == nil && info.IsDir() {
			out = append(out, name)
		}
	}
	return out
}

// doneDir は退避の done/。途中にリンクがあるか無ければ空（読まない）。
func doneDir(root, repo string) string {
	parts := archiveParts(repo, string(place.Done))
	if linkState(root, parts) != pathmatch.Clean {
		return ""
	}
	return ospath.Join(root, parts...)
}

// regularMD はディレクトリの *.md のふつうのファイルの識別子（拡張子を除いた名前）。リンクは除く。
func regularMD(directory string) []string {
	if directory == "" {
		return nil
	}
	names, err := ospath.DiskListdir(directory)
	if err != nil {
		return nil
	}
	var out []string
	for _, n := range names {
		if stem, ok := strings.CutSuffix(n, place.MdExt); ok && regular(ospath.Join(directory, n)) {
			out = append(out, stem)
		}
	}
	return out
}

// names はディレクトリのふつうのファイルの名前（リンクは除く）。
func names(directory string) []string {
	all, err := ospath.DiskListdir(directory)
	if err != nil {
		return nil
	}
	var out []string
	for _, n := range all {
		if regular(ospath.Join(directory, n)) {
			out = append(out, n)
		}
	}
	return out
}

// IDs はそのリポジトリの退避の done/ にある識別子（ファイル名から）。
func IDs(root, project string) map[string]bool {
	out := map[string]bool{}
	for _, ident := range regularMD(doneDir(root, tree.RepoKey(project))) {
		out[ident] = true
	}
	return out
}

// IDsAny は全リポジトリの退避の done/ にある識別子。
func IDsAny(root string) map[string]bool {
	out := map[string]bool{}
	for _, repo := range repoNames(root) {
		if directory := doneDir(root, repo); directory != "" {
			for _, ident := range regularMD(directory) {
				out[ident] = true
			}
		}
	}
	return out
}

// FindAny は全リポジトリの退避の done/<識別子>.md を読む。読めないものは除く。
func FindAny(root, ident string) []*ticket.Ticket {
	if !ids.IsValidID(ident) {
		return nil
	}
	var out []*ticket.Ticket
	for _, repo := range repoNames(root) {
		directory := doneDir(root, repo)
		if directory == "" {
			continue
		}
		path := ospath.Join(directory, ident+place.MdExt)
		if !regular(path) {
			continue
		}
		if t := load(path, ident, repo); t != nil {
			out = append(out, t)
		}
	}
	return out
}

// ClosedTickets は退避にある閉じたチケットの全部（ボードの表示用）。
func ClosedTickets(root string) []*ticket.Ticket {
	var out []*ticket.Ticket
	for _, repo := range repoNames(root) {
		directory := doneDir(root, repo)
		for _, ident := range regularMD(directory) {
			if t := load(ospath.Join(directory, ident+place.MdExt), ident, repo); t != nil {
				out = append(out, t)
			}
		}
	}
	return out
}

// load は退避のチケットを読む。状態は done（取り消しの欄があれば cancelled）で、ツリーは持たない。
func load(path, ident, repo string) *ticket.Ticket {
	t, _ := ticket.Load(path)
	if t == nil || t.ID != ident {
		return nil
	}
	t.State = place.Done
	if t.CancelledAt != "" {
		t.State = place.Cancelled
	}
	t.Project = tree.ProjectOfKey(repo)
	t.Tree, t.TreeRoot = "", ""
	return t
}

// ArchivedParent は退避にある親（識別子が同じで、子でない）。無ければ nil。
func ArchivedParent(root, project, ident string) *ticket.Ticket {
	repo := tree.RepoKey(project)
	directory := doneDir(root, repo)
	if directory == "" {
		return nil
	}
	path := ospath.Join(directory, ident+place.MdExt)
	if !regular(path) {
		return nil
	}
	if t := load(path, ident, repo); t != nil && t.Parent == "" {
		return t
	}
	return nil
}

func fieldsOf(data []byte, ok bool) (ticket.DoneCopy, bool) {
	if !ok || !utf8.Valid(data) {
		return ticket.DoneCopy{}, false
	}
	return ticket.CopyFields(string(data))
}

// ArchivedName は退避の done/ にある、その識別子のファイルの実際の綴り（幹）と真。大文字小文字だけが違う識別子も
// 同じものとして引く。
func ArchivedName(root, project, ident string) (string, bool) {
	if !ids.IsValidID(ident) {
		return "", false
	}
	if _, ok := ArchivedBytes(root, project, string(place.Done)+"/"+ident+place.MdExt); ok {
		return ident, true
	}
	for _, i := range regularMD(doneDir(root, tree.RepoKey(project))) {
		if ids.SameID(i, ident) {
			return i, true
		}
	}
	return "", false
}

// ArchivedFields は退避の done/<識別子>.md の識別子と親。無い・読めない・リンクなら偽。大文字小文字だけが違う
// 識別子も同じものとして引く（区別しないファイルシステムと区別するものとで答えを揃える）。
func ArchivedFields(root, project, ident string) (ticket.DoneCopy, bool) {
	name, ok := ArchivedName(root, project, ident)
	if !ok {
		return ticket.DoneCopy{}, false
	}
	copy, ok := fieldsOf(ArchivedBytes(root, project, string(place.Done)+"/"+name+place.MdExt))
	if !ok || !ids.SameID(copy.Ticket, name) {
		return ticket.DoneCopy{}, false
	}
	return copy, true
}

// Plan は移すもの。Files は承認済みの領域からの相対（"/" 区切り）で、マーカー・フロー・子のチケット・親の
// チケットの順に並ぶ。止まったときに done/<親>.md がツリーに残り、次の ready が同じ親を拾い直せるようにするため。
type Plan struct {
	Parents []string
	Tickets []string
	Files   []string
}

// doneCopies は承認済みの領域の done/ のふつうのファイルの、識別子 → 欄。読めない・名前と違うものは除く。
func doneCopies(approvedDir string) map[string]ticket.DoneCopy {
	directory := ospath.Join(approvedDir, string(place.Done))
	out := map[string]ticket.DoneCopy{}
	for _, ident := range regularMD(directory) {
		if copy, ok := fieldsOf(fsio.ReadBytes(ospath.Join(directory, ident+place.MdExt))); ok && copy.Ticket == ident {
			out[ident] = copy
		}
	}
	return out
}

// ClosedParents は承認済みの領域の done/ に在る親（parent: を持たないチケット）。
func ClosedParents(approvedDir string) []string {
	var out []string
	for ident, copy := range doneCopies(approvedDir) {
		if copy.Parent == "" {
			out = append(out, ident)
		}
	}
	slices.Sort(out)
	return out
}

// ArchivedParents は手元の退避の done/ に在る親。
func ArchivedParents(root, project string) []string {
	var out []string
	for _, ident := range regularMD(doneDir(root, tree.RepoKey(project))) {
		if copy, ok := ArchivedFields(root, project, ident); ok && copy.Parent == "" {
			out = append(out, ident)
		}
	}
	return out
}

func sortedKeys[V any](m map[string]V, keep func(string) bool) []string {
	var out []string
	for k := range m {
		if keep(k) {
			out = append(out, k)
		}
	}
	slices.Sort(out)
	return out
}

// PlanMove は閉じた親のリストから、移すファイルを決める。親として拾うのは done/ に parent: を持たないチケットとして
// 在るものと、root を渡したときは手元の退避に親として在るもの（前の回が途中で止まった残り。作業中に同じ識別子が
// 在れば別の開いた親子なので拾わない）。子は名前の形ではなく parent: 欄で親に結ぶ（rel-01 という親を rel の子と
// 取り違えない）。承認済みにも退避にも無い識別子（フローしか残っていない子）だけを子の形で結ぶ。
func PlanMove(approvedDir string, parents []string, root, project string) Plan {
	done := doneCopies(approvedDir)
	family := map[string]bool{}
	for _, p := range parents {
		here, ok := done[p]
		switch {
		case ok && here.Parent == "":
			family[p] = true
		case !ok && root != "" && !ospath.DiskLexists(ospath.Join(approvedDir, string(place.Doing), p+place.MdExt)):
			if held, ok := ArchivedFields(root, project, p); ok && held.Parent == "" {
				family[p] = true
			}
		}
	}
	children := map[string]bool{}
	for i, copy := range done {
		if family[copy.Parent] {
			children[i] = true
		}
	}
	if root != "" {
		for _, ident := range regularMD(doneDir(root, tree.RepoKey(project))) {
			if held, ok := ArchivedFields(root, project, ident); ok && family[held.Parent] {
				children[ident] = true
			}
		}
	}
	known := map[string]bool{}
	for i := range done {
		known[i] = true
	}
	for _, i := range regularMD(ospath.Join(approvedDir, string(place.Doing))) {
		known[i] = true
	}
	belongs := func(ident string) bool {
		if family[ident] || children[ident] {
			return true
		}
		if known[ident] {
			return false
		}
		if root != "" {
			if _, ok := ArchivedFields(root, project, ident); ok {
				return false
			}
		}
		c, ok := ids.ParseChild(ident)
		return ok && family[c.Parent]
	}

	out := Plan{Parents: sortedKeys(family, func(string) bool { return true })}
	for _, p := range out.Parents {
		out.Files = append(out.Files, walk(approvedDir, place.PhasesDir+"/"+p)...)
	}
	for _, name := range names(ospath.Join(approvedDir, place.FlowsDir)) {
		stem, ext := ospath.Splitext(name)
		if slices.Contains(flowSuffixes, ext) && !family[stem] && belongs(stem) {
			out.Files = append(out.Files, place.FlowsDir+"/"+name)
		}
	}
	out.Tickets = append(sortedKeys(done, func(i string) bool { return children[i] }),
		sortedKeys(done, func(i string) bool { return family[i] })...)
	for _, i := range out.Tickets {
		out.Files = append(out.Files, string(place.Done)+"/"+i+place.MdExt)
	}
	return out
}

// walk は承認済みの領域の下の rel の下のふつうのファイル（相対、"/" 区切り）。リンクは辿らない。os.walk と同じく
// 1 つのディレクトリのファイルを先に、下のディレクトリを後に、それぞれ名前の順で並べる。
func walk(approvedDir, rel string) []string {
	start := ospath.Join(approvedDir, filepath.FromSlash(rel))
	if ospath.IsLink(start) || !ospath.IsDir(start) {
		return nil
	}
	var out []string
	var visit func(dir, rel string)
	visit = func(dir, rel string) {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return
		}
		var dirs []string
		for _, e := range entries {
			full := ospath.Join(dir, e.Name())
			if e.IsDir() {
				dirs = append(dirs, e.Name())
				continue
			}
			if regular(full) && !tempName.MatchString(e.Name()) {
				out = append(out, rel+"/"+e.Name())
			}
		}
		for _, d := range dirs {
			visit(ospath.Join(dir, d), rel+"/"+d)
		}
	}
	visit(start, rel)
	return out
}

func destination(root, project, rel string) string {
	return ospath.Join(BaseDir(root, project), filepath.FromSlash(rel))
}

func badRel(rel string) bool {
	return slices.ContainsFunc(strings.Split(rel, "/"), func(p string) bool { return p == "" || p == "." || p == ".." })
}

// Holds は退避したコピー held が、ツリーの中身 data をコピーしたものか。改行を LF に揃えて比べる（autocrlf で
// ワークツリーだけ CRLF になったものも同じと読む）。退避に無ければ（heldOK が偽）偽。
func Holds(held []byte, heldOK bool, data []byte) bool {
	if !heldOK {
		return false
	}
	lf := func(b []byte) []byte { return bytes.ReplaceAll(b, []byte("\r\n"), []byte("\n")) }
	return bytes.Equal(lf(held), lf(data))
}

// ArchivedBytes は退避にあるファイルの中身と真。無い・読めない・途中（logs を含む）にリンクがあれば偽。
func ArchivedBytes(root, project, rel string) ([]byte, bool) {
	if root == "" || badRel(rel) {
		return nil, false
	}
	parts := archiveParts(append([]string{tree.RepoKey(project)}, strings.Split(rel, "/")...)...)
	if linkState(root, parts) != pathmatch.Clean {
		return nil, false
	}
	path := ospath.Join(root, parts...)
	if !regular(path) {
		return nil, false
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, false
	}
	return data, true
}

// ---- ready が退避したマーカー（どの親のワークツリーから、どのファイルを移したか）
//
// write-and-push の見分けと実行後チェックは、ready の流れで消したものだけを ccnavi の書き込みとして外す。
// 「退避に同じ中身がある」だけで外すと、ready を経ない削除も気づかないうちにコミットされる。

func treeKey(treeRoot string) string {
	if treeRoot == "" {
		return ""
	}
	return pathcase.Real(treeRoot)
}

// InReadyPlaces は承認済みの領域からの相対が、ready が移す 3 つの置き場の下か（".." などは受けない）。
func InReadyPlaces(rel string) bool {
	return slices.ContainsFunc(readyPlaces, func(p string) bool { return strings.HasPrefix(rel, p) }) && !badRel(rel)
}

// TreeHead はツリーの版（rev）の sha。読めなければ空（git はローカルの読み取りだけ）。
func TreeHead(treeRoot, rev string) string {
	if treeRoot == "" {
		return ""
	}
	done := git.Run(treeRoot, []string{"rev-parse", "--verify", "--quiet", rev + "^{commit}"}, git.Options{})
	if !done.OK() {
		return ""
	}
	return ospath.Strip(done.Out)
}

func readReady(root, project, name string) *pyjson.Object {
	held, ok := ArchivedBytes(root, project, place.ReadyDir+"/"+name)
	if !ok || !utf8.Valid(held) {
		return nil
	}
	v, err := pyjson.Loads(string(held))
	if err != nil {
		return nil
	}
	obj, _ := v.(*pyjson.Object)
	return obj
}

func readyStrings(v any) []string {
	list, _ := v.([]any)
	var out []string
	for _, f := range list {
		if s, ok := f.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// ReadyFiles はその回の ready がそのツリーから移したファイル（承認済みの領域からの相対）。マーカーが効くのは、
// 書いたときのツリーの先頭（head）といま比べている版が同じ間だけ（ready の削除がコミットされてツリーが進めば、
// 以後の削除には効かない）。head が空なら何も返さない。
func ReadyFiles(root, project, treeRoot, head string) map[string]bool {
	out := map[string]bool{}
	if root == "" || head == "" {
		return out
	}
	if linkState(root, archiveParts(tree.RepoKey(project), place.ReadyDir)) == pathmatch.Link {
		return out
	}
	key := treeKey(treeRoot)
	for _, name := range names(ospath.Join(BaseDir(root, project), place.ReadyDir)) {
		if !strings.HasSuffix(name, fsio.JSONExt) {
			continue
		}
		data := readReady(root, project, name)
		if data == nil || data.Value("tree") != key || data.Value("head") != head {
			continue
		}
		for _, f := range readyStrings(data.Value("files")) {
			if InReadyPlaces(f) {
				out[f] = true
			}
		}
	}
	return out
}

// ReadyStarted はそのツリーで、その親の ready が退避を始めたか（マーカー ready/<親>.json が在るか）。マーカーは
// 条件を確かめた後にだけ書くので、在れば条件は前の回で確かめてある。
func ReadyStarted(root, project, parent, treeRoot string) bool {
	data := readReady(root, project, parent+fsio.JSONExt)
	return data != nil && data.Value("tree") == treeKey(treeRoot)
}

// noteReady は ready のマーカーに、これから移すファイルとツリーの先頭を書く（消す前に書く）。同じ先頭の前の回
// （途中で止まった）の一覧には足し、先頭が進んでいれば今回の分で書き直す。
func noteReady(root, project, parent, treeRoot string, rels []string, head string) string {
	key := treeKey(treeRoot)
	merged := map[string]bool{}
	if data := readReady(root, project, parent+fsio.JSONExt); data != nil && data.Value("tree") == key && data.Value("head") == head {
		for _, f := range readyStrings(data.Value("files")) {
			merged[f] = true
		}
	}
	for _, r := range rels {
		if InReadyPlaces(r) {
			merged[r] = true
		}
	}
	files := []any{}
	for _, f := range sortedKeys(merged, func(string) bool { return true }) {
		files = append(files, f)
	}
	payload := pyjson.NewObject("parent", parent, "tree", key, "head", head, "files", files)
	body, err := pyjson.Dumps(payload, pyjson.Options{RawUnicode: true, Indent: 1})
	if err != nil {
		return err.Error()
	}
	return write(root, project, place.ReadyDir+"/"+parent+fsio.JSONExt, []byte(body))
}

// Move は todo のファイルを退避へ移し、移した相対パスと止まった理由（無ければ空）を返す。1 本ずつ、退避の側へ
// 一時ファイルから書いて読み戻して確かめ、それから元を消す（元の消去は fsio を通すので write-and-push の一覧に
// 載る）。行き先に同じ中身のコピーが在れば（前の回の残り）元だけ消す。違えば、チケット（done/）は閉じた記録を
// 書き換えないので止め、マーカー・フローは今の中身で置き換える。readyParent を渡せば、消す前に ready の
// マーカーへ移すファイルを足す。途中で止まっても、移した分は戻さない（元は git に残り、write-and-push が戻す）。
func Move(root, project, approvedDir string, todo Plan, readyParent, treeRoot string) ([]string, string) {
	base := BaseDir(root, project)
	if rel, ok := ospath.Relpath(base, root); ok && linkState(root, strings.Split(rel, ospath.Sep)) == pathmatch.Link {
		return nil, "退避の置き場（" + base + "）の途中にシンボリックリンクがある（辿らない）"
	}
	if readyParent != "" {
		head := TreeHead(treeRoot, "HEAD")
		if head == "" {
			return nil, "親のワークツリー（" + treeRoot + "）の先頭を読めない。何も移していない"
		}
		if failed := noteReady(root, project, readyParent, treeRoot, todo.Files, head); failed != "" {
			return nil, failed
		}
	}
	var moved []string
	for _, rel := range todo.Files {
		source := ospath.Join(approvedDir, filepath.FromSlash(rel))
		data, ok := fsio.ReadBytes(source)
		if !ok {
			return moved, source + " を読めない"
		}
		target := destination(root, project, rel)
		held, heldOK := ArchivedBytes(root, project, rel)
		if !heldOK && ospath.DiskLexists(target) {
			return moved, "退避先 " + target + " を読めない（ふつうのファイルでないかリンク）"
		}
		if !Holds(held, heldOK, data) {
			if heldOK && strings.HasPrefix(rel, string(place.Done)+"/") {
				return moved, "退避先 " + target + " に違う中身が既に在る。" +
					"クローズ済みのチケットを上書きしないので" +
					"止めた。ユーザが中身を確かめてください"
			}
			if failed := write(root, project, rel, data); failed != "" {
				return moved, failed
			}
		}
		if failed := fsio.Unlink(source); failed != "" {
			return moved, source + " を削除できません（" + failed + "）"
		}
		moved = append(moved, rel)
	}
	for _, p := range todo.Parents {
		pruneEmpty(ospath.Join(approvedDir, place.PhasesDir, p))
	}
	return moved, ""
}

// safeDir は行き先のディレクトリを、ルートから 1 段ずつ確かめながら作る。リンクがあれば理由を返す。
func safeDir(root, project, rel string) string {
	relParts := strings.Split(rel, "/")
	path := root
	for _, part := range archiveParts(append([]string{tree.RepoKey(project)}, relParts[:len(relParts)-1]...)...) {
		path = ospath.Join(path, part)
		info, err := os.Lstat(path)
		if errors.Is(err, fs.ErrNotExist) {
			if err := os.Mkdir(path, 0o777); err != nil && !errors.Is(err, fs.ErrExist) {
				return path + " を作れない（" + fsio.ErrorText(err) + "）"
			}
			info, err = os.Lstat(path)
		}
		if err != nil {
			return path + " を確かめられない（" + fsio.ErrorText(err) + "）"
		}
		if info.Mode()&fs.ModeSymlink != 0 {
			return "退避の置き場の途中（" + path + "）がシンボリックリンク（辿らない）"
		}
		if !info.IsDir() {
			return "退避の置き場の途中（" + path + "）がディレクトリでない"
		}
	}
	return ""
}

// write は退避の側の 1 本を書く。同じディレクトリの一時ファイルから置き換え、読み戻して確かめる。書けなければ
// 理由を返す（一時ファイルは消し、半端なファイルを行き先に残さない）。
func write(root, project, rel string, data []byte) string {
	if badRel(rel) {
		return "退避のパスが読めない（" + rel + "）"
	}
	if failed := safeDir(root, project, rel); failed != "" {
		return failed
	}
	target := destination(root, project, rel)
	if ospath.IsLink(target) {
		return "退避先 " + target + " がシンボリックリンク（辿らない）"
	}
	temp := ospath.Join(ospath.Dirname(target), "."+ospath.Basename(target)+"."+strconv.Itoa(os.Getpid())+".part")
	err := func() error {
		f, err := os.OpenFile(temp, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err != nil {
			return err
		}
		_, err = f.Write(data)
		if err == nil {
			err = f.Sync()
		}
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return err
		}
		return os.Rename(temp, target)
	}()
	if err != nil {
		_ = os.Remove(temp)
		return target + " に保存できませんでした（" + fsio.ErrorText(err) + "）"
	}
	if held, ok := ArchivedBytes(root, project, rel); !ok || !bytes.Equal(held, data) {
		return target + " を読み戻すと中身が違う（書けていない）"
	}
	return ""
}

// pruneEmpty は空になったディレクトリを下から消す（git は空のディレクトリを持たない）。
func pruneEmpty(directory string) {
	if ospath.IsLink(directory) || !ospath.IsDir(directory) {
		return
	}
	var dirs []string
	_ = filepath.WalkDir(directory, func(path string, d fs.DirEntry, err error) error {
		if err == nil && d.IsDir() {
			dirs = append(dirs, path)
		}
		return nil
	})
	sort.SliceStable(dirs, func(i, j int) bool { return len(dirs[i]) > len(dirs[j]) })
	for _, d := range dirs {
		_ = os.Remove(d)
	}
}
