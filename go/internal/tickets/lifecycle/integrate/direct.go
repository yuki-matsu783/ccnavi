package integrate

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
)

const (
	// RecordDir は `ccnavi-git.sh integrate` が取り込みの途中の記録を置く、統合先の git ディレクトリの中の名前。
	RecordDir = "ccnavi-integrate"
	// DoneDir は片付けた記録のコピーの置き場。
	DoneDir = "ccnavi-integrate-done"
	// squashMsg は tree.busyMarks に無い、integrate が断る途中の操作の目印。
	squashMsg = "SQUASH_MSG"
	// markerFile は取り込みの途中の記録の中で版を書くファイル。
	markerFile = "marker"
)

// Marker は取り込みの途中の記録の種類。
type Marker string

const (
	markerNone    Marker = ""
	MarkerV4      Marker = "v4"
	MarkerUnknown Marker = "unknown" // 版を知らない（壊れた記録）
)

// Direct は Stop で integrate を促す（か、促せない理由を言う）相手。Blocked が空でなければ促さず理由を言う。
type Direct struct {
	Name        string
	worktree    string
	head        string
	dirty       bool
	ahead       int
	integration string
	// integTree は統合先をチェックアウトしているツリー（見つからなければ空）。
	integTree string
	Blocked   string
	// base は記録の取り込み先。記録があるときだけ入る（無いときは統合先で数える）。
	base string
	// noRecord は記録が無い（手で切った）。integrate は断るので --adopt を案内する。
	noRecord bool
	marker   Marker
	// originAhead は手元の取り込み先より origin/<取り込み先> が先に進んでいるコミットの数。
	originAhead int
}

// nudgeKey は記録のキーの頭（チケットの識別子・request: と分ける）。
const nudgeKey = "integrate:"

// Key は `integrate:<名前>`。
func (d *Direct) Key() string { return nudgeKey + d.Name }

// Digest は促すか理由か・未コミットの有無・入っていないコミットの数・HEAD など。進めばまた言う。
func (d *Direct) Digest() string {
	kind := "nudge"
	if d.Blocked != "" {
		kind = "blocked"
	}
	extra := ""
	if d.marker != markerNone {
		extra += ":marker-" + string(d.marker)
	}
	if d.noRecord {
		extra += ":no-record"
	}
	if d.originAhead != 0 {
		extra += ":origin+" + strconv.Itoa(d.originAhead)
	}
	dirt := "clean"
	if d.dirty {
		dirt = "dirty"
	}
	return kind + ":" + dirt + ":" + strconv.Itoa(d.ahead) + "@" + d.head + extra
}

// count は git の数の答え。走って数字だけなら真。
func count(done git.Done) (int, bool) {
	if !done.OK() {
		return 0, false
	}
	return git.Count(done.Out)
}

// Record は ccnavi-worktree.sh が置いた直接作業の記録（git の共通ディレクトリの ccnavi-direct/<名前>）。
// broken は読めても形が違う（版・取り込み先・起点が欠ける）。
type Record struct {
	Fields map[string]string
	Common string
	broken bool
}

// DirectRecord は直接作業の記録をファイルだけで読む。無ければ nil。
func DirectRecord(owner, name string) *Record {
	common, ok := tree.CommonDir(owner)
	if !ok {
		return nil
	}
	path := ospath.Join(common, tree.DirectDir, name)
	if !ospath.IsFile(path) {
		return nil
	}
	text, _ := fsio.ReadText(path, fsio.Replace)
	fields := tree.ParseDirect(text)
	broken := fields["version"] != tree.DirectVersion || fields["base_branch"] == "" || fields["snap_sha"] == ""
	return &Record{fields, common, broken}
}

// MarkerKind は marks（ccnavi-integrate の置き場）の下の <名前>/ の記録の種類。
func MarkerKind(marks, name string) Marker {
	folder := ospath.Join(marks, name)
	if !ospath.DiskLexists(folder) {
		return markerNone
	}
	text, _ := fsio.ReadText(ospath.Join(folder, markerFile), fsio.Replace)
	if tree.ParseDirect(text)["version"] == tree.DirectVersion {
		return MarkerV4
	}
	return MarkerUnknown
}

// AheadSince は記録の起点 snap から HEAD までのコミットの数。数えられなければ偽。
func AheadSince(worktree, snap string, deadline time.Time) (int, bool) {
	return count(git.Run(worktree, []string{"rev-list", "--count", snap + "..HEAD"}, git.Options{Deadline: deadline}))
}

// IntegrationName はそのリポジトリ（owner はそのチェックアウト）の統合先の名前。手元の origin/HEAD、無ければ
// origin/main・origin/master の順（sh の ccnavi_integration と同じ）。決まらなければ空。
func IntegrationName(owner string) string {
	name, _ := Integration(owner, time.Time{})
	return name
}

// Integration は IntegrationName の期限つきの版。期限不足・git の失敗で決められなければ偽。
func Integration(owner string, deadline time.Time) (string, bool) {
	if head := tree.OriginHead(owner); head != "" {
		return head, true
	}
	for _, name := range []string{"main", "master"} {
		done := git.Run(owner, []string{"rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + name}, git.Options{Deadline: deadline})
		if done.Failure != nil {
			return "", false
		}
		if done.OK() {
			return name, true
		}
	}
	return "", true
}

// GitPath は `git rev-parse --git-path <name>`（相対ならツリーから）。引けなければ偽。
func GitPath(top, name string, deadline time.Time) (string, bool) {
	done := git.Run(top, []string{"rev-parse", "--git-path", name}, git.Options{Deadline: deadline})
	path := ""
	if done.OK() {
		path = ospath.Strip(done.Out)
	}
	if path == "" {
		return "", false
	}
	if ospath.IsAbs(path) {
		return path, true
	}
	return ospath.Join(top, path), true
}

// Repeat は「キー 値」の記録で同じキーが 2 度あるときの読み方。
type Repeat int

const (
	// LastKept は最後の行を採り、値の前後の空白を除く（Stop の促しの読み方）。
	LastKept Repeat = iota
	// firstKept は最初の行を採り、値をそのまま残す（sh の ccnavi_record_get と、取り込み後の確認 post_findings の読み方）。
	firstKept
)

// ReadRecord は ccnavi_record_write が書いた「キー 値」の行の記録。読めなければ空。読む側ごとに読み方が
// 違うので、同じキーの扱いを repeat で選ぶ。
func ReadRecord(path string, repeat Repeat) map[string]string {
	text, _ := fsio.ReadText(path, fsio.Replace)
	out := map[string]string{}
	for _, line := range ospath.Splitlines(text) {
		key, value, _ := strings.Cut(line, " ")
		if key == "" {
			continue
		}
		if repeat == firstKept {
			if _, seen := out[key]; !seen {
				out[key] = value
			}
			continue
		}
		out[key] = ospath.Strip(value)
	}
	return out
}

// HasRecord は integrate <名前> の記録があるか。取り込みの途中の記録があれば有り。片付けた記録のコピーは、
// コピーの branch_sha が今の HEAD と同じで、未コミットの変更が無いと確かめられたときだけ有り（名前を使い
// 直した作業を、前のコピーで黙らせない）。dirty が nil（不明）でそこまで来たら、調べられないので known が偽。
func HasRecord(integTree, name, head string, dirty *bool, deadline time.Time) (recorded, known bool) {
	marks, ok := GitPath(integTree, RecordDir, deadline)
	if !ok {
		return false, false
	}
	if ospath.DiskLexists(ospath.Join(marks, name)) {
		return true, true
	}
	done, ok := GitPath(integTree, DoneDir, deadline)
	if !ok {
		return false, false
	}
	copied := ospath.Join(done, name)
	if !ospath.DiskLexists(copied) {
		return false, true
	}
	if sha := ReadRecord(copied, LastKept)["branch_sha"]; sha == "" || head == "" || sha != head {
		return false, true
	}
	if dirty == nil {
		return false, false
	}
	return !*dirty, true
}

// integrationBusy は取り込み先のツリーに途中の操作があればその理由。未コミットの変更は見ない（ユーザの
// 書きかけは fast-forward で持ち越す）。
func integrationBusy(integTree string, deadline time.Time) string {
	mark := tree.BusyOf(integTree)
	if mark == "" {
		if squash, ok := GitPath(integTree, squashMsg, deadline); ok && ospath.DiskLexists(squash) {
			mark = squashMsg
		}
	}
	if mark == "" {
		return ""
	}
	return fmt.Sprintf("統合先のツリー（%s）に途中の操作（%s）があります。持ち主に確かめて済ませてもらってください", integTree, mark)
}
