package wap

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
)

// kind は sort の分け。
type kind string

const (
	kindB    kind = "b"    // ccnavi が書いたと内容で分かるもの（write-and-push がコミットする）
	kindC    kind = "c"    // ユーザの判断が書くもの（write-and-push はコミットせずに止める）
	kindD    kind = "d"    // 見分けられないもの（write-and-push は止める）
	kindKeep kind = "keep" // record-risk が書いた <子>.judge.json（その子の finish の write-and-push がコミットする）
	kindSkip kind = "skip" // 書きかけの一時ファイル（分けにも数えず、コミットの対象にもしない）
)

var (
	// temp は書きかけの一時ファイル（fsio の `.<名前>.<一意>.part.*`、フローの保存の `flows/.*.tmp`、
	// configsync の `*.ccnavi-sync`）。Python の search の `$` は末尾の改行 1 つの前でも当たるので `\n?\z` にする。
	temp = regexp.MustCompile(`(?:(?:\A|/)\.[^/]*\.part(?:\.[^/]*)?|(?:\A|/)flows/\.[^/]*\.tmp|\.ccnavi-sync)\n?\z`)
	// number はマーカーのフェーズ番号（ASCII の数字だけ。全角の数字は通さない）。
	number = regexp.MustCompile(`\A[0-9]+\z`)
	// hookFields は hook の告知が置くマーカーの欄。これ以外の欄があれば (b) にしない。
	hookFields = map[string]bool{"at": true, "review": true, "source": true, "deferred_to": true, "tickets": true}
	// humanMarkNames はユーザの判断が書く親ごとのマーカー（受け入れ台帳は reviewed と一緒に書かれる）。
	humanMarkNames = map[string]bool{
		string(marks.CloseEarly) + fsio.JSONExt: true,
		string(marks.Ledger) + fsio.JSONExt:     true,
	}
)

// flowsDir は承認済みの領域の下の、子のフローの置き場。
const flowsDir = place.FlowsDir

// entry は分けた 1 件。why は分けの理由を言えるときだけ。
type entry struct {
	class kind
	path  string
	why   string
}

// Sort は `ccnavi write-and-push sort <親> [<版>]`。置き場の変更を 1 行 1 つに分ける。終了コードを返す。
func Sort(stdout, stderr io.Writer, conf *config.Settings, root, parent, since string) int {
	where, relative := relativePlaces(conf)
	st := standing.NewFamilies(conf, root).StandingAny(parent)
	if !relative || st.Home == nil {
		fmt.Fprintf(stderr, "ccnavi: write-and-push sort: 親子のチケット %s の親のワークツリーが決まらない\n", parent)
		return config.ExitError
	}
	treeRoot := st.Home.Root
	changed, why := changedPaths(treeRoot, where, since)
	if why != "" {
		fmt.Fprintf(stderr, "ccnavi: write-and-push sort: %s\n", why)
		return config.ExitError
	}
	fmt.Fprintln(stdout, Head)
	at := &archiveAt{root: root, project: tree.ProjectOfKey(st.Repo)}
	for _, e := range classify(treeRoot, where.approved, where.review, parent, changed, since, at) {
		fmt.Fprintf(stdout, "%s %s\n", e.class, e.path)
		if e.why != "" {
			fmt.Fprintf(stdout, "why %s %s\n", e.path, oneLine(e.why))
		}
	}
	return config.ExitOK
}

// changedPaths は置き場の変更のパス（ツリーからの相対、"/" 区切り、名前の順）。
func changedPaths(treeRoot string, where places, since string) ([]string, string) {
	specs := []string{literalSpec(where.approved), literalSpec(where.review)}
	opts := git.Options{Timeout: git.MergeTimeout, RawPaths: true}
	var out []string
	if since != "" {
		done := git.Run(treeRoot, append([]string{"diff", "--name-only", "-z", "--no-renames", since, "HEAD", "--"}, specs...), opts)
		if !done.OK() {
			return nil, since + "..HEAD の差分を読めない（" + failureText(done) + "）"
		}
		for _, p := range strings.Split(done.Out, "\x00") {
			if p != "" {
				out = append(out, p)
			}
		}
	} else {
		done := git.Status(treeRoot, git.Porcelain{NUL: true, Untracked: git.UntrackedAll, NoRenames: true, Paths: append([]string{}, specs...)}, opts)
		if !done.OK() {
			return nil, "git の状態を読めない（" + failureText(done) + "）"
		}
		for _, entry := range strings.Split(done.Out, "\x00") {
			if r := []rune(entry); len(r) > 3 {
				out = append(out, string(r[3:]))
			}
		}
	}
	sort.Strings(out)
	return out, ""
}

// failureText は Python の (done.err or done.failure).strip()。
func failureText(done git.Done) string {
	text := done.Stderr
	if text == "" && done.Failure != nil {
		text = done.Failure.Error()
	}
	return ospath.Strip(text)
}

// archiveAt は ready の退避を見る先（ワークスペースルートとプロジェクト）。
type archiveAt struct{ root, project string }

type contents struct {
	now, before []byte
	readable    bool
}

// classify は、変更のリストを分ける。since が無ければ未コミット（今の中身と HEAD）を、あれば HEAD と since を比べる。ユーザの判断が一緒に書く移動は、組の両側を見て (c) にする。at を渡せば、ready がこのツリーから退避した削除を (b) にする（ArchivedRemoval）。approvedRel・reviewRel は、ツリーからの相対の置き場である。上から当てて、最初に当たったものを採る。
func classify(treeRoot, approvedRel, reviewRel, parent string, changed []string, since string, at *archiveAt) []entry {
	var ready map[string]bool
	if at != nil {
		// マーカーが効くのは、書いたときの先頭と比べている版が同じ間だけ（未コミットなら HEAD）。
		rev := since
		if rev == "" {
			rev = "HEAD"
		}
		ready = archive.ReadyFiles(at.root, at.project, treeRoot, archive.TreeHead(treeRoot, rev))
	}
	opts := git.Options{Timeout: git.MergeTimeout}
	states := map[string]contents{}
	for _, rel := range changed {
		var c contents
		var ok, okBefore bool
		if since != "" {
			c.now, ok = git.Blob(treeRoot, "HEAD", rel, opts)
			c.before, okBefore = git.Blob(treeRoot, since, rel, opts)
		} else {
			c.now, ok = readDisk(onDisk(treeRoot, rel))
			c.before, okBefore = git.Blob(treeRoot, "HEAD", rel, opts)
		}
		c.readable = ok && okBefore
		states[rel] = c
	}
	added := func(rel string) bool {
		c, ok := states[rel]
		return ok && c.readable && c.before == nil && c.now != nil
	}
	removed := func(rel string) bool {
		c, ok := states[rel]
		return ok && c.readable && c.before != nil && c.now == nil
	}
	out := make([]entry, 0, len(changed))
	for _, rel := range changed {
		c := states[rel]
		switch {
		case temp.MatchString(rel):
			out = append(out, entry{kindSkip, rel, ""})
			continue
		case !c.readable:
			out = append(out, entry{kindD, rel, "中身を読めない"})
			continue
		}
		if name, ok := strings.CutPrefix(rel, reviewRel+"/"); ok {
			class := kindD
			if removed(rel) && added(approvedRel+"/"+string(place.Done)+"/"+name) {
				class = kindC
			}
			out = append(out, entry{class, rel, ""})
			continue
		}
		inside, ok := strings.CutPrefix(rel, approvedRel+"/")
		if !ok || inside == "" {
			out = append(out, entry{kindD, rel, ""})
			continue
		}
		parts := strings.Split(inside, "/")
		class := kindD
		switch {
		case hookMark(parts, parent, c.now, c.before) ||
			(at != nil && ArchivedRemoval(inside, c.now, c.before, ready, at.root, at.project)):
			class = kindB
		case since == "" && judgeRecord(parts):
			class = kindKeep
		case human(parts, approvedRel, reviewRel, added, removed):
			class = kindC
		}
		out = append(out, entry{class, rel, ""})
	}
	return out
}

// readDisk は今の中身。リンクと読めないものは読めない、無ければ (nil, 読めた)。
func readDisk(path string) ([]byte, bool) {
	if ospath.IsLink(path) {
		return nil, false
	}
	data, err := fsio.ReadDiskBytes(path)
	if err != nil {
		return nil, errors.Is(err, fs.ErrNotExist)
	}
	return data, true
}

// hookMark はその親子のチケットの phases/<親>/<N>.(pending|skipped) で、変更前は無く、中身が hook の欄だけか。
func hookMark(parts []string, parent string, now, before []byte) bool {
	if len(parts) != 3 || parts[0] != place.PhasesDir || parts[1] != parent || before != nil || now == nil {
		return false
	}
	n, kind, _ := strings.Cut(parts[2], ".")
	if !number.MatchString(n) || (kind != string(marks.Pending) && kind != string(marks.Skipped)) {
		return false
	}
	if !utf8.Valid(now) {
		return false
	}
	data, err := pyjson.Loads(string(now))
	obj, isObj := data.(*pyjson.Object)
	if err != nil || !isObj {
		return false
	}
	if _, isText := obj.Value("at").(string); !isText {
		return false
	}
	for _, key := range obj.Keys() {
		if !hookFields[key] {
			return false
		}
	}
	return true
}

// ArchivedRemoval は、ready の退避が消したものかを返す。rel は承認済みの領域からの相対である。ready のマーカー（そのツリーから ready が移したファイルの一覧）に載っていて、消えた中身が手元の退避したコピーと同じときだけ真になる。マーカーは、記録の保護がエージェントの書き込みを止める。退避の置き場の途中にリンクがあれば、コピーは読まない（ArchivedBytes）。
func ArchivedRemoval(rel string, now, before []byte, ready map[string]bool, root, project string) bool {
	if now != nil || before == nil || !ready[rel] || !archive.InReadyPlaces(rel) {
		return false
	}
	held, ok := archive.ArchivedBytes(root, project, rel)
	return archive.Holds(held, ok, before)
}

// judgeRecord は phases/<親>/<子>.judge.json（record-risk の記録）か。
func judgeRecord(parts []string) bool {
	return len(parts) == 3 && parts[0] == place.PhasesDir &&
		strings.HasSuffix(parts[2], "."+string(marks.Judge)+fsio.JSONExt)
}

// human は、ユーザの判断が書くものの形かを返す（形だけで見る。(c) も (d) も、write-and-push は止める）。対象は、フローの本文、ユーザの承認で置かれた承認済みチケット、reviewed・(b) でない skipped・close-early・受け入れ台帳、ユーザの判断が消したマーカー、ユーザの判断が一緒に書く移動（review/ から done/、doing/ から done/）である。
func human(parts []string, approvedRel, reviewRel string, added, removed func(string) bool) bool {
	rel := approvedRel + "/" + strings.Join(parts, "/")
	doing, done := string(place.Doing), string(place.Done)
	switch {
	case len(parts) == 2 && parts[0] == flowsDir:
		return strings.HasSuffix(parts[1], ".yml") || strings.HasSuffix(parts[1], ".yaml")
	case len(parts) == 2 && parts[0] == doing:
		// ユーザの承認で置かれたか、早めに閉じたときの取り消しで done/ へ動いた。
		return added(rel) || removed(rel) && added(approvedRel+"/"+done+"/"+parts[1])
	case len(parts) == 2 && parts[0] == done && added(rel):
		// ユーザのレビュー（review/ から）か、早めに閉じたときの取り消し（doing/ から）で動いた先。
		return removed(reviewRel+"/"+parts[1]) || removed(approvedRel+"/"+doing+"/"+parts[1])
	case len(parts) == 3 && parts[0] == place.PhasesDir:
		name := parts[2]
		if humanMarkNames[name] {
			return true
		}
		n, kind, _ := strings.Cut(name, ".")
		if !number.MatchString(n) {
			return false
		}
		for _, k := range marks.Kinds() {
			if kind == string(k) && removed(rel) {
				return true // ユーザの承認が消したマーカー（子が足された）
			}
		}
		return kind == string(marks.Reviewed) || kind == string(marks.Skipped)
	}
	return false
}
