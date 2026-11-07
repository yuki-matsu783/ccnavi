package templates

import (
	"bytes"
	"cmp"
	"errors"
	"io/fs"
	"os"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/flow"
)

// Dropped は統合結果で取り込まれない理由。
type Dropped string

const (
	DropNone      Dropped = ""
	DropInvalid   Dropped = "invalid"
	DropDuplicate Dropped = "duplicate"
	DropConflict  Dropped = "conflict"
)

// Entry は置き場の <名前>.yml 1 本。読めなかったものも載せる（Template が nil）。Notes は読むときと統合結果の
// 苦情、Checks は計画とフローの検査の苦情（フェーズ定義が変われば変わるので分ける）。Effective は統合結果で
// 取り込まれたか（読めないもの・衝突で破棄したものは偽）。
type Entry struct {
	Layer         string
	Name          string
	Path          string
	Rel           string
	SHA           string
	Raw           []byte
	Template      *Template
	Notes         []rules.Problem
	Checks        []rules.Problem
	Effective     bool
	Dropped       Dropped
	DroppedDetail string
	Writable      bool
}

// Problems は読むときの苦情と検査の苦情。
func (e *Entry) Problems() []rules.Problem { return append(slices.Clip(e.Notes), e.Checks...) }

// Usable は取り込まれていて error も無いか（使う側が受けてよい）。
func (e *Entry) Usable() bool { return e.Effective && !hasError(e.Problems()) }

func (e *Entry) drop(why Dropped, detail string) {
	e.Effective = false
	e.Dropped, e.DroppedDetail = why, detail
}

// LayerRead は 1 つのレイヤーの置き場。Problems は置き場そのものの苦情（読めない、.yml でないファイル）。
type LayerRead struct {
	Name       string
	Directory  string
	Rel        string
	Exists     bool
	Unreadable string
	Writable   bool
	Entries    []*Entry
	Problems   []rules.Problem
}

// Scan は共通と各レイヤー（自身のレイヤーが先、プロジェクトは名前順）。Catalog はフローの検査の候補。
type Scan struct {
	Root    string
	Common  *LayerRead
	Layers  []*LayerRead
	Catalog flow.Catalog
}

// Layer は名前のレイヤー。無ければ nil。
func (s *Scan) Layer(name string) *LayerRead {
	for _, l := range s.Layers {
		if l.Name == name {
			return l
		}
	}
	return nil
}

func (s *Scan) all() []*LayerRead { return append([]*LayerRead{s.Common}, s.Layers...) }

// Relative は root からの相対（/ 区切り）。root の外なら path を / 区切りにしたもの。
func Relative(root, path string) string {
	rel, ok := ospath.Relpath(path, root)
	if !ok || rel == ".." || strings.HasPrefix(rel, ".."+ospath.Sep) {
		return ospath.Slashed(path)
	}
	return ospath.Slashed(rel)
}

func stemOf(path string) (base, stem string) {
	base = ospath.Basename(path)
	return base, strings.TrimSuffix(base, Suffix)
}

func badFileName(base string) string {
	return "ファイル名 `" + line(base) + "` は名前に使えない（英数字で始め、英数字と `._-` だけ）"
}

// SHA はファイルの中身のハッシュ。保存の --expect に使う。
func SHA(raw []byte) string {
	sum, _ := digest.HexOf(digest.SHA256, bytes.NewReader(raw))
	return sum
}

// ReadEntry は置き場の 1 本を読む。読めなければ理由を error の苦情に持つ（Template は nil）。
func ReadEntry(layer, root, path string, writable bool) *Entry {
	base, stem := stemOf(path)
	e := &Entry{Layer: layer, Name: stem, Path: path, Rel: Relative(root, path), Writable: writable, Effective: true}
	raw, why := flow.ReadBytes(path, root)
	if raw == nil {
		e.Notes = append(e.Notes, rules.Problem{Severity: rules.Error, Rule: stem, Detail: why})
		e.drop(DropInvalid, why)
		return e
	}
	e.Raw, e.SHA = raw, SHA(raw)
	if !validName(stem) {
		text := badFileName(base)
		e.Notes = append(e.Notes, rules.Problem{Severity: rules.Error, Rule: stem, Detail: text})
		e.drop(DropInvalid, text)
		return e
	}
	t, notes := Parse(raw, stem)
	e.Notes = append(e.Notes, notes...)
	if t == nil {
		first := "読めない"
		for _, p := range notes {
			if p.Severity == rules.Error {
				first = p.Detail
				break
			}
		}
		e.drop(DropInvalid, first)
		return e
	}
	e.Template = t
	return e
}

// osReason は読めない理由（Python の exc.strerror か型の名前）。
func osReason(err error) string {
	return "読めない (" + line(cmp.Or(fsio.StrError(err), "OSError")) + ")"
}

// ReadLayer はレイヤーの置き場の <名前>.yml を全部読む（名前順）。置き場が無いのは正常（空）。
func ReadLayer(layer, root, dir string, writable bool) *LayerRead {
	l := &LayerRead{Name: layer, Directory: dir, Rel: Relative(root, dir), Writable: writable}
	before, err := os.Lstat(dir)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		return l
	case err != nil:
		l.Unreadable = osReason(err)
	case before.Mode()&os.ModeSymlink != 0 || pathmatch.HasLink(root, dir):
		l.Unreadable = "置き場がシンボリックリンクなので読まない"
	case !before.IsDir():
		l.Unreadable = "置き場がディレクトリではない"
	}
	l.Exists = true
	if l.Unreadable == "" {
		names, err := ospath.DiskListdir(dir)
		if err != nil {
			l.Unreadable = osReason(err)
		} else {
			l.readEntries(root, names)
			return l
		}
	}
	l.Problems = append(l.Problems, rules.Problem{Severity: rules.Error, Detail: l.Rel + ": " + l.Unreadable})
	return l
}

func (l *LayerRead) readEntries(root string, names []string) {
	for _, name := range names {
		if strings.HasPrefix(name, ".") {
			continue
		}
		if !strings.HasSuffix(name, Suffix) {
			l.Problems = append(l.Problems, rules.Problem{Severity: rules.Warn,
				Detail: l.Rel + "/" + line(name) + " は `" + Suffix + "` でないので読まない"})
			continue
		}
		l.Entries = append(l.Entries, ReadEntry(l.Name, root, ospath.Join(l.Directory, name), l.Writable))
	}
}

// Merge は共通にレイヤーを足す。同名で全欄が同じならレイヤーの側を破棄して info、中身が違えば error で
// レイヤーの側を使わない。共通の側が読めていないものは無いものとして扱う。苦情は返し、レイヤーの側の Notes にも足す。
func Merge(common, extra *LayerRead) []rules.Problem {
	base := map[string]*Entry{}
	for _, e := range common.Entries {
		if e.Template != nil {
			base[e.Name] = e
		}
	}
	var said []rules.Problem
	for _, e := range extra.Entries {
		prior := base[e.Name]
		if prior == nil || e.Template == nil {
			continue
		}
		var p rules.Problem
		if Dump(prior.Template) == Dump(e.Template) {
			p = rules.Problem{Severity: rules.Info, Rule: e.Name, Detail: "`" + e.Name + "` は共通と全欄が同じなので、" +
				e.Layer + " の側を破棄した。共通の 1 本を使う"}
			e.Dropped = DropDuplicate
		} else {
			p = rules.Problem{Severity: rules.Error, Rule: e.Name, Detail: "`" + e.Name + "` は共通と同じ名前で中身が違う（違う欄: " +
				strings.Join(differing(prior.Template, e.Template), ", ") + "）。" +
				e.Layer + " の `" + e.Name + "` は使わず、共通の定義を使う。" +
				"中身を変えたいなら " + e.Layer + " の側に別の名前を付ける"}
			e.Dropped = DropConflict
		}
		e.DroppedDetail = p.Detail
		e.Effective = false
		e.Notes = append(e.Notes, p)
		said = append(said, p)
	}
	return said
}

// differing は既知の欄のうち中身が違うもの（Python の == で比べる）。どれも同じなら extra。
func differing(a, b *Template) []string {
	same := []bool{
		a.Name == b.Name, a.Description == b.Description, a.When == b.When,
		slices.EqualFunc(a.Plan, b.Plan, Item.equal), slices.EqualFunc(a.Feedback, b.Feedback, Item.equal),
		yaml.Equal(a.flowsData(), b.flowsData()),
	}
	var out []string
	for i, ok := range same {
		if !ok {
			out = append(out, Fields[i])
		}
	}
	if len(out) == 0 {
		return []string{fieldExtra}
	}
	return out
}

// CommonWritable は共通レイヤーの置き場を書き換えてよいか。着手済みの親のワークツリーの中の共通レイヤーは
// 同期が配ったミラーで、誰も編集しないので読み取り専用。
func CommonWritable(conf *config.Settings, root string) bool {
	where, ok := tree.TreeOf(root, root, conf.Projects)
	return !ok || where.IsMain() || where.Project == ""
}

// TypesFor はそのレイヤーの検査に使うフェーズ定義（共通 + レイヤー）。共通は共通だけ。
func TypesFor(conf *config.Settings, root, layer string) *phases.Types {
	if layer == config.LayerCommon {
		types, _ := phases.CommonTypes(conf)
		return types
	}
	project := layer
	if layer == config.LayerSelf {
		project = ""
	}
	types, _ := phases.LayerTypes(conf, root, project)
	return types
}

// Recheck は別のフェーズ定義（統合結果）で検査をやり直した写し。読むときの苦情は残す。
func Recheck(e *Entry, types *phases.Types, cat *flow.Catalog) *Entry {
	if e.Template == nil || !e.Effective {
		return e
	}
	again := *e
	again.Checks = Check(e.Template, types, cat)
	return &again
}

// Load は共通と全レイヤーのテンプレートを読んで検査する。各 Entry の検査はそのレイヤーのフェーズ定義で行い、
// 統合結果の印はレイヤーの側に付く。読むのは設定の置き場だけで、承認済みチケットの置き場は読まない。
func Load(conf *config.Settings, root string) *Scan {
	cat := flow.LoadCatalog(root)
	common := ReadLayer(config.LayerCommon, root, conf.CommonTemplatesDir(root), CommonWritable(conf, root))
	commonTypes := TypesFor(conf, root, config.LayerCommon)
	for _, e := range common.Entries {
		if e.Template != nil {
			e.Checks = append(e.Checks, Check(e.Template, commonTypes, &cat)...)
		}
	}
	out := &Scan{Root: root, Common: common, Catalog: cat}
	for _, view := range ruleload.Layers(conf, root) {
		l := ReadLayer(view.Name, root, conf.LayerTemplatesDir(view.Home), true)
		Merge(common, l)
		types := TypesFor(conf, root, view.Name)
		for _, e := range l.Entries {
			if e.Template != nil && e.Effective {
				e.Checks = append(e.Checks, Check(e.Template, types, &cat)...)
			}
		}
		out.Layers = append(out.Layers, l)
	}
	return out
}

// Adopted は layer を足したときの、取り込まれた・破棄した全部（共通が先、名前順）。共通の側を統合結果の定義で
// 検査し直す（Recheck）のは呼び手。
func (s *Scan) Adopted(layer string) []*Entry {
	view := s.Layer(layer)
	if view == nil {
		return nil
	}
	return append(slices.Clone(s.Common.Entries), view.Entries...)
}

// LayerProblem は苦情とそのレイヤー。
type LayerProblem struct {
	Layer   string
	Problem rules.Problem
}

// Problems は全部の苦情（--lint が使う）。
func (s *Scan) Problems() []LayerProblem {
	var out []LayerProblem
	for _, view := range s.all() {
		for _, p := range view.Problems {
			out = append(out, LayerProblem{view.Name, p})
		}
		for _, e := range view.Entries {
			for _, p := range e.Problems() {
				out = append(out, LayerProblem{view.Name, p})
			}
		}
	}
	return out
}

// HasAny は置き場がどこかに在るか。
func (s *Scan) HasAny() bool {
	return slices.ContainsFunc(s.all(), func(l *LayerRead) bool { return l.Exists })
}

// Find は名前の Entry。取り込まれたものを先に、破棄したものは最後に探す。
func (s *Scan) Find(layer, name string) *Entry {
	var found []*Entry
	for _, e := range s.Adopted(layer) {
		if e.Name == name {
			found = append(found, e)
		}
	}
	slices.SortStableFunc(found, func(a, b *Entry) int { return boolInt(!a.Effective) - boolInt(!b.Effective) })
	if len(found) == 0 {
		return nil
	}
	return found[0]
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// Matches は name・description・when のどれかに語のどれかが含まれるか（大文字小文字は問わない）。
func Matches(e *Entry, keywords []string) bool {
	if len(keywords) == 0 {
		return true
	}
	parts := []string{e.Name, "", ""}
	if e.Template != nil {
		parts[1], parts[2] = e.Template.Description, e.Template.When
	}
	haystack := pyfmt.Casefold(strings.Join(parts, " "))
	for _, k := range keywords {
		if k != "" && strings.Contains(haystack, pyfmt.Casefold(k)) {
			return true
		}
	}
	return false
}
