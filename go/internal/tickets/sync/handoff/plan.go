package handoff

import (
	"bytes"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/proposal/templates"
)

// State は下書き 1 本の状態（JSON の state）。
type State string

const (
	StateNew       State = "new"
	StateChanged   State = "changed"
	StateUnchanged State = "unchanged"
	StateRejected  State = "rejected"
)

// absent は取り込み先が無いときの、照合の値のハッシュの欄。
const absent = "-"

const (
	msgEmptyPath   = "空のパスは指せない"
	msgAbsolute    = "%s は絶対パス。ツリーからの相対パスで指す"
	msgParent      = "%s は `..` を含むか空。ツリーからの相対パスで指す"
	msgNoDraft     = "%s の下書きが " + place.Handoff + "/ に無いか、取り込める種類の表にヒットしない"
	msgTwins       = "同じ取り込み先に落ちる下書きが複数ある（大文字小文字か NFC/NFD だけが違う）。1 本に揃える"
	msgGitPart     = "パスの要素に `.git` で始まるものがある。git の置き場には取り込めない"
	msgDraftUnread = "下書きを読めない（%s）"
	msgBinary      = "バイナリ（NUL を含む）は取り込めない"
	msgNotUTF8     = "UTF-8 として読めない"
	msgLinkOnWay   = "取り込み先の途中にリンクがあるか、ツリーの外を指すので書かない"
	msgTargetBad   = "取り込み先には書かない（%s）"
	msgCannotMake  = "取り込み先 %s が無い。この種類（%s）は新しく作れない"
	msgNotLayer    = "projects/%s はレイヤーとして読まれるプロジェクトではない（取り込んでも使われない）"
	msgLayerAway   = "projects/%s のレイヤーの置き場がこのツリーの外にある"
	msgMirror      = "このツリーの `.ccnavi/config/common/` は共通レイヤーのコピー（ミラー）なので書かない。" +
		"ワークスペースのツリーで取り込む"
)

// gitPrefix で始まる要素（.git・.gitmodules など）には取り込まない。
const gitPrefix = ".git"

// projectsPart は取り込み先のパスの先頭で、プロジェクトのレイヤーを指す要素。
const projectsPart = "projects"

// Entry は下書き 1 本。Rel は取り込み先のツリーからの相対パス（読み替えを戻した形）。Errors があれば取り込まない。
type Entry struct {
	Rel  string
	Kind *Kind
	// key は表に当てる形、draft は下書きの絶対パス、drel は置き場の中の相対パス（置いてある綴りのまま）、
	// target は取り込み先の絶対パス、raw と current は下書きと取り込み先のバイト列（無ければ nil）。
	key, draft, drel, target string
	raw, current             []byte
	// layer はテンプレートとレイヤーの設定のレイヤー（common・self・プロジェクト）、configKind はレイヤーの設定の種類。
	layer      string
	configKind config.LayerKind
	Errors     []string
	Warnings   []string
	Diff       string
	Selected   bool
	// Defense は、種類はボードで取り込めるが中身が防御の記述を動かすのでターミナルに限る理由。空なら種類のまま。
	Defense string
}

// Board はボードの経路（--via board）で取り込めるか。
func (e *Entry) Board() bool { return e.Kind.onBoard && e.Defense == "" }

// DraftRel は下書きのツリーからの相対パス。置いてある綴りのまま（無ければ表から組む）。
func (e *Entry) DraftRel() string {
	if e.drel != "" {
		return place.Handoff + "/" + e.drel
	}
	return DraftRel(e.Rel)
}

// State は下書きの状態。
func (e *Entry) State() State {
	switch {
	case len(e.Errors) > 0:
		return StateRejected
	case e.current == nil:
		return StateNew
	case same(e.current, e.raw):
		return StateUnchanged
	}
	return StateChanged
}

// record は照合の値の 1 件分（長さの前置きつき）。
func (e *Entry) record() []byte {
	var b bytes.Buffer
	for _, v := range []string{e.key, string(e.Kind.ID), contentDigest(e.raw), contentDigest(e.current)} {
		b.WriteString(strconv.Itoa(len(v)))
		b.WriteByte(':')
		b.WriteString(v)
	}
	return b.Bytes()
}

// sha は照合の値の 1 件分のハッシュ（JSON の entry_sha）。
func (e *Entry) sha() string {
	return digest.HexBytes(digest.SHA256, e.record(), 0)
}

func contentDigest(raw []byte) string {
	if raw == nil {
		return absent
	}
	return digest.HexBytes(digest.SHA256, raw, 0)
}

// same は Python のバイト列の ==（None と b"" は違う）。
func same(a, b []byte) bool {
	return (a == nil) == (b == nil) && bytes.Equal(a, b)
}

// expectOf は選んだ対象の照合の値。パスの並び順で記録を連結した SHA-256。
func expectOf(entries []*Entry) string {
	ordered := sortedByKey(entries)
	h := digest.New(digest.SHA256)
	for _, e := range ordered {
		h.Write(e.record())
	}
	return digest.HexSum(h)
}

func sortedByKey(entries []*Entry) []*Entry {
	out := append([]*Entry(nil), entries...)
	sort.SliceStable(out, func(i, j int) bool { return out[i].key < out[j].key })
	return out
}

// Plan は下見の結果。Error は全体を止める誤り（置き場がリンク、引数のパスが読めないなど）。
type Plan struct {
	Tree    string
	Entries []*Entry
	Ignored []Ignored
	Error   string
}

// Selected は選んだ対象。
func (p *Plan) Selected() []*Entry {
	var out []*Entry
	for _, e := range p.Entries {
		if e.Selected {
			out = append(out, e)
		}
	}
	return out
}

// Expect は選んだ対象の照合の値（--expect）。
func (p *Plan) Expect() string { return expectOf(p.Selected()) }

// OK は全体の誤りが無く、選んだ対象に断ったものが無いか。
func (p *Plan) OK() bool {
	if p.Error != "" {
		return false
	}
	for _, e := range p.Selected() {
		if e.State() == StateRejected {
			return false
		}
	}
	return true
}

// Prepare は下書きを読み、検査し、取り込み先との差分を作る。何も書かない。wanted は引数で指したパス
// （ツリーからの相対）で、空なら表に当たり unchanged でないもの全部を選ぶ。
func Prepare(conf *config.Settings, root string, wanted []string) *Plan {
	plan := &Plan{Tree: root}
	found, ignored, refused := Walk(root)
	plan.Ignored = ignored
	if refused != "" {
		plan.Error = refused
		return plan
	}
	count := map[string]int{}
	for _, f := range found {
		count[f.Key]++
	}
	for _, f := range found {
		e := &Entry{Rel: f.Rel, key: f.Key, Kind: KindOf(f.Key), draft: f.Path, drel: f.Drel}
		if count[f.Key] > 1 {
			e.Errors = append(e.Errors, msgTwins)
		}
		read(conf, root, e)
		plan.Entries = append(plan.Entries, e)
	}
	if len(wanted) == 0 {
		for _, e := range plan.Entries {
			e.Selected = e.State() != StateUnchanged
		}
	} else {
		index := map[string]*Entry{}
		for _, e := range plan.Entries {
			index[e.key] = e
		}
		for _, arg := range wanted {
			key, why := wantedKey(arg)
			if why != "" {
				plan.Error = why
				return plan
			}
			hit := index[key]
			if hit == nil {
				plan.Error = sprintf(msgNoDraft, line(arg))
				return plan
			}
			hit.Selected = true
		}
	}
	checkContents(conf, root, plan)
	for _, e := range plan.Entries {
		makeDiff(e)
	}
	return plan
}

// wantedKey は引数のパスを比べる形にする。(そろえた形, 断る理由)。置き場の中のパス（下書きの綴り）も受け、
// 読み替えを戻して取り込み先のパスにする。
func wantedKey(arg string) (string, string) {
	text := ospath.Strip(strings.ReplaceAll(arg, `\`, "/"))
	if text == "" {
		return "", msgEmptyPath
	}
	if strings.HasPrefix(text, "/") || ospath.IsAbs(text) || ospath.HasDrive(text, true) {
		return "", sprintf(msgAbsolute, line(arg))
	}
	var parts []string
	for _, p := range strings.Split(text, "/") {
		if p != "" && p != "." {
			parts = append(parts, p)
		}
	}
	for _, p := range parts {
		if p == ".." {
			return "", sprintf(msgParent, line(arg))
		}
	}
	if len(parts) == 0 {
		return "", sprintf(msgParent, line(arg))
	}
	key := Key(strings.Join(parts, "/"))
	if rest, ok := strings.CutPrefix(key, Key(place.Handoff)+"/"); ok {
		key = Key(names.FromDraft(rest))
	}
	return key, ""
}

// read は下書きと取り込み先を読み、形の上の断る理由を足す。中身の検査は checkContents。
func read(conf *config.Settings, root string, e *Entry) {
	for _, part := range strings.Split(e.key, "/") {
		if strings.HasPrefix(part, gitPrefix) {
			e.Errors = append(e.Errors, msgGitPart)
			break
		}
	}
	raw, why := flow.ReadBytes(e.draft, root)
	if raw == nil {
		e.Errors = append(e.Errors, sprintf(msgDraftUnread, why))
	} else {
		e.raw = raw
		if bytes.IndexByte(raw, 0) >= 0 {
			e.Errors = append(e.Errors, msgBinary)
		} else if _, err := fsio.Decode(raw, fsio.Strict); err != nil {
			e.Errors = append(e.Errors, msgNotUTF8)
		}
	}
	locateLayer(conf, root, e)
	target, why := Spelled(root, e.Rel)
	if why != "" {
		e.Errors = append(e.Errors, why)
		return
	}
	e.target = target
	if !inside(root, target) || pathmatch.HasLink(root, target) {
		e.Errors = append(e.Errors, msgLinkOnWay)
		return
	}
	switch {
	case ospath.DiskLexists(target):
		current, why := flow.ReadBytes(target, root)
		if current == nil {
			e.Errors = append(e.Errors, sprintf(msgTargetBad, why))
			return
		}
		e.current = current
	case !e.Kind.creatable:
		e.Errors = append(e.Errors, sprintf(msgCannotMake, e.Rel, e.Kind.ID))
	}
}

func inside(root, path string) bool {
	base := pathcase.Fold(ospath.Normpath(ospath.Abspath(root)))
	full := pathcase.Fold(ospath.Normpath(ospath.Abspath(path)))
	return strings.HasPrefix(full, base+ospath.Sep)
}

// locateLayer はテンプレートとレイヤーの設定のレイヤーを決める。読まれない置き場なら断る。
// 文面ファイルはレイヤーを持たない（projects/<名前>/.ccnavi/messages/ は表が許さない）。
func locateLayer(conf *config.Settings, root string, e *Entry) {
	if e.Kind.ID != Template && e.Kind.ID != LayerConfig {
		return
	}
	parts := strings.Split(e.key, "/")
	switch {
	case parts[0] == projectsPart:
		project := strings.Split(e.Rel, "/")[1]
		layers := map[string]ruleload.Layer{}
		for _, l := range ruleload.Layers(conf, root) {
			layers[FoldPart(l.Name)] = l
		}
		l, ok := layers[FoldPart(project)]
		if !ok || l.Name == config.LayerSelf {
			e.Errors = append(e.Errors, sprintf(msgNotLayer, line(project)))
			return
		}
		e.layer = l.Name
		rel, ok := ospath.Relpath(l.Home, root)
		if !ok || Key(ospath.Slashed(rel)) != strings.Join(parts[:2], "/") {
			e.Errors = append(e.Errors, sprintf(msgLayerAway, line(project)))
		}
	case parts[2] == config.LayerCommon:
		e.layer = config.LayerCommon
		if !templates.CommonWritable(conf, root) {
			e.Errors = append(e.Errors, msgMirror)
		}
	default:
		e.layer = config.LayerSelf
	}
	if e.Kind.ID == LayerConfig {
		for kind, file := range config.LayerFileNames {
			if file == parts[len(parts)-1] {
				e.configKind = kind
			}
		}
	}
}

func sprintf(format string, a ...any) string { return fmt.Sprintf(format, a...) }

// line は空白をつぶして 1 行にする（Python の " ".join(str(v).split())）。
func line(text string) string {
	return strings.Join(strings.FieldsFunc(text, ospath.IsSpace), " ")
}
