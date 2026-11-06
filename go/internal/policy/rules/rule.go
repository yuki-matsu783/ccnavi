package rules

import (
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
)

// Decision はルールが置かれたタイプ。強い順に Deny・Ask・Allow。
type Decision string

const (
	Deny  Decision = "deny"
	Ask   Decision = "ask"
	Allow Decision = "allow"
)

// Sections はタイプを強い順に並べたもの。判定もこの順に見る。
var Sections = []Decision{Deny, Ask, Allow}

// Version はこのビルドが読めるルールファイルの書式の版。
const Version = 1

const (
	// StopMatch は match に書ける、ツールではない名前。メインのターンの終わりに当たる（見るのは allow だけ）。
	StopMatch = string(hookio.Stop)
	// StopSubject は StopMatch のルールを当てる固定の文字列。
	StopSubject = "(stop)"
	// IDSeparator はレイヤーの名前と id の間の字（`self:docs`）。書いた id には使えない。
	IDSeparator = ":"
	// ReservedIDPrefix は組み込みのルールの名前の頭。ルールファイルには書けない。
	ReservedIDPrefix = "builtin:"
	// EveryDefault は every を書かなかったときの刻み（当たるたびに渡す）。
	EveryDefault = 1
	// DegradedRuleName は組み立てられずに止める側へ倒したルールの文面の中の、ルールの名前の差し込み。
	DegradedRuleName = "{rule}"
)

// 組み合わせ条件（when）と除く（except）のキー。
const (
	ExceptKey = "except"
	WhenKey   = "when"
	// MaxWhenDepth と MaxWhenNodes は when の入れ子の段数とノードの数の上限（判定を重くしない）。
	MaxWhenDepth = 8
	MaxWhenNodes = 64
)

// WhenKind は when のノードの種類。Glob と Regex は個別条件で、except の要素と同じ形。
type WhenKind string

const (
	WhenGlob  WhenKind = "glob"
	WhenRegex WhenKind = "regex"
	WhenAll   WhenKind = "all"
	WhenAny   WhenKind = "any"
	WhenNot   WhenKind = "not"
)

// whenKinds は when のノードに書けるキー（並びは苦情の文の順）。
var whenKinds = []WhenKind{WhenGlob, WhenRegex, WhenAll, WhenAny, WhenNot}

// TextRef は組み込みのルールが持つ文面ファイルの指定（識別子・差し込む値・場合）。文は止めた回に texts が引く。
// hook は呼び出しごとにルールを組み立てるので、通る呼び出しに文面ファイルの読みを乗せないためである。
type TextRef struct {
	ID       string
	Values   map[string]string
	Variants map[string]string
}

// Exclusion は except の 1 件。本体と同じ読み方で組み立てた式を持つ。
type Exclusion struct {
	Glob     string
	Regex    string
	Compiled *pyre.Regexp
}

// Written は書いた表記に戻した形（{"glob": …} か {"regex": …}）。
func (e Exclusion) Written() *pyjson.Object {
	if e.Glob != "" {
		return pyjson.NewObject(string(WhenGlob), e.Glob)
	}
	return pyjson.NewObject(string(WhenRegex), e.Regex)
}

// Condition は when のノード 1 つ。Where は苦情と lint で名指しする位置（`when.any[1].not`）。
// 比べるとき（Key）と書き戻し（Written）には入れない。
type Condition struct {
	Kind     WhenKind
	Text     string
	Children []*Condition
	Compiled *pyre.Regexp
	Where    string
}

// IsLeaf は個別条件（glob・regex）か。
func (c *Condition) IsLeaf() bool { return c.Kind == WhenGlob || c.Kind == WhenRegex }

// Hits は subject に当たるか。
func (c *Condition) Hits(subject string) bool {
	switch c.Kind {
	case WhenGlob, WhenRegex:
		return c.Compiled != nil && c.Compiled.Search(subject)
	case WhenAll:
		for _, child := range c.Children {
			if !child.Hits(subject) {
				return false
			}
		}
		return true
	case WhenAny:
		for _, child := range c.Children {
			if child.Hits(subject) {
				return true
			}
		}
		return false
	}
	return !c.Children[0].Hits(subject)
}

// Written は書いた表記に戻した形。
func (c *Condition) Written() *pyjson.Object {
	if c.IsLeaf() {
		return pyjson.NewObject(string(c.Kind), c.Text)
	}
	if c.Kind == WhenNot {
		return pyjson.NewObject(string(WhenNot), c.Children[0].Written())
	}
	items := make([]any, len(c.Children))
	for i, child := range c.Children {
		items[i] = child.Written()
	}
	return pyjson.NewObject(string(c.Kind), items)
}

// Key は比べるための形。個別条件は {root} を置き換えたあとの式で比べる（Rule.Key と同じ）。
func (c *Condition) Key() string {
	if c.IsLeaf() {
		text := c.Text
		if c.Compiled != nil {
			text = c.Compiled.Pattern()
		}
		return string(c.Kind) + "(" + strconv.Quote(text) + ")"
	}
	parts := make([]string, len(c.Children))
	for i, child := range c.Children {
		parts[i] = child.Key()
	}
	return string(c.Kind) + "(" + strings.Join(parts, ",") + ")"
}

// Describe は 1 行で読める形。`any(glob '*.pem', all(glob '*/secrets/*', not(glob '*.md')))`。
func (c *Condition) Describe() string {
	if c.IsLeaf() {
		return string(c.Kind) + " '" + c.Text + "'"
	}
	parts := make([]string, len(c.Children))
	for i, child := range c.Children {
		parts[i] = child.Describe()
	}
	return string(c.Kind) + "(" + strings.Join(parts, ", ") + ")"
}

// Lines は字下げで読める形。1 ノード 1 行。
func (c *Condition) Lines(indent string) []string {
	if c.IsLeaf() {
		return []string{indent + c.Describe()}
	}
	out := []string{indent + string(c.Kind)}
	for _, child := range c.Children {
		out = append(out, child.Lines(indent+"  ")...)
	}
	return out
}

// Walk は自分と、その下のノードを上から順に。
func (c *Condition) Walk() []*Condition {
	out := []*Condition{c}
	for _, child := range c.Children {
		out = append(out, child.Walk()...)
	}
	return out
}

// Rule は探すものと、見つけたときに言うことの組。
type Rule struct {
	// ID は報告で名指しする名前。レイヤーの和では `lib:schema` のように前にレイヤーの名前が付く。
	ID string
	// BareID は書かれたままの id。レイヤーをまたいで同じルールかを見るキー（ID から前置きを外して
	// 求めると、`:` を含む id を書いたユーザの定義が外される側になる）。
	BareID string
	// Source はルールが来たレイヤー（common・self・プロジェクトの名前）。
	Source string
	// Match は対象のツール名を "|" で並べたもの。
	Match string
	Glob  string
	Regex string
	// Message は止めた理由と代わりの手段。組み込みのルールは代わりに Text を持つ。
	Message string
	Text    TextRef
	// AdditionalContext 系は当たったときにモデルへ渡す文（ctxfile が並べる）。
	AdditionalContext         string
	AdditionalContextOnce     string
	AdditionalContextFile     string
	AdditionalContextOnceFile string
	// Every は渡す回の刻み。EveryWritten は書かれたままの値（無ければ nil）で、読めない値でも
	// ルールは組み上げ、Every は 1 にする（指摘は lint の仕事で、id で名指しできるように）。
	Every        int
	EveryWritten any
	// Base は設定に依らず常に当たる組み込みの deny（土台）。同じ呼び出しがレイヤーの deny にも当たれば、
	// 文面と記録はそちらに載せる。
	Base     bool
	Decision Decision
	// Root は読んだときのワークスペースルート。文面の {root} をモデルへ渡すときに置き換える先。
	Root string
	// FromFile はルールファイルから読んだルール（組み込みは偽）。
	FromFile bool
	// File はルールファイルの、ワークスペースのルートからの相対パス（"/" 区切り）。読んだ側が入れる。
	File string
	// DegradedMessage は、書かれたとおりに組み立てられず止める側へ倒したときに返す文面
	// （書いた Message を上書きすると、書いたユーザの言葉が消える）。
	DegradedMessage string
	// Compiled は本体の式。when のルールではふつう nil。
	Compiled *pyre.Regexp
	// Exempt は式を当てる前に対象から外すものを決める（組み込みの保護だけが持つ）。返すのは式を当てる対象。
	Exempt func(string) string
	// Excepts は except の各パターン。本体に当たっても、どれかに当たればこのルールは当たらない。
	Excepts []Exclusion
	// When は when で書いた本体。
	When *Condition
}

// Built は判定に使える本体を持つか。
func (r *Rule) Built() bool { return r.Compiled != nil || r.When != nil }

// WrittenBody は本体を書いた表記で 1 行に。
func (r *Rule) WrittenBody() string {
	if r.When != nil {
		return WhenKey + ": " + r.When.Describe()
	}
	if r.Glob != "" {
		return r.Glob
	}
	return r.Regex
}

// SpokenMessage はモデルへ渡す文面（{root} と {script:…} を置き換えたもの）。止める側へ倒したルールは、
// その説明を先に返す。文面ファイルを引く組み込みのルールの文は texts.Spoken が組む。
func (r *Rule) SpokenMessage() string {
	if r.DegradedMessage != "" {
		return FillText(strings.ReplaceAll(r.DegradedMessage, DegradedRuleName, RecordName(r)), r.Root)
	}
	return FillText(r.Message, r.Root)
}

// Key はレイヤーをまたいで「同じ定義」と言えるかのキー。式は {root} を置き換えたあとで比べる。
func (r *Rule) Key() string {
	pattern := func(re *pyre.Regexp) string {
		if re == nil {
			return ""
		}
		return re.Pattern()
	}
	when := ""
	if r.When != nil {
		when = r.When.Key()
	}
	excepts := make([]string, len(r.Excepts))
	for i, e := range r.Excepts {
		excepts[i] = strconv.Quote(pattern(e.Compiled))
	}
	parts := []string{
		r.BareID, r.Match, pattern(r.Compiled), when, strings.Join(excepts, ","), r.Message,
		r.Text.ID, sortedItems(r.Text.Values), sortedItems(r.Text.Variants),
		r.AdditionalContext, r.AdditionalContextOnce, r.AdditionalContextFile, r.AdditionalContextOnceFile,
		strconv.Itoa(r.Every), string(r.Decision),
	}
	for i, p := range parts {
		parts[i] = strconv.Quote(p)
	}
	return strings.Join(parts, "\x00")
}

func sortedItems(m map[string]string) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(strconv.Quote(k) + "=" + strconv.Quote(m[k]) + ";")
	}
	return b.String()
}

// Matches はこのルールがこのツールと対象に当たるか。
func (r *Rule) Matches(tool, subject string) bool {
	if !r.Built() || subject == "" || !ToolMatches(r.Match, tool) {
		return false
	}
	if r.Exempt != nil {
		subject = r.Exempt(subject)
	}
	return r.Hits(subject)
}

// Hits はツール名を見ずに、対象だけで当たるか（本体に当たり、except のどれにも当たらない）。
func (r *Rule) Hits(subject string) bool {
	if !r.BodyHits(subject) {
		return false
	}
	for _, e := range r.Excepts {
		if e.Compiled != nil && e.Compiled.Search(subject) {
			return false
		}
	}
	return true
}

// BodyHits は本体（glob・regex・when）だけで当たるか。
func (r *Rule) BodyHits(subject string) bool {
	if r.Compiled != nil {
		return r.Compiled.Search(subject)
	}
	return r.When != nil && r.When.Hits(subject)
}

// RecordName は記録の rules 欄と文面に出す名前。ルールファイル由来は `<相対パス>:<書いた id>`、
// 組み込みは id そのもの。
func RecordName(r *Rule) string {
	if r.FromFile {
		return r.File + IDSeparator + r.BareID
	}
	return r.ID
}

// matchSep は match のツール名の区切り。
const matchSep = "|"

// MatchOf はツール名を match の 1 本（"|" 区切り）にする。
func MatchOf(tools ...string) string { return strings.Join(tools, matchSep) }

// ToolMatches は match（"|" 区切り）がこのツール名を含むか。大文字小文字を区別した完全一致。
func ToolMatches(match, tool string) bool {
	for _, want := range strings.Split(match, matchSep) {
		if ospath.Strip(want) == tool {
			return true
		}
	}
	return false
}

// RuleSet はルールファイル 1 本ぶん。タイプごとに分けて持つ（強い順に見るのがリストの順そのものになる）。
type RuleSet struct {
	Version int
	Deny    []*Rule
	Ask     []*Rule
	Allow   []*Rule
}

// Section はタイプのリスト。足す・挿すときはこのポインタを通す。
func (s *RuleSet) Section(d Decision) *[]*Rule {
	switch d {
	case Deny:
		return &s.Deny
	case Ask:
		return &s.Ask
	}
	return &s.Allow
}

// All は全タイプを強い順に。
func (s *RuleSet) All() []*Rule {
	out := make([]*Rule, 0, len(s.Deny)+len(s.Ask)+len(s.Allow))
	out = append(out, s.Deny...)
	out = append(out, s.Ask...)
	return append(out, s.Allow...)
}
