package ruleload

import (
	"fmt"
	"io"
	"path/filepath"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/builtin"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/audit"
)

// stopEveryLeast は、ターンの終わりのルールに使う every の下限。1 だとターンの終わりのたびに止まる（lint も言う）。
const stopEveryLeast = 2

// unreadableNote は読めないレイヤーを記録の detail に残すときの前置き。
const unreadableNote = "unreadable layer: "

// Layer は共通レイヤーより後ろのレイヤー 1 つ。Name は記録と id の前置きに使う名前（self かプロジェクトの名前）。
// Home はそのレイヤーの git プロジェクトルート、Path はその rules.yml（無くてよい。無ければ空として扱う）。
type Layer struct {
	Name string
	Home string
	Path string
}

// readCommon は共通レイヤーのルールファイルだけを読む（土台の deny は足さない）。無いのは正常で、空として扱う。
// 読めなければ組み込みの既定に戻し、記録の Fallback に残す。拒否にすると、壊れたファイルを直す呼び出しまで止まり、
// 回復できなくなるためである。
func readCommon(stderr io.Writer, conf *config.Settings, record *audit.Record, root string) *rules.RuleSet {
	path := conf.Rules
	rs := &rules.RuleSet{Version: rules.Version}
	var problems []rules.Problem
	if ospath.DiskExists(path) {
		loaded, found, err := rules.Load(path, root)
		if err != nil {
			fmt.Fprintf(stderr, "ccnavi: ルールを読めない: %s\n", err)
			loaded, found = builtin.Load(root, conf)
			record.Fallback = builtin.Fallback
			record.Detail = path
		} else {
			markFile(loaded, RelPath(path, root))
		}
		rs, problems = loaded, found
	}
	for _, p := range problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", p)
	}
	markSource(rs, config.LayerCommon)
	return rs
}

// LoadRules は共通レイヤーのルール集合（土台の deny つき）と、その出所を返す。既定に戻ったときは、既定が同じ
// deny を持つので土台は足さない。出所は既定を使っているなら builtin.Source にする。読めなかったファイルを出所として
// 出すと、見に行ったユーザが適用されたルールを見つけられないためである。
func LoadRules(stderr io.Writer, conf *config.Settings, record *audit.Record, root string) (*rules.RuleSet, string) {
	rs := readCommon(stderr, conf, record, root)
	if record.Fallback != builtin.Fallback {
		addBase(rs, stderr, root)
	}
	if record.Fallback != "" {
		return rs, builtin.Source
	}
	return rs, conf.Rules
}

// addBase は土台の deny を、共通レイヤーのルールの後ろ・レイヤーより前に足す。
func addBase(rs *rules.RuleSet, stderr io.Writer, root string) {
	base, problems := builtin.LoadBase(root)
	for _, p := range problems {
		fmt.Fprintf(stderr, "ccnavi: %s\n", p)
	}
	markSource(base, config.LayerCommon)
	rs.Deny = append(rs.Deny, base.Deny...)
}

// bare は、共通レイヤーも足すレイヤーもルールのファイルを 1 本も持たないか（組み込みの既定だけの形）を返す。
// 共通レイヤーが壊れて既定に戻っているときは該当しない。
func bare(conf *config.Settings, record *audit.Record, chosen []Layer) bool {
	if record.Fallback == builtin.Fallback || ospath.DiskExists(conf.Rules) {
		return false
	}
	for _, l := range chosen {
		if ospath.DiskExists(l.Path) {
			return false
		}
	}
	return true
}

// addReadAllow は Read を通す組み込みの allow を足す。bare のときだけ呼ぶ。
func addReadAllow(rs *rules.RuleSet) {
	allow := builtin.ReadAllow()
	for _, r := range allow {
		r.Source = config.LayerCommon
	}
	rs.Allow = append(rs.Allow, allow...)
}

// Layers は共通レイヤーより後ろのレイヤーを、足す順に並べて返す（自身のレイヤーが先、プロジェクトは名前順）。
// 予約名のプロジェクト（`projects/self/` など）は数えない。行き先を引く LayerFor も同じ予約を見るので、
// 数えない名前で別のレイヤーの判定を引くことはない。
func Layers(conf *config.Settings, root string) []Layer {
	found := []Layer{{config.LayerSelf, root, conf.LayerPath(root, config.KindRules, config.LayerSelf)}}
	for _, p := range tree.Projects(conf.Projects) {
		if config.IsReservedLayerName(p.Name) {
			continue
		}
		home := tree.ProjectRoot(conf.Projects, p.Name)
		found = append(found, Layer{p.Name, home, conf.LayerPath(home, config.KindRules, p.Name)})
	}
	return found
}

// Unreadable は、ルールのファイルがあるのに読めないレイヤーの名前を返す。読めないレイヤーは空として扱われ、その deny は
// 黙って効かなくなる。そのため、判定のたびではなく、セッションの開始のたびに言うために使う。
func Unreadable(conf *config.Settings, root string) []string {
	var broken []string
	for _, l := range Layers(conf, root) {
		if !ospath.DiskExists(l.Path) {
			continue
		}
		if _, _, err := rules.Load(l.Path, root); err != nil {
			broken = append(broken, l.Name)
		}
	}
	return broken
}

// LayerFor は、このツリーに足すレイヤーを返す（書き込み系は行き先の 1 つだけ）。ワークスペースのツリーなら自身の
// レイヤー、プロジェクトのツリーならそのレイヤー、ワークスペースルートの外（nil）なら無し。
// 行き先が予約名のプロジェクトなら無し。名前引きに任せると、`projects/self/` への書き込みが自身のレイヤーの
// ルール（広い allow があればそれ）で判定されてしまう。無しなら共通レイヤーだけで判定するので、緩む側にならない。
func LayerFor(conf *config.Settings, root string, target *tree.Tree) []Layer {
	if target == nil || config.IsReservedLayerName(target.Project) {
		return nil
	}
	name := target.Project
	if name == "" {
		name = config.LayerSelf
	}
	var out []Layer
	for _, l := range Layers(conf, root) {
		if pathcase.Fold(l.Name) == pathcase.Fold(name) {
			out = append(out, l)
		}
	}
	return out
}

// RulesFor は、この呼び出しに適用するルール集合、その出所、行き先のツリーを返す。パスを持たないツールと
// ワークスペースルートの外は、行き先を nil にする。パスを持つツールは、共通レイヤーに行き先のレイヤーを足す。
// 持たないツールは全部のレイヤーの和を使う。どのプロジェクトのものかは特定しない。和なら deny と ask は増える側になる。
// record.Subject は解決済みの行き先のパス。
func RulesFor(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record) (*rules.RuleSet, string, *tree.Tree) {
	byPath := isPathTool(in.ToolName)
	var target *tree.Tree
	if byPath {
		if t, ok := tree.TreeOf(root, record.Subject, conf.Projects); ok {
			target = &t
		}
	}
	rs, source := LoadRules(stderr, conf, record, root)
	if record.Fallback == builtin.Fallback {
		// 壊れた共通レイヤーの上にレイヤーを足しても、何が判定に使われているのかをユーザが読めない。
		return rs, source, target
	}
	chosen := Layers(conf, root)
	if byPath {
		chosen = LayerFor(conf, root, target)
	}
	AddLayers(stderr, rs, chosen, root, record)
	if bare(conf, record, chosen) {
		addReadAllow(rs)
	}
	return rs, source, target
}

func isPathTool(name string) bool {
	for _, t := range config.PathTools {
		if t == name {
			return true
		}
	}
	return false
}

// AddLayers は共通レイヤーの上にレイヤーを順に足す。壊れたレイヤーは空として扱い、記録の Fallback と Detail に
// 残す。レイヤーをまたいだ苦情（重複の info、同 id の warn）は捨てる。判定のたびに言うと、同じ話が毎回モデルへ
// 届いてしまう。言う場所は lint である。
func AddLayers(stderr io.Writer, rs *rules.RuleSet, chosen []Layer, root string, record *audit.Record) {
	var broken []string
	for _, l := range chosen {
		extra, ok := readLayer(stderr, l, root)
		if !ok {
			if ospath.DiskExists(l.Path) {
				broken = append(broken, l.Name)
			}
			continue
		}
		merge(rs, extra, l.Name)
	}
	if len(broken) > 0 {
		record.Fallback = strings.Join(broken, ",")
		note := unreadableNote + record.Fallback
		if record.Detail != "" {
			note = record.Detail + "; " + note
		}
		record.Detail = note
	}
}

// readLayer はレイヤーのルールを読み、記録名の相対パスと id の前置きを付ける。無い・読めないときは ok が偽になる。
// 読めないときと苦情は stderr に言う。
func readLayer(stderr io.Writer, l Layer, root string) (*rules.RuleSet, bool) {
	if !ospath.DiskExists(l.Path) {
		return nil, false
	}
	extra, notes, err := rules.Load(l.Path, root)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: レイヤー %s のルールを読めない: %s\n", l.Name, err)
		return nil, false
	}
	for _, n := range notes {
		fmt.Fprintf(stderr, "ccnavi: %s\n", n)
	}
	markFile(extra, RelPath(l.Path, root))
	prefixIDs(extra, l.Name)
	return extra, true
}

// merge は後ろのレイヤーを前の集合に足す。裸の id が同じで、{root} 置換後の全欄が一致する定義は重複として
// 後ろを捨てる（info。見本を写して始めたプロジェクトには普通の形）。同 id で中身が違えば両方残す（warn）。
func merge(base, extra *rules.RuleSet, layer string) []rules.Problem {
	var problems []rules.Problem
	keys := map[string]bool{}
	ids := map[string]bool{}
	for _, r := range base.All() {
		keys[r.Key()] = true
		if r.BareID != "" {
			ids[r.BareID] = true
		}
	}
	for _, name := range rules.Sections {
		for _, r := range *extra.Section(name) {
			if keys[r.Key()] {
				problems = append(problems, rules.Problem{Severity: rules.Info, Rule: r.ID, Detail: fmt.Sprintf(
					"`%s` は前のレイヤーと全欄が同じなので、%s の側を破棄した。判定は前のレイヤーの 1 本で行う", r.BareID, layer)})
				continue
			}
			if ids[r.BareID] {
				problems = append(problems, rules.Problem{Severity: rules.Warn, Rule: r.ID, Detail: fmt.Sprintf(
					"`%s` は前のレイヤーと同じ id で中身が違う。両方が判定に使われる。"+
						"同じ名前で違うものを指していると、記録を読んだユーザがどちらの話か決められない", r.BareID)})
			}
			keys[r.Key()] = true
			ids[r.BareID] = true
			section := base.Section(name)
			*section = append(*section, r)
		}
	}
	return problems
}

// LayerView は診断が見るレイヤー 1 つ。RuleSet は、実際に判定へ入ったぶんを持つ（重複で捨てた定義は入らない）。
// Unreadable は読めなかった理由（空なら読めた）。Missing はファイルが無いこと（不備ではない）。Problems は
// レイヤーの読みの苦情と、レイヤーをまたいだ苦情。
type LayerView struct {
	Name       string
	Path       string
	RuleSet    *rules.RuleSet
	Unreadable string
	Missing    bool
	Problems   []rules.Problem
}

// commonView は診断のために共通レイヤーを読み、重ねる先（共通レイヤーのルールを写した集合）を作る。
// 既定に戻っていれば unreadable が読めなかったもの。
func commonView(stderr io.Writer, conf *config.Settings, root string) (common, merged *rules.RuleSet, unreadable string) {
	record := &audit.Record{}
	common = readCommon(stderr, conf, record, root)
	merged = &rules.RuleSet{Version: common.Version}
	for _, name := range rules.Sections {
		*merged.Section(name) = append(*merged.Section(name), *common.Section(name)...)
	}
	if record.Fallback == builtin.Fallback {
		unreadable = record.Detail
		if unreadable == "" {
			unreadable = conf.Rules
		}
	}
	return common, merged, unreadable
}

// overlay は 1 つのレイヤーを読んで merged に重ね、view に結果を書く。重ねて増えた分を返す。
func overlay(view *LayerView, merged *rules.RuleSet, l Layer, root string) *rules.RuleSet {
	added := &rules.RuleSet{Version: rules.Version}
	if !ospath.DiskExists(l.Path) {
		view.Missing = true
		return added
	}
	extra, notes, err := rules.Load(l.Path, root)
	if err != nil {
		view.Unreadable = err.Error()
		return added
	}
	view.Problems = append(view.Problems, notes...)
	markFile(extra, RelPath(l.Path, root))
	prefixIDs(extra, l.Name)
	before := map[rules.Decision]int{}
	for _, name := range rules.Sections {
		before[name] = len(*merged.Section(name))
	}
	view.Problems = append(view.Problems, merge(merged, extra, l.Name)...)
	for _, name := range rules.Sections {
		*added.Section(name) = append(*added.Section(name), (*merged.Section(name))[before[name]:]...)
	}
	return added
}

// Survey は共通レイヤーと各レイヤーを、判定と同じ順・同じ読み方で読む（--explain・--lint）。並べたものは Bash の
// 和と一致する（重ね方は判定と同じ merge の 1 本）。共通レイヤーが壊れているときは、レイヤーを足さない。
func Survey(stderr io.Writer, conf *config.Settings, root string) []LayerView {
	common, merged, unreadable := commonView(stderr, conf, root)
	views := []LayerView{{Name: config.LayerCommon, Path: conf.Rules, RuleSet: common, Unreadable: unreadable}}
	if unreadable == "" && !ospath.DiskExists(conf.Rules) {
		views[0].Missing = true
	}
	for _, l := range Layers(conf, root) {
		view := LayerView{Name: l.Name, Path: l.Path, RuleSet: &rules.RuleSet{Version: rules.Version}}
		switch {
		case !ospath.DiskExists(l.Path):
			view.Missing = true
		case unreadable != "":
			// 共通レイヤーが壊れているときはレイヤーを足さない。診断もそう見せる。
		default:
			view.RuleSet = overlay(&view, merged, l, root)
		}
		views = append(views, view)
	}
	return views
}

// SumView は、共通レイヤーに 1 つのレイヤーを足した和を、判定と同じ読み方で読む（診断用）。Survey は全レイヤーを
// 順に重ねるので、後ろのレイヤーの欄は先のレイヤーの定義に引かれて欠ける。SumView は、パスを持つツールの判定と
// 同じ和を返す（RuleSet は共通レイヤーを含む。土台の deny は含まない）。
func SumView(stderr io.Writer, conf *config.Settings, root string, l Layer) LayerView {
	_, merged, unreadable := commonView(stderr, conf, root)
	view := LayerView{Name: l.Name, Path: l.Path, RuleSet: merged, Unreadable: unreadable}
	if unreadable != "" {
		return view
	}
	overlay(&view, merged, l, root)
	return view
}

// LayerFiles は、守る対象（selfguard）に渡すレイヤーごとの設定ファイルを返す。共通レイヤーは phases と risk だけ
// （rules は守る側が別に受け取る）。自身とプロジェクトのレイヤーは本来の置き場の 3 本を渡す（差し替えは診断のため）。
// プロジェクトはさらに、共通レイヤーのミラーの 3 本も渡す。Origin で分けるのは、`projects/common/` の名前で比べると
// 共通レイヤーと同じ key になって外れるためである。予約名のプロジェクトも並べる。判定に使わないものを守るだけで、
// 緩む側にはならない。在るかどうかは見ない（無いものは守る側が外す）。
func LayerFiles(conf *config.Settings, root string) []config.LayerFile {
	found := []config.LayerFile{
		{Origin: config.OriginCommon, Layer: config.LayerCommon, Kind: config.KindPhases, Path: conf.Phases},
		{Origin: config.OriginCommon, Layer: config.LayerCommon, Kind: config.KindRisk, Path: conf.Risk},
	}
	type home struct {
		origin config.Origin
		name   string
		root   string
	}
	homes := []home{{config.OriginSelf, config.LayerSelf, root}}
	for _, p := range tree.Projects(conf.Projects) {
		homes = append(homes, home{config.OriginProject, p.Name, tree.ProjectRoot(conf.Projects, p.Name)})
	}
	for _, h := range homes {
		for _, kind := range config.LayerKinds {
			found = append(found, config.LayerFile{Origin: h.origin, Layer: h.name, Kind: kind, Path: conf.LayerRealPath(h.root, kind)})
		}
		if h.origin == config.OriginProject {
			for _, kind := range config.LayerKinds {
				found = append(found, config.LayerFile{Origin: config.OriginMirror, Layer: h.name, Kind: kind, Path: conf.MirrorRealPath(h.root, kind)})
			}
		}
	}
	return found
}

func markSource(rs *rules.RuleSet, layer string) {
	for _, r := range rs.All() {
		r.Source = layer
	}
}

// RelPath はルールファイルのパスを、ワークスペースのルートからの相対パス（"/" 区切り）にする。記録名の前半になる。
// root が空なら作業ディレクトリを基準にする。相対にできないとき（Windows の別のドライブ）は、絶対パスのまま返す。
func RelPath(path, root string) string {
	if root == "" {
		root = "."
	}
	rel, ok := ospath.Relpath(path, root)
	if !ok {
		rel = ospath.Abspath(path)
	}
	return filepath.ToSlash(rel)
}

func markFile(rs *rules.RuleSet, rel string) {
	for _, r := range rs.All() {
		r.File = rel
	}
}

// prefixIDs はレイヤーのルールの id にレイヤーの名前を付ける（`self:docs`）。レイヤーどうしで同じ id があっても、
// 記録の上で衝突しない。id だけで、見に行くファイルが分かる。共通レイヤーは裸の id のままにする。
func prefixIDs(rs *rules.RuleSet, layer string) {
	markSource(rs, layer)
	for _, r := range rs.All() {
		r.ID = layer + rules.IDSeparator + r.ID
	}
}

// workspaceRules は、共通レイヤーとワークスペース自身のレイヤーの和を返す。判定の経路ではない場面（ターンの終わり、
// セッションの開始）で、ワークスペースが何を宣言しているかを見るためのものである。苦情は捨て、記録にも残さない。
// 共通レイヤーが壊れて既定に戻ったときは、レイヤーを足さない（判定と同じ）。
func workspaceRules(conf *config.Settings, root string) *rules.RuleSet {
	rs, source := LoadRules(io.Discard, conf, &audit.Record{}, root)
	if source != builtin.Source {
		var own []Layer
		for _, l := range Layers(conf, root) {
			if l.Name == config.LayerSelf {
				own = append(own, l)
			}
		}
		AddLayers(io.Discard, rs, own, root, &audit.Record{})
	}
	return rs
}

// StopRules は、ターンの終わりに適用するルールを返す。共通レイヤーとワークスペース自身のレイヤーの allow だけを使い、
// プロジェクトのレイヤーは見ない（同期で配ったコピーを二重に数えないため）。同じ id は最初の 1 本だけにする。
// every が 2 より小さいものは使わない。
func StopRules(conf *config.Settings, root string) []*rules.Rule {
	var picked []*rules.Rule
	seen := map[string]bool{}
	for _, r := range workspaceRules(conf, root).Allow {
		if r.Every < stopEveryLeast || !r.Matches(rules.StopMatch, rules.StopSubject) {
			continue
		}
		key := r.BareID
		if key == "" {
			key = r.ID
		}
		if key != "" && seen[key] {
			continue
		}
		seen[key] = true
		picked = append(picked, r)
	}
	return picked
}

// DenyIDs は、共通レイヤーとワークスペース自身のレイヤーにある deny の id（書かれたままの表記）を返す。
// セッション開始の作業の決まりが、その決まりを支えるルールをワークスペースが置いているときだけ行を出すために使う。
func DenyIDs(conf *config.Settings, root string) map[string]bool {
	ids := map[string]bool{}
	for _, r := range workspaceRules(conf, root).Deny {
		if r.ID == "" {
			continue
		}
		if r.BareID != "" {
			ids[r.BareID] = true
		} else {
			ids[r.ID] = true
		}
	}
	return ids
}
