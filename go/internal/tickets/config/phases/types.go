package phases

import (
	"errors"
	"fmt"
	"io/fs"
	"iter"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/ticket"
)

// Version は phases.yml の版。
const Version = 1

// Kind はフェーズ定義の種類。
type Kind string

const (
	Work     Kind = "work"
	Feedback Kind = "feedback"
)

var kinds = []Kind{Work, Feedback}

// Learning は学びの蓄積（learning-wiki）をこの定義の子に効かせるか。Off の子は案内と閉じる検査を受けない。
type Learning string

const (
	LearningOn  Learning = "on"
	LearningOff Learning = "off"
)

var learnings = []Learning{LearningOn, LearningOff}

const (
	// inherit は定義の範囲が「親の範囲そのまま」であることを言う表記。
	inherit = "inherit"
	// maxEntries は定義の scope と deliverables の件数の上限。定義は親の phases: にコピーするので、ここで
	// 縛らないと親の大きさが縛れない。レビューしきれない数を並べて広い範囲を紛れ込ませる手口も防ぐ。
	maxEntries = ticket.MaxScopeEntries
	// Where は phases.yml の苦情の出どころ（ファイル全体への苦情の rule）。
	Where = "(phases)"
)

// PhaseType はフェーズ定義 1 つ。フェーズは親の計画に並ぶ番号付きの実体で、子が phase: N で指す。
type PhaseType struct {
	ID    string
	Title string
	Kind  Kind
	// Review はこのフェーズの終わりにユーザのレビュー（マージリクエスト）が要るか。
	Review bool
	// Inherit は範囲が親の範囲そのまま（inherit か無し）。偽なら Scope が上限（空なら何も書けない）。
	Inherit      bool
	Scope        []ticket.Entry
	ScopeGlobs   []string
	Deliverables []string
	Agent        string
	// Skill は子が最初に呼ぶ工程スキルの名前。案内にだけ使う。
	Skill    string
	When     string
	Learning Learning
	// Source はこの定義が書いてあるレイヤーの名前（common / self / プロジェクト名）。記録の source になる。
	Source string
}

// Decide はこの定義の範囲が、ワークツリーのルートからの相対パスをどう扱うか。inherit なら常に中。
func (p *PhaseType) Decide(rel string) rules.Decision {
	if p.Inherit {
		return rules.Allow
	}
	for i := range p.Scope {
		if p.Scope[i].Matches(rel) {
			return rules.Allow
		}
	}
	return ticket.Outside
}

// fields はレイヤーをまたいで「同じ定義か」を比べる欄（id を除く）。Source は含めない（比べたいのは中身で、
// 置いてある場所ではない）。名前の並びはコピーの欄と同じ。
func (p *PhaseType) fields() []field {
	scope := inherit
	if !p.Inherit {
		scope = fmt.Sprintf("%q", p.ScopeGlobs)
	}
	return []field{
		{"title", p.Title},
		{"kind", string(p.Kind)},
		{"review", fmt.Sprint(p.Review)},
		{"scope", scope},
		{"deliverables", fmt.Sprintf("%q", p.Deliverables)},
		{"agent", p.Agent},
		{"skill", p.Skill},
		{"when", p.When},
		{"learning", string(p.Learning)},
	}
}

type field struct{ name, value string }

// Same は読んだ形で同じ定義か。YAML の書き方・キーの順・既定値を省いた書き方の違いは同じとみなす。
func Same(a, b *PhaseType) bool {
	return a.ID == b.ID && len(Differing(a, b)) == 0
}

// Differing は 2 つの定義で値の違う欄の名前。
func Differing(a, b *PhaseType) []string {
	var out []string
	fb := b.fields()
	for i, f := range a.fields() {
		if f.value != fb[i].value {
			out = append(out, f.name)
		}
	}
	return out
}

// AsCopy は親の phases: に書く形。読み直すと同じ定義になる欄に戻す。
func (p *PhaseType) AsCopy() *yaml.Map {
	out := &yaml.Map{}
	out.Put("kind", string(p.Kind))
	out.Put("title", p.Title)
	out.Put("review", p.Review)
	if p.Inherit {
		out.Put("scope", inherit)
	} else {
		out.Put("scope", strs(p.ScopeGlobs))
	}
	if len(p.Deliverables) > 0 {
		out.Put("deliverables", strs(p.Deliverables))
	}
	for _, kv := range [][2]string{{"agent", p.Agent}, {"skill", p.Skill}, {"when", p.When}} {
		if kv[1] != "" {
			out.Put(kv[0], kv[1])
		}
	}
	if p.Learning != LearningOn {
		out.Put("learning", string(p.Learning))
	}
	return out
}

func strs(items []string) []any {
	out := make([]any, len(items))
	for i, s := range items {
		out[i] = s
	}
	return out
}

// Types は定義の集合（id → 定義。書いた順を保つ）。nil は「定義が無い」（番号だけの挙動）。
type Types struct {
	list []*PhaseType
	at   map[string]int
}

// Get は id の定義。無ければ nil。nil の *Types でも使える。
func (t *Types) Get(id string) *PhaseType {
	if t == nil {
		return nil
	}
	if i, ok := t.at[id]; ok {
		return t.list[i]
	}
	return nil
}

// Len は定義の数。
func (t *Types) Len() int {
	if t == nil {
		return 0
	}
	return len(t.list)
}

// All は書いた順の定義。
func (t *Types) All() iter.Seq[*PhaseType] {
	return func(yield func(*PhaseType) bool) {
		if t == nil {
			return
		}
		for _, p := range t.list {
			if !yield(p) {
				return
			}
		}
	}
}

// put は定義を置く。同じ id があれば位置はそのままで置き換える（Python の dict と同じ）。
func (t *Types) put(p *PhaseType) {
	if t.at == nil {
		t.at = map[string]int{}
	}
	if i, ok := t.at[p.ID]; ok {
		t.list[i] = p
		return
	}
	t.at[p.ID] = len(t.list)
	t.list = append(t.list, p)
}

// With は定義を足した写し（同じ id は位置を保って置き換える。Python の {**t, **extra}）。t は変えない。
func (t *Types) With(extra ...*PhaseType) *Types {
	out := t.clone()
	for _, p := range extra {
		out.put(p)
	}
	return out
}

func (t *Types) clone() *Types {
	out := &Types{}
	for p := range t.All() {
		out.put(p)
	}
	return out
}

// markSource はこの集合の定義に、どのレイヤーから来たかを持たせる。
func (t *Types) markSource(layer string) {
	for p := range t.All() {
		p.Source = layer
	}
}

func problem(sev rules.Severity, rule, detail string) rules.Problem {
	return rules.Problem{Severity: sev, Rule: rule, Detail: detail}
}

func hasError(problems []rules.Problem) bool {
	for _, p := range problems {
		if p.Severity == rules.Error {
			return true
		}
	}
	return false
}

// Load は定義のファイルを読む。ファイルが無ければ nil（定義を使わない）。壊れていれば nil と苦情。
// UTF-8 として読めないものも苦情で返す（判定を異常終了させず、読めない定義を「定義では切り詰めない」として扱う）。
func Load(path string) (*Types, []rules.Problem) {
	text, err := fsio.ReadDiskText(path, fsio.Strict)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return nil, nil
		}
		return nil, []rules.Problem{problem(rules.Error, Where, path+" を読めない ("+err.Error()+")")}
	}
	return Parse(text, path)
}

// Parse は phases.yml の本文を読む。where は苦情の出どころ。error が 1 つでもあれば nil。
func Parse(text, where string) (*Types, []rules.Problem) {
	data, err := yaml.Load(text)
	if err != nil {
		return nil, []rules.Problem{problem(rules.Error, where, "YAML として読めない: "+err.Error())}
	}
	top, ok := data.(*yaml.Map)
	if !ok {
		return nil, []rules.Problem{problem(rules.Error, where, "最上位がマップではない")}
	}
	if v := top.Get("version"); !pyfmt.EqualsInt(v, Version) {
		return nil, []rules.Problem{problem(rules.Error, where,
			fmt.Sprintf("版 %s は扱えない（このビルドが読むのは %d）", pyfmt.Repr(v), Version))}
	}
	raw, ok := top.Get("phases").(*yaml.Map)
	if !ok || raw.Len() == 0 {
		return nil, []rules.Problem{problem(rules.Error, where, "`phases` が無いか空か、マップではない")}
	}
	types, problems := readDefs(raw)
	if hasError(problems) {
		return nil, problems
	}
	return types, problems
}

// readDefs は定義のマップ（id → 欄）を読む。phases.yml と親の phases: の両方が使う。読めない定義は入れずに
// 苦情を足す。表示名も一意にする（ユーザは表示名で見るので、同じ名前が 2 つあると承認画面で見分けられない）。
func readDefs(raw *yaml.Map) (*Types, []rules.Problem) {
	types := &Types{}
	var problems []rules.Problem
	titles := map[string]string{}
	for key, body := range raw.All() {
		id := ospath.Strip(pyfmt.Str(key))
		if strings.Contains(id, rules.IDSeparator) {
			// レイヤーの名前をつけた形（lib:build）と見分けが付かず、記録を読んだユーザが直すファイルを決められない。
			problems = append(problems, problem(rules.Error, id, "識別子に `"+rules.IDSeparator+"` は書けない。"+
				"レイヤーの名前を添えた形（`self"+rules.IDSeparator+"id` / "+
				"`<プロジェクト名>"+rules.IDSeparator+"id`）と見分けが付かない"))
			continue
		}
		if !ids.IsValidName(id) {
			problems = append(problems, problem(rules.Error, id, "識別子に使えない文字がある"))
			continue
		}
		fields, ok := body.(*yaml.Map)
		if !ok {
			problems = append(problems, problem(rules.Error, id, "定義の中身がマップではない"))
			continue
		}
		p, own := one(id, fields)
		problems = append(problems, own...)
		if p == nil {
			continue
		}
		if prior, ok := titles[p.Title]; ok {
			problems = append(problems, problem(rules.Error, id, "表示名 `"+p.Title+"` が `"+prior+"` と重なる"))
			continue
		}
		titles[p.Title] = id
		types.put(p)
	}
	return types, problems
}

// one は定義 1 件を PhaseType にする。読めなければ nil と、error を 1 件以上含む苦情。欄の既定値（表示名は
// 識別子、種類は作業、レビューは要る）はここで入れる。
func one(id string, body *yaml.Map) (*PhaseType, []rules.Problem) {
	bad := func(detail string) (*PhaseType, []rules.Problem) {
		return nil, []rules.Problem{problem(rules.Error, id, detail)}
	}
	p := &PhaseType{ID: id, Kind: Work, Review: true, Inherit: true, Learning: LearningOn}
	if p.Title = ospath.Strip(body.Text("title")); p.Title == "" {
		p.Title = id
	}
	kind := body.Text("kind")
	if kind == "" {
		kind = string(Work)
	}
	p.Kind = Kind(ospath.Strip(kind))
	if !containsKind(p.Kind) {
		return bad("`kind` は " + joinKinds(" か "))
	}
	review, present := body.Lookup("review")
	if present {
		b, ok := review.(bool)
		if !ok {
			return bad("`review` は true か false で書く（`" + pyfmt.Str(review) + "` は読めない。" +
				"以前の `none` は false、`mr` は true に書き直す）")
		}
		p.Review = b
	}
	if p.Kind == Feedback && !p.Review {
		// フィードバック対応の結果をユーザが見ない経路は作らない。
		return bad("フィードバック対応の定義に `review: false` は書けない（`true` にする）")
	}
	if scope, present := body.Lookup("scope"); present && scope != nil && scope != inherit {
		list, ok := scope.([]any)
		if !ok {
			return bad("`scope` は glob のリストか `inherit`")
		}
		if len(list) > maxEntries {
			return bad(tooMany("scope", len(list)))
		}
		entries, problems := globs(id, "scope", list)
		if len(problems) > 0 {
			return nil, problems
		}
		p.Inherit, p.Scope = false, entries
		for _, e := range entries {
			p.ScopeGlobs = append(p.ScopeGlobs, e.Glob)
		}
	}
	if raw, present := body.Lookup("deliverables"); present && raw != nil {
		list, ok := yaml.Strings(raw)
		if !ok {
			return bad("`deliverables` は文字列のリスト")
		}
		if len(list) > maxEntries {
			return bad(tooMany("deliverables", len(list)))
		}
		for _, x := range list {
			if x = ospath.Strip(x); x != "" {
				p.Deliverables = append(p.Deliverables, x)
			}
		}
	}
	for _, glob := range p.Deliverables {
		if strings.Contains(glob, "..") || ospath.IsAbs(glob) {
			return bad("`deliverables` の `" + glob + "` はワークツリーの中を指す形で書く")
		}
	}
	p.Agent = ospath.Strip(body.Text("agent"))
	p.Skill = ospath.Strip(body.Text("skill"))
	p.When = ospath.Strip(body.Text("when"))
	if raw, present := body.Lookup("learning"); present && raw != nil {
		switch x := raw.(type) {
		case bool:
			p.Learning = LearningOff
			if x {
				p.Learning = LearningOn
			}
		case string:
			l := Learning(pyfmt.Lower(ospath.Strip(x)))
			if l != LearningOn && l != LearningOff {
				return bad("`learning` は " + joinLearnings())
			}
			p.Learning = l
		default:
			return bad("`learning` は " + joinLearnings())
		}
	}
	return p, nil
}

func containsKind(k Kind) bool {
	for _, x := range kinds {
		if x == k {
			return true
		}
	}
	return false
}

func joinKinds(sep string) string {
	parts := make([]string, len(kinds))
	for i, k := range kinds {
		parts[i] = string(k)
	}
	return strings.Join(parts, sep)
}

func joinLearnings() string {
	parts := make([]string, len(learnings))
	for i, l := range learnings {
		parts[i] = string(l)
	}
	return strings.Join(parts, " か ")
}

func tooMany(key string, count int) string {
	return fmt.Sprintf("`%s` が %d 件ある（上限 %d 件）。まとめるか、定義を分けてください", key, count, maxEntries)
}

// globs は範囲の glob を、子チケットの範囲と同じ規則で式にする（大文字小文字はどの機械でも区別しない。
// 子 ⊆ 定義 ⊆ 親 の 3 つを 1 つの規則で揃える）。苦情の rule は id、文は `<key>[i]` で項を名指しする。
func globs(id, key string, raw []any) ([]ticket.Entry, []rules.Problem) {
	var entries []ticket.Entry
	var problems []rules.Problem
	for i, item := range raw {
		at := fmt.Sprintf("`%s[%d]`", key, i)
		s, ok := item.(string)
		glob := ospath.Strip(s)
		if !ok || glob == "" {
			problems = append(problems, problem(rules.Error, id, at+" が文字列ではない"))
			continue
		}
		if ticket.GlobHasSpace(glob) {
			problems = append(problems, problem(rules.Error, id, at+" の `"+glob+"` に"+ticket.GlobSpaceReason))
			continue
		}
		if strings.ContainsAny(glob, "~$") || strings.Contains(glob, "..") || ospath.IsAbs(glob) {
			problems = append(problems, problem(rules.Error, id, at+" の `"+glob+"` はワークツリーの中を指す形で書く"))
			continue
		}
		glob = strings.Trim(strings.ReplaceAll(glob, `\`, "/"), "/")
		compiled, err := pyre.Compile("^"+pyre.TranslateGlob(glob), pyre.IgnoreCase)
		if err != nil {
			problems = append(problems, problem(rules.Error, id, at+" を式にできない: "+err.Error()))
			continue
		}
		entries = append(entries, ticket.Entry{Decision: rules.Allow, Glob: glob, Compiled: compiled})
	}
	return entries, problems
}

// consolidate は統合の子の範囲の上限を持つ、ccnavi の中に固定した定義。
var consolidate = func() *PhaseType {
	entries, _ := globs(ticket.MaintenanceConsolidate, "scope", strs(ticket.ConsolidateScope))
	p := &PhaseType{
		ID: ticket.MaintenanceConsolidate, Title: "学びの統合", Kind: Feedback,
		Scope: entries, Learning: LearningOff,
	}
	for _, e := range entries {
		p.ScopeGlobs = append(p.ScopeGlobs, e.Glob)
	}
	return p
}()

// ConsolidateType は統合の子の範囲の上限を持つ定義。phases.yml にも親のコピーにも置かない。統合の子は親の
// 計画に載らず、親の範囲の上限も当てないので、範囲を切り詰めるのはこの定義と子の allow だけ。
func ConsolidateType() *PhaseType { return consolidate }
