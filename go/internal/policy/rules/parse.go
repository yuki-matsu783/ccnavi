package rules

import (
	"fmt"
	"math"
	"slices"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
)

// needsMessage は文面が要るタイプ。ask の文面は確認の画面にしか出ず、allow は届く先が無い
// （書いた文面は lint が言うが、読み込みは通す。外すとその文面のせいでルールごと通ってしまう）。
const needsMessage = Deny

// Load はルールファイルを読んで組み立てる。root は {root} の置き換え先。
// 読めない（ファイルが無い、UTF-8 でない、YAML として読めない、マッピングでない）ときは error を返す。
// 組み立てられないルールは外して Problem で名指しし、組み立てられたものは返す。1 件の不備でガード全体を止めないため。
func Load(path, root string) (*RuleSet, []Problem, error) {
	// UTF-8 として厳密に読む。fsio を通さずディスクだけを読むので、溜める段も見ない。
	text, err := fsio.ReadDiskText(path, fsio.Strict)
	if err != nil {
		return nil, nil, err
	}
	data, err := yaml.Load(text)
	if err != nil {
		return nil, nil, fmt.Errorf("%s を YAML として読めない: %w", path, err)
	}
	m, ok := data.(*yaml.Map)
	if !ok {
		return nil, nil, fmt.Errorf("%s のルールがマッピングではない", path)
	}
	rs, problems := Parse(m, root)
	return rs, problems, nil
}

// Parse は読み込み済みのルールを組み立てる。ファイルを開く部分と分けるのは、ルールファイルを
// 書き換えずに組み立てる呼び手（設定の取り込み）が同じ経路を通れるようにするため。
func Parse(data *yaml.Map, root string) (*RuleSet, []Problem) {
	written := data.Get("version")
	if !pyfmt.Truthy(written) {
		written = 0
	}
	// 版が合わなくても読み込みは続ける（苦情に載せる）。版が違うファイルを読んで、保護を丸ごと失わないため。
	rs := &RuleSet{Version: versionOf(written)}
	var problems []Problem
	if !pyfmt.EqualsInt(written, Version) {
		problems = append(problems, Problem{Severity: Error, Detail: fmt.Sprintf(
			"ルール書式の版 %s は扱えない（このビルドが読むのは %d）。"+
				"書式は `%s` の 3 タイプで、照合に指定できるのは `glob` か `regex`。"+
				"`glob` は fnmatch の glob で、文字列全体と一致したときだけヒットするので、"+
				"部分一致が必要なら前後に `*` を書く",
			pyfmt.Str(written), Version, joinSections("` `"))})
	}
	for _, name := range sections {
		raw, present := data.Lookup(string(name))
		if !present || raw == nil {
			continue
		}
		list, ok := raw.([]any)
		if !ok {
			problems = append(problems, Problem{Severity: Error, Rule: "(" + string(name) + ")", Detail: "`" + string(name) + "` がリストではない"})
			continue
		}
		for i, item := range list {
			where := fmt.Sprintf("%s[%d]", name, i)
			m, ok := item.(*yaml.Map)
			if !ok {
				problems = append(problems, Problem{Severity: Error, Rule: where, Detail: "ルールがマッピングではない"})
				continue
			}
			rule, found := build(fromYAML(m), name, where, root)
			problems = append(problems, found...)
			if rule != nil {
				section := rs.Section(name)
				*section = append(*section, rule)
			}
		}
	}
	return rs, problems
}

func joinSections(sep string) string {
	names := make([]string, len(sections))
	for i, s := range sections {
		names[i] = string(s)
	}
	return strings.Join(names, sep)
}

// versionLimit は書かれた小数の版を RuleSet.Version（int）に入れる上限。超えた版は 0 にする（どのみち扱えない版）。
const versionLimit = math.MaxInt32

func versionOf(v any) int {
	if pyfmt.EqualsInt(v, Version) {
		return Version
	}
	switch x := v.(type) {
	case int:
		return x
	case float64:
		if x == math.Trunc(x) && math.Abs(x) < versionLimit {
			return int(x)
		}
	}
	return 0
}

// Spec は組み込みのルール 1 件。ルールファイルのルールと同じ検査と組み立てを通る。
type Spec struct {
	ID    string
	Match string
	Glob  string
	Regex string
	Text  TextRef
	// Exempt は Rule.Exempt に入る。
	Exempt func(string) string
}

// Builtin は組み込みのルールを組み立てる。builtin: で始まる id を書けるのはこちらだけ。
// 苦情はこのビルドの不備を指す。
func Builtin(section Decision, specs []Spec, root string) ([]*Rule, []Problem) {
	var out []*Rule
	var problems []Problem
	for i, s := range specs {
		raw := rawRule{
			builtin: true, id: s.ID, match: s.Match, glob: s.Glob, regex: s.Regex, text: s.Text,
			hasGlob: s.Glob != "", hasRegex: s.Regex != "",
		}
		rule, found := build(raw, section, fmt.Sprintf("%s[%d]", section, i), root)
		problems = append(problems, found...)
		if rule != nil {
			rule.Exempt = s.Exempt
			out = append(out, rule)
		}
	}
	return out, problems
}

// rawRule は書かれたルール 1 件（ルールファイルの欄か、組み込みの Spec）。文字列の欄は、
// 無い・空は空文字、文字列でない値は文字にした形にそろえる。
type rawRule struct {
	builtin                            bool
	id, match, glob, regex, message    string
	text                               TextRef
	ctx, ctxOnce, ctxFile, ctxOnceFile string
	every                              any
	hasGlob, hasRegex, hasWhen         bool
	when                               any
	hasExcept                          bool
	except                             any
}

func fromYAML(m *yaml.Map) rawRule {
	when, hasWhen := m.Lookup(WhenKey)
	except, hasExcept := m.Lookup(ExceptKey)
	return rawRule{
		id: m.Text("id"), match: m.Text("match"), glob: m.Text("glob"), regex: m.Text("regex"),
		message: m.Text("message"), ctx: m.Text("additionalContext"), ctxOnce: m.Text("additionalContextOnce"),
		ctxFile: m.Text("additionalContextFile"), ctxOnceFile: m.Text("additionalContextOnceFile"),
		every: m.Get("every"), hasGlob: m.Has("glob"), hasRegex: m.Has("regex"),
		hasWhen: hasWhen, when: when, hasExcept: hasExcept, except: except,
	}
}

// build はルール 1 件を組み立てる。返すのは (ルール, 苦情)。苦情があってもルールが返ることがある。
// {!root} を組み立てられずに止める側へ倒した deny・ask と、{!root} の後ろの書き方への warn がそれにあたる。
//
// 処理フロー:
//  1. 書き方の検査（id・match・文面）。通らなければルールごと外す。
//  2. 本体を 1 つに絞る（glob・regex・when のどれか）。
//  3. when の個別条件、または glob・regex を式に組む。組めない式は deny・ask を全部止める側に倒す（degrade）。
//  4. except を式に組む。組めない except は外すが、allow はルールごと使わない。
func build(raw rawRule, section Decision, where, root string) (*Rule, []Problem) {
	if !raw.builtin && ospath.Strip(raw.id) == "" {
		return nil, []Problem{{Severity: Error, Rule: where, Detail: "id がありません。ルールには id を書いてください（記録と文面にこの名前が出る）"}}
	}
	// 1. 書き方の検査。
	every := ReadableEvery(raw.every)
	if every == 0 {
		every = EveryDefault
	}
	rule := &Rule{
		ID: raw.id, BareID: raw.id, Match: raw.match, Glob: raw.glob, Regex: raw.regex, Message: raw.message,
		Text: raw.text, AdditionalContext: raw.ctx, AdditionalContextOnce: raw.ctxOnce,
		AdditionalContextFile: raw.ctxFile, AdditionalContextOnceFile: raw.ctxOnceFile,
		Every: every, EveryWritten: raw.every, Decision: section, Root: root, FromFile: !raw.builtin,
	}
	name := string(section) + ":" + rule.ID
	fail := func(detail string) (*Rule, []Problem) {
		return nil, []Problem{{Severity: Error, Rule: name, Detail: detail}}
	}
	if !raw.builtin && strings.HasPrefix(pyfmt.Lower(raw.id), ReservedIDPrefix) {
		return fail("`" + ReservedIDPrefix + "` で始まる id は組み込みルールの名前で、" +
			"ルールファイルには書けない。同じ名前で書いても組み込みは置き換わらない。" +
			"別の名前にしてください。" +
			"組み込みを外したいなら env（CCNAVI_GUARD_CORE_FILES など）で" +
			"その保護ごと切ってください")
	}
	if !raw.builtin && strings.Contains(raw.id, IDSeparator) {
		return fail("id に `" + IDSeparator + "` は書けない。レイヤーの名前を添えた形" +
			"（`self" + IDSeparator + "id` / `<プロジェクト名>" + IDSeparator + "id`）と" +
			"見分けが付かず、記録を読んだユーザがどのファイルを直すのか決められない")
	}
	if rule.Message == "" && rule.Text.ID == "" && section == needsMessage {
		return fail("message がありません。ルールには、代わりに何をすべきかを書く必要がある")
	}
	if rule.Match == "" {
		return fail("match がありません。どのツールにもヒットしない")
	}
	// 2. 本体は glob・regex・when のうち 1 つだけ。
	// when と並べたかはキーの有無で見る（`glob: ''` を添えた組み合わせ条件を通さない）。
	var bodies []string
	for _, b := range []struct {
		key     string
		present bool
	}{
		{"glob", raw.hasWhen && raw.hasGlob || !raw.hasWhen && rule.Glob != ""},
		{"regex", raw.hasWhen && raw.hasRegex || !raw.hasWhen && rule.Regex != ""},
		{WhenKey, raw.hasWhen},
	} {
		if b.present {
			bodies = append(bodies, b.key)
		}
	}
	switch len(bodies) {
	case 0:
		return fail("glob も regex も when もありません。")
	case 2:
		return fail(bodies[0] + " と " + bodies[1] + " の両方がある。どちらで判定するのか決められない")
	case 3:
		return fail("glob と regex と when が並んでいる。書けるのは 1 つだけで、どれで判定するのか決められない")
	}

	// 3. 本体を式に組む。
	var tree *Condition
	if raw.hasWhen {
		var problem *Problem
		if tree, problem = readWhen(raw.when, name); problem != nil {
			return nil, []Problem{*problem}
		}
	}
	excepts, problem := readExcepts(raw, name)
	if problem != nil {
		return nil, []Problem{*problem}
	}

	var problems []Problem
	if tree != nil {
		refused := ""
		for _, leaf := range tree.Walk() {
			if !leaf.IsLeaf() {
				continue
			}
			label := leaf.Where + " の "
			glob, regex := leaf.text, ""
			if leaf.Kind == WhenRegex {
				glob, regex = "", leaf.text
			}
			part := compileExpr(glob, regex, root, name, label)
			if part.outcome == exprFailed {
				return nil, append(problems, *part.err)
			}
			if part.note != nil {
				problems = append(problems, *part.note)
			}
			if part.outcome == exprRefused && refused == "" {
				refused = label + part.refused
			}
			leaf.Compiled = part.re
		}
		rule.When = tree
		if refused != "" {
			// 個別条件を 1 つでも組み立てられなければ、組み合わせ条件の意味が決まらない（not の下なら外すと
			// 狭まり、上なら広がる）。本体と同じく deny・ask は全部止め、allow は使わない。
			degraded, p := degrade(rule, section, name, refused)
			return degraded, append(problems, p)
		}
	} else {
		built := compileExpr(rule.Glob, rule.Regex, root, name, "")
		if built.outcome == exprFailed {
			return nil, []Problem{*built.err}
		}
		if built.note != nil {
			problems = append(problems, *built.note)
		}
		if built.outcome == exprRefused {
			// except は付けない。付けると「全部止める」に穴が空く。
			degraded, p := degrade(rule, section, name, built.refused)
			return degraded, append(problems, p)
		}
		rule.Compiled = built.re
	}

	// 4. except を式に組む。
	for i, e := range excepts {
		label := fmt.Sprintf("except[%d] の ", i)
		part := compileExpr(e.Glob, e.Regex, root, name, label)
		if part.outcome == exprFailed {
			return nil, append(problems, *part.err)
		}
		if part.note != nil {
			problems = append(problems, *part.note)
		}
		if part.outcome == exprRefused {
			// 組み立てられない except は外して使う。deny・ask は当たる範囲が広がる（止める側）。allow は
			// 広がると通す範囲が増えるので、ルールごと使わない。
			problems = append(problems, Problem{Severity: Error, Rule: name, Detail: label + part.refused})
			if section == Allow {
				return nil, problems
			}
			continue
		}
		e.Compiled = part.re
		rule.Excepts = append(rule.Excepts, e)
	}
	return rule, problems
}

// readExcepts は except を読む。キーが無ければ空。書いてあるのに空・null・形が違うものは error にする。
// 書いたユーザは何かを除いたつもりでいるため、黙って無視しない。
func readExcepts(raw rawRule, name string) ([]Exclusion, *Problem) {
	if !raw.hasExcept {
		return nil, nil
	}
	fail := func(detail string) ([]Exclusion, *Problem) {
		return nil, &Problem{Severity: Error, Rule: name, Detail: detail}
	}
	list, ok := raw.except.([]any)
	if !ok {
		return fail("`" + ExceptKey + "` がリストではない。`- glob: '...'` か `- regex: '...'` を並べてください")
	}
	if len(list) == 0 {
		return fail("`" + ExceptKey + "` が空。除くものが無いなら `" + ExceptKey + "` ごと削除してください")
	}
	var out []Exclusion
	for i, item := range list {
		where := fmt.Sprintf("%s[%d]", ExceptKey, i)
		m, ok := item.(*yaml.Map)
		if !ok {
			return fail(where + " がマッピングではない。`glob: '...'` か `regex: '...'` の形で書いてください")
		}
		if unknown := unknownKeys(m, []string{"glob", "regex"}); len(unknown) > 0 {
			return fail(where + " に未対応のキー（" + strings.Join(unknown, ", ") + "）がある。書けるのは `glob` か `regex` の 1 つだけ")
		}
		// 文字列だけを受ける。引用符の無い `on` は真偽値に読まれ、文字にすると 'True' に当てる except になる。
		for _, key := range []string{"glob", "regex"} {
			if v, present := m.Lookup(key); present {
				if _, isText := v.(string); !isText {
					return fail(where + " の " + key + " が文字列ではない。引用符で囲んでください")
				}
			}
		}
		glob, regex := m.Text("glob"), m.Text("regex")
		if glob == "" && regex == "" {
			return fail(where + " に glob も regex もありません。")
		}
		if glob != "" && regex != "" {
			return fail(where + " に glob と regex の両方がある。どちらで判定するのか決められない")
		}
		out = append(out, Exclusion{Glob: glob, Regex: regex})
	}
	return out, nil
}

// unknownKeys は allowed に無いキーを文字にして、並べ替えたもの。
func unknownKeys(m *yaml.Map, allowed []string) []string {
	var unknown []string
	for k := range m.All() {
		if s, ok := k.(string); ok && slices.Contains(allowed, s) {
			continue
		}
		unknown = append(unknown, pyfmt.Str(k))
	}
	slices.Sort(unknown)
	return unknown
}

func kindNames(sep string) string {
	names := make([]string, len(whenKinds))
	for i, k := range whenKinds {
		names[i] = string(k)
	}
	return strings.Join(names, sep)
}

type whenError string

// readWhen は when を読んで組み合わせ条件にする（個別条件の式は build が組み立てる）。書式の誤りは最初の 1 件。
func readWhen(written any, name string) (*Condition, *Problem) {
	count := 0
	var node func(raw any, where string, depth int) (*Condition, whenError)
	node = func(raw any, where string, depth int) (*Condition, whenError) {
		if depth > maxWhenDepth {
			return nil, whenError(fmt.Sprintf("%s で入れ子が %d 段を超える。判定が重くならないように、段を浅くするか、ルールを分けてください", where, maxWhenDepth))
		}
		count++
		if count > maxWhenNodes {
			return nil, whenError(fmt.Sprintf("%s のノードが %d 個を超える。判定が重くならないように、ルールを分けてください", WhenKey, maxWhenNodes))
		}
		m, ok := raw.(*yaml.Map)
		if !ok {
			return nil, whenError(where + " がマッピングではない。`" + kindNames("` / `") + "` のどれか 1 つのキーを持つ形で書いてください")
		}
		allowed := make([]string, len(whenKinds))
		for i, k := range whenKinds {
			allowed[i] = string(k)
		}
		if unknown := unknownKeys(m, allowed); len(unknown) > 0 {
			return nil, whenError(where + " に未対応のキー（" + strings.Join(unknown, ", ") + "）がある。書けるのは `" + kindNames("` / `") + "` のどれか 1 つだけ")
		}
		if m.Len() != 1 {
			got := "キーがありません"
			if m.Len() > 0 {
				keys, _ := m.StringKeys()
				got = fmt.Sprintf("キーが %d つある（%s）", m.Len(), strings.Join(keys, ", "))
			}
			return nil, whenError(where + " に" + got + "。1 つのノードに書けるキーは 1 つだけで、組み合わせるなら `" +
				string(WhenAll) + "` / `" + string(WhenAny) + "` の下に並べてください")
		}
		var kind WhenKind
		var value any
		for k, v := range m.All() {
			kind, value = WhenKind(k.(string)), v
		}
		switch kind {
		case WhenGlob, WhenRegex:
			text, ok := value.(string)
			if !ok || text == "" {
				return nil, whenError(fmt.Sprintf("%s の %s が空か、文字列ではない", where, kind))
			}
			return &Condition{Kind: kind, text: text, Where: where + "." + string(kind)}, ""
		case WhenNot:
			child, err := node(value, where+"."+string(kind), depth+1)
			if err != "" {
				return nil, err
			}
			return &Condition{Kind: kind, Children: []*Condition{child}, Where: where}, ""
		}
		list, ok := value.([]any)
		if !ok {
			return nil, whenError(fmt.Sprintf("%s の %s がリストではない。`- ノード` を並べてください", where, kind))
		}
		if len(list) == 0 {
			return nil, whenError(fmt.Sprintf("%s の %s が空。1 つ以上のノードを並べてください", where, kind))
		}
		cond := &Condition{Kind: kind, Where: where}
		for i, v := range list {
			child, err := node(v, fmt.Sprintf("%s.%s[%d]", where, kind, i), depth+1)
			if err != "" {
				return nil, err
			}
			cond.Children = append(cond.Children, child)
		}
		return cond, ""
	}
	tree, err := node(written, WhenKey, 1)
	if err != "" {
		return nil, &Problem{Severity: Error, Rule: name, Detail: string(err)}
	}
	return tree, nil
}
