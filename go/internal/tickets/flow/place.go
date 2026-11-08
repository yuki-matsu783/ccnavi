package flow

import (
	"os"
	"path"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

const (
	// CodeLocked はロックで止めたときの理由コード（文面ファイルの識別子も同じ）。
	CodeLocked = "DENY_TICKET_FLOW_LOCKED"
	// LockRule はロックで止めたときの記録のルール名。
	LockRule = rules.ReservedIDPrefix + "ticket-flow-lock"
	// codeChanged は着手のあとにフローが書き換わったと知らせる理由コード。止めない（知らせるだけ）。
	codeChanged = "NOTICE_TICKET_FLOW_CHANGED"

	// FlowsDir と Suffix は置き場の形。承認済みチケットの置き場（と提案の置き場）の下の `flows/<子>.yml`。
	FlowsDir = "flows"
	Suffix   = ".yml"
	// anyProject は置き場が絶対パスかツリーの外で、どのツリーにも共通のとき。どのプロジェクトの子にも当てる。
	anyProject = "*"

	// 着手のときに残すハッシュの記録（`phases/<親>/<子>.flow.json`）。形は承認の子の記録と同じ。
	digestRecord = "flow"
)

// posixNormpath は posixpath.normpath。path.Clean と違い、頭のちょうど 2 つの "/" を残す。
func posixNormpath(p string) string {
	if strings.HasPrefix(p, "//") && !strings.HasPrefix(p, "///") {
		return "/" + path.Clean(p)
	}
	return path.Clean(p)
}

func isAbsolute(rel string) bool {
	r := []rune(rel)
	return ospath.IsAbs(rel) || strings.HasPrefix(rel, "/") || len(r) >= 3 && string(r[1:3]) == ":/"
}

// placeRel は置き場のパスを整える（"/" 区切り、前後の区切りなし）。絶対なら絶対のまま。
// `./`・`//`・`x/..` を整えないと、判定が整えたパスに当てたときに置き場と食い違い、ロックが外れる。
func placeRel(raw string) string {
	raw = strings.ReplaceAll(raw, `\`, "/")
	if isAbsolute(raw) {
		if out := strings.TrimRight(posixNormpath(raw), "/"); out != "" {
			return out
		}
		return "/"
	}
	norm := strings.Trim(posixNormpath(raw), "/")
	if norm == "" || norm == "." {
		return strings.Trim(raw, "/")
	}
	return norm
}

func approvedRel(conf *config.Settings) string {
	return placeRel(orDefault(conf.Approved, config.DefaultApproved))
}

// TicketsRel は提案の置き場のパス（"/" 区切り、前後の区切りなし）。下書きの置き場もこの下に並ぶ。
func TicketsRel(conf *config.Settings) string {
	return placeRel(orDefault(conf.Tickets, config.DefaultTickets))
}

func orDefault(s, fallback string) string {
	if s == "" {
		return fallback
	}
	return s
}

// FlowRel は子のフローの置き場。ツリーのルートからの相対（置き場が絶対ならその絶対パス）。
func FlowRel(conf *config.Settings, id string) string {
	return approvedRel(conf) + "/" + FlowsDir + "/" + id + Suffix
}

// FlowFile はこのツリーでの子のフローの絶対パス（整えたパス）。
func FlowFile(conf *config.Settings, treeRoot, id string) string {
	return ospath.Normpath(ospath.Join(conf.ApprovedDir(treeRoot), FlowsDir, id+Suffix))
}

// DraftRel は子のフローの下書きの置き場。提案の置き場の `flows/<子>.yml`。提案の置き場は範囲の外なので、
// エージェントは判定を変えずに書ける。下書きに効力は無く、ユーザが取り込んで FlowRel に保存したものだけが効く。
func DraftRel(conf *config.Settings, id string) string {
	return TicketsRel(conf) + "/" + FlowsDir + "/" + id + Suffix
}

// DraftFile はこのツリーでの子のフローの下書きの絶対パス。
func DraftFile(conf *config.Settings, treeRoot, id string) string {
	return ospath.Normpath(ospath.Join(treeRoot, strings.Split(DraftRel(conf, id), "/")...))
}

// Resolve はフローのファイルの絶対パスと、それがあるツリーのルートと、在るか（リンクでも在るとする）。
// 読むのは正とするツリー（承認済みチケットが在るツリー）の版だけ。子のワークツリーの版はシェルの書き込みで
// 書き換えられうるので読まない。
func Resolve(conf *config.Settings, root string, child *ticket.Ticket) (file, base string, exists bool) {
	base = orDefault(child.TreeRoot, root)
	file = FlowFile(conf, base, child.ID)
	return file, base, ospath.DiskLexists(file)
}

// Info はボード（--explain --json）に出すフローの欄。
type Info struct {
	Path, Rel, Tree string
	Exists, Linked  bool
	// LockKind は錠の種類。レビュー中（Review）は着手の有無によらず止め、着手中は Doing。空なら錠は無い。
	LockKind place.State
	Draft    DraftInfo
}

// DraftInfo はエージェントが書く下書きの欄。置くツリーはフローと同じ。
type DraftInfo struct {
	Path, Rel      string
	Exists, Linked bool
}

// Locked は錠がかかっているか。
func (i *Info) Locked() bool { return i.LockKind != "" }

// FlowInfo はボードに出すフローの欄。子でなければ nil。閉じた子でフローが無ければ nil（作っても読まれる
// 場面が無い）。Linked はツリーのルートからファイルまでの途中にリンクがあるか（在るときだけ見る）。
func FlowInfo(conf *config.Settings, root string, child *ticket.Ticket) *Info {
	if !child.IsChild() {
		return nil
	}
	file, base, exists := Resolve(conf, root, child)
	if !exists && (child.State == place.Done || child.State == place.Cancelled) {
		return nil
	}
	draft := DraftFile(conf, base, child.ID)
	draftExists := ospath.DiskLexists(draft)
	var lock place.State
	switch {
	case child.State == place.Review:
		lock = place.Review
	case child.InProgress() && child.State == place.Doing:
		lock = place.Doing
	}
	return &Info{
		Path: file, Rel: FlowRel(conf, child.ID), Tree: base,
		Exists: exists, Linked: exists && pathmatch.HasLink(base, file),
		LockKind: lock,
		Draft: DraftInfo{
			Path: draft, Rel: DraftRel(conf, child.ID),
			Exists: draftExists, Linked: draftExists && pathmatch.HasLink(base, draft),
		},
	}
}

// fold は区切りを "/" に揃え、1 字ずつ小文字にする。小文字にすると長さの変わる字（İ など）はそのまま残すので、
// 符号位置の数は変わらない（位置で元のパスを切り出す Locate の読みが食い違わない）。
func fold(p string) []rune {
	out := []rune(strings.ReplaceAll(p, `\`, "/"))
	for i, r := range out {
		if low := pyfmt.Lower(string(r)); utf8.RuneCountInString(low) == 1 {
			out[i], _ = utf8.DecodeRuneInString(low)
		}
	}
	return out
}

// Location は書き込み先が子のフローの置き場のときの、ファイルの名前（`<子>.yml`。小文字にしたもの）と、
// 置き場があるツリーのプロジェクト。known が偽なら置き場はワークスペースの外のツリーにある。
type Location struct {
	name    string
	project string
	known   bool
}

// Locate は path が子のフローの置き場か。パスは解いたものでも解く前のものでもよく、大文字小文字は区別しない。
// 名前は Windows で同じファイルを指す表記をまとめる（末尾の `.` と空白、`:` から後ろの代替データストリームを
// 除く。止める向きだけ）。8.3 形式の短い名前はまとめられないので、解いたパスに任せる。
func Locate(conf *config.Settings, root, file string) (Location, bool) {
	if file == "" {
		return Location{}, false
	}
	norm := ospath.Normpath(file)
	here := fold(norm)
	rel := approvedRel(conf)
	absolute := isAbsolute(rel)
	shared := absolute
	if !absolute && (rel == ".." || strings.HasPrefix(rel, "../")) {
		// ツリーの外（`../shared/approved`）を指す置き場。ツリーをまたいで 1 か所になりうるので、
		// `..` を除いた残りで当て、どのプロジェクトの子でも止める（止める向き）。
		var kept []string
		for _, part := range strings.Split(rel, "/") {
			if part != ".." {
				kept = append(kept, part)
			}
		}
		rel, shared = strings.Join(kept, "/"), true
	}
	lead := ""
	switch {
	case absolute:
		lead = rel
	case rel != "":
		lead = "/" + rel
	}
	marker := fold(lead + "/" + FlowsDir + "/")
	at := lastIndex(here, marker)
	if at < 0 {
		return Location{}, false
	}
	name, _, _ := strings.Cut(string(here[at+len(marker):]), ":")
	name = strings.TrimRight(name, ". ")
	if name == "" {
		return Location{}, false
	}
	if shared {
		return Location{name: name, project: anyProject, known: true}, true
	}
	owner := string([]rune(norm)[:at])
	if owner == "" {
		owner = ospath.Sep
	}
	t, ok := tree.TreeOf(root, owner, conf.Projects)
	return Location{name: name, project: t.Project, known: ok}, true
}

func lastIndex(hay, needle []rune) int {
	for i := len(hay) - len(needle); i >= 0; i-- {
		if hasPrefixAt(hay, needle, i) {
			return i
		}
	}
	return -1
}

// LockHit はこの書き込みを止める着手中の子。copies はどのツリー上のチケットも並べたもの（識別子で 1 本に
// まとめる前）で、どれか 1 本でも着手中なら止める（止める向きだけ）。
func LockHit(copies []*ticket.Ticket, at Location) *ticket.Ticket {
	if !at.known {
		return nil
	}
	for _, child := range copies {
		if child.IsChild() && child.InProgress() &&
			(at.project == child.Project || at.project == anyProject) &&
			pyfmt.Casefold(child.ID+Suffix) == pyfmt.Casefold(at.name) {
			return child
		}
	}
	return nil
}

// HardLinked は在るふつうのファイルで、名前が 2 つ以上あるか。無い・読めないなら偽。
func HardLinked(file string) bool {
	info, err := os.Stat(file)
	if err != nil || !info.Mode().IsRegular() {
		return false
	}
	n, ok := ospath.HardLinks(file)
	return ok && n > 1
}

// InodeHit は書き込み先が、着手中の子のフローとハードリンクで同じ中身なら、その子。ハードリンクはパスに
// 置き場が出ないので Locate では当たらない。名前が 2 つ以上あるときだけ、着手中の子のフロー（どのツリーの
// 置き場も）と同じファイルかを比べる。
func InodeHit(conf *config.Settings, root string, copies []*ticket.Ticket, file string) *ticket.Ticket {
	info, err := os.Stat(file)
	if err != nil || !info.Mode().IsRegular() {
		return nil
	}
	if n, ok := ospath.HardLinks(file); !ok || n < 2 {
		return nil
	}
	for _, child := range copies {
		if !child.IsChild() || !child.InProgress() {
			continue
		}
		for _, base := range pyfmt.NonEmpty([]string{child.TreeRoot, root}, pyfmt.DropRepeats) {
			other, err := os.Stat(FlowFile(conf, base, child.ID))
			if err == nil && os.SameFile(info, other) {
				return child
			}
		}
	}
	return nil
}

// LockedMessage はロックで止めたときの文面。
func LockedMessage(root string, conf *config.Settings, child *ticket.Ticket, file string) string {
	id := Clean(child.ID)
	return strings.Join([]string{
		hookio.Cited(CodeLocked, Clean(child.Path)),
		"ticket: " + id + " (" + Clean(child.Title) + "), started " + Clean(child.StartedAt),
		"path: " + Clean(file),
		texts.Say(root, CodeLocked, texts.Values{
			"ticket": id,
			"flow":   FlowRel(conf, child.ID),
			"draft":  DraftRel(conf, child.ID),
		}, nil),
	}, "\n")
}
