package texts

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
)

// version は文面ファイルの書式の版。
const version = 1

// kind は文の種類。識別子の頭で決まる。
type kind string

const (
	kindDeny    kind = "deny"
	kindNudge   kind = "nudge"
	kindNext    kind = "next"
	kindChoices kind = "choices"
	kindNote    kind = "note"
	// kindNotice は止めない通知。見出しと状態の行はコードに残し、文面ファイルには本文だけを書く。
	kindNotice kind = "notice"
)

// 欄の名前。
const (
	fieldWhat     = "what"
	fieldWhy      = "why"
	fieldNext     = "next"
	fieldNoBypass = "no_bypass"
	fieldNote     = "note"
	fieldItems    = "items"
	keyKind       = "kind"
	keyValues     = "values"
)

// fields は種類ごとの（必須の欄, 任意の欄）。並びは出力の行の順。
type fields struct {
	required []string
	optional []string
}

var kindFields = map[kind]fields{
	kindDeny:    {[]string{fieldWhat, fieldWhy, fieldNext, fieldNoBypass}, []string{fieldNote}},
	kindNudge:   {[]string{fieldWhat, fieldNext}, []string{fieldWhy, fieldNoBypass, fieldNote}},
	kindNext:    {[]string{fieldNext}, []string{fieldNote}},
	kindChoices: {[]string{fieldItems}, nil},
	kindNote:    {[]string{fieldNote}, nil},
	kindNotice:  {[]string{fieldWhat}, []string{fieldWhy, fieldNext, fieldNote}},
}

// kindOrder は種類を苦情の文に並べる順。
var kindOrder = []kind{kindDeny, kindNudge, kindNext, kindChoices, kindNote, kindNotice}

// ruleLooseFields は `RULE_` の文（組み込みルールの文）の欄。what だけが必須で、言うことが無い欄は作り足さない。
// ruleAsk とその場合は、記録の code と同じ識別子を持ち、ほかの拒否と同じ 4 欄を必ず持つ。
var ruleLooseFields = fields{[]string{fieldWhat}, []string{fieldWhy, fieldNext, fieldNoBypass, fieldNote}}

const (
	// rulePrefix は組み込みルールの文の識別子の頭。
	rulePrefix = "RULE_"
	// ruleAsk は確認の文の識別子。
	ruleAsk = rulePrefix + "ASK"
)

// prefixes は識別子の頭と種類（並びは苦情の文の順）。
var prefixes = []struct {
	head string
	kind kind
}{
	{"DENY_", kindDeny}, {rulePrefix, kindDeny}, {"NUDGE_", kindNudge}, {"NEXT_", kindNext},
	{"CHOICES_", kindChoices}, {"NOTE_", kindNote}, {"NOTICE_", kindNotice},
}

// 出力で欄の前に付ける語。what と items には付けない（items は番号を付ける）。
const (
	whyLabel      = "なぜ: "
	NextLabel     = "次: "
	noBypassLabel = "迂回しない: "
	NoteLabel     = "案内: "
	choicesHead   = "次の手:"
)

var labels = map[string]string{fieldWhy: whyLabel, fieldNext: NextLabel, fieldNoBypass: noBypassLabel}

const (
	// ShownSubjectLimit は、理由や報告に載せる対象の文字列の長さの上限。ヒアドキュメントの全文で本体を押し出さないため。
	// 判定の記録に残す長さ（audit.recordedSubjectLimit）とは別の約束。
	ShownSubjectLimit = 200
	// limitField は 1 欄の長さ（場合ごと）、limitEntry は 1 件の欄の長さの合計の上限。
	limitField = 600
	limitEntry = 2000
)

var (
	idPattern    = regexp.MustCompile(`^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?:__[A-Z0-9]+(?:_[A-Z0-9]+)*)?$`)
	namePattern  = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)
	placeholders = regexp.MustCompile(`\{\{|\}\}|\{([^{}]*)\}|[{}]`)
	unsafeChars  = regexp.MustCompile(`[\x00-\x1f\x7f-\x{9f}\x{2028}\x{2029}\x{3000}]+`)
)

// adrPattern は ADR 番号の書き方。`\s` と `\d` は Unicode で読む。
const adrPattern = `ADR[-\s]?\d`

var adr, adrErr = pyre.Compile(adrPattern, pyre.IgnoreCase)

// hasADR は文に ADR 番号があるか。
func hasADR(text string) bool { return adrErr == nil && adr.Search(text) }

// modifiers は値の差し込みに付けられる修飾。
var modifiers = []string{"sh", "path"}

// quotedSuffix は引用を済ませた値を受ける名前の終わり。修飾は付けられない。
const quotedSuffix = "_q"

const scriptPrefix = "script:"

// IsID は識別子の形か。
func IsID(id string) bool { return idPattern.MatchString(id) }

func isName(name string) bool { return namePattern.MatchString(name) }

// kindOfID は識別子の頭から決まる種類。知らない頭なら空。
func kindOfID(id string) kind {
	for _, p := range prefixes {
		if strings.HasPrefix(id, p.head) {
			return p.kind
		}
	}
	return ""
}

// fieldsFor は識別子と種類から決まる欄。`RULE_` の文（ruleAsk の系統を除く）だけが what 以外を任意にする。
func fieldsFor(id string, kind kind) fields {
	if kind == kindDeny && strings.HasPrefix(id, rulePrefix) && id != ruleAsk && !strings.HasPrefix(id, ruleAsk+"__") {
		return ruleLooseFields
	}
	return kindFields[kind]
}

// value は欄の値。文字列か、場合ごとの文字列（Cases）か、items の並び。
type value struct {
	text  string
	cases []string // 場合の名前（書いた順）
	byKey map[string]string
	items []string
}

// Entry は文面 1 件。
type Entry struct {
	id     string
	kind   kind
	Values []string
	path   string
	fields map[string]value
}

// has は欄があるか。
func (e *Entry) has(field string) bool {
	_, ok := e.fields[field]
	return ok
}

// Cases は場合分けした欄の場合の名前（書いた順）。場合分けでなければ nil。
func (e *Entry) Cases(field string) []string { return e.fields[field].cases }

// Field は場合分けしていない欄の文。
func (e *Entry) Field(field string) string { return e.fields[field].text }

// token の 1 件。kind は value か script。
type placeholder struct {
	script bool
	name   string
	mod    string
}

// scan は文の差し込みを順に拾う。`{{` と `}}` は字の `{` `}` で、差し込みに数えない。
func scan(text string) ([]placeholder, []string) {
	var found []placeholder
	var bad []string
	for _, m := range placeholders.FindAllStringSubmatchIndex(text, -1) {
		whole := text[m[0]:m[1]]
		if whole == "{{" || whole == "}}" {
			continue
		}
		if m[2] < 0 {
			bad = append(bad, fmt.Sprintf("対にならない `%s`（字で使うときは `%s%s`）", whole, whole, whole))
			continue
		}
		inner := text[m[2]:m[3]]
		if name, ok := strings.CutPrefix(inner, scriptPrefix); ok {
			if !rules.IsScriptName(name) {
				bad = append(bad, "`{"+inner+"}` の sh の名前の形が違う")
				continue
			}
			found = append(found, placeholder{script: true, name: name})
			continue
		}
		name, mod, _ := strings.Cut(inner, ":")
		switch {
		case !isName(name):
			bad = append(bad, "`{"+inner+"}` の名前の形が違う（小文字・数字・`_`）")
		case mod != "" && !slices.Contains(modifiers, mod):
			bad = append(bad, "`{"+inner+"}` の修飾 `"+mod+"` を知らない（"+strings.Join(modifiers, " ")+"）")
		case mod != "" && strings.HasSuffix(name, quotedSuffix):
			bad = append(bad, "`{"+inner+"}` は引用済みの値（`"+quotedSuffix+"`）に修飾を付けられない")
		default:
			found = append(found, placeholder{name: name, mod: mod})
		}
	}
	return found, bad
}

// checker は 1 件の検査の途中の状態。
type checker struct {
	id, where string
	problems  []rules.Problem
	used      map[string]bool
	sizes     int
}

func (c *checker) err(message string) {
	c.problems = append(c.problems, rules.Problem{Severity: rules.Error, Rule: c.id, Detail: c.where + ": " + message})
}

func textOf(raw any) string {
	if s, ok := raw.(string); ok {
		return ospath.Strip(s)
	}
	return ""
}

func blank(raw any) bool {
	if raw == nil {
		return true
	}
	s, ok := raw.(string)
	return ok && ospath.Strip(s) == ""
}

func lineCount(text string) int { return len(ospath.Splitlines(text)) }

// checkText は文 1 つの検査。誤りがあれば真。
func (c *checker) checkText(label, text string, single bool) bool {
	bad := false
	if single && lineCount(text) > 1 {
		c.err("`" + label + "` は 1 行で書く")
		bad = true
	}
	if strings.HasPrefix(text, "次:") || strings.HasPrefix(text, "次の手:") {
		c.err("`" + label + "` を `次:` で始めない（`次:` の行は組み立てる側が付ける）")
		bad = true
	}
	n := utf8.RuneCountInString(text)
	if n > limitField {
		c.err(fmt.Sprintf("`%s` が %d 字を超える（%d 字）", label, limitField, n))
		bad = true
	}
	c.sizes += n
	if hasADR(text) {
		c.err("`" + label + "` に ADR 番号がある。決めた中身と理由を書く")
		bad = true
	}
	if rules.HasRelativeScripts(text) {
		c.err("`" + label + "` に相対の `.ccnavi/scripts/` がある。`{script:<名前>}` を使う")
		bad = true
	}
	found, problems := scan(text)
	for _, p := range problems {
		c.err("`" + label + "` の差し込み: " + p)
		bad = true
	}
	for _, f := range found {
		if !f.script {
			c.used[f.name] = true
		}
	}
	return bad
}

// checkField は欄 1 つを検査して正規化した値。誤りがあれば err に言って ok が偽。
func (c *checker) checkField(name string, raw any) (value, bool) {
	if name == fieldItems {
		list, ok := raw.([]any)
		if !ok || len(list) < 2 {
			c.err("`items` は 2 件以上の文字列の並び")
			return value{}, false
		}
		var items []string
		for _, item := range list {
			text := textOf(item)
			if text == "" || lineCount(text) > 1 {
				c.err("`items` の 1 件は 1 行の文字列")
				return value{}, false
			}
			if c.checkText(fieldItems, text, true) {
				return value{}, false
			}
			items = append(items, text)
		}
		return value{items: items}, true
	}
	if m, ok := raw.(*yaml.Map); ok {
		keys, allText := m.StringKeys()
		if m.Len() < 2 || !allText || slices.ContainsFunc(keys, func(k string) bool { return !isName(k) }) {
			c.err("`" + name + "` の場合分けは、2 つ以上の場合の名前（小文字・数字・`_`）で書く")
			return value{}, false
		}
		picked := value{byKey: map[string]string{}}
		failed := false
		for _, k := range keys {
			text := textOf(m.Get(k))
			if text == "" {
				c.err("`" + name + "` の場合 " + k + " の文が無い")
				failed = true
				continue
			}
			if c.checkText(name+"."+k, text, name != fieldNote) {
				failed = true
			}
			picked.cases = append(picked.cases, k)
			picked.byKey[k] = text
		}
		return picked, !failed
	}
	text := textOf(raw)
	if text == "" {
		c.err("`" + name + "` が文字列でない、または空")
		return value{}, false
	}
	if c.checkText(name, text, name != fieldNote) {
		return value{}, false
	}
	return value{text: text}, true
}

func basename(path string) string {
	if b := ospath.Basename(path); b != "" {
		return b
	}
	return path
}

// checkEntry は 1 件を検査して組む。誤りがあれば Entry は nil で、呼び手はその 1 件を採らない。
func checkEntry(id string, raw any, path string) (*Entry, []rules.Problem) {
	c := &checker{id: id, where: basename(path), used: map[string]bool{}}
	expect := kindOfID(id)
	if !IsID(id) {
		c.err("識別子の形が違う（大文字・数字・`_`。場合を分けるときだけ `__`）")
		return nil, c.problems
	}
	if expect == "" {
		heads := make([]string, len(prefixes))
		for i, p := range prefixes {
			heads[i] = p.head
		}
		c.err("識別子の頭は " + strings.Join(heads, " ") + " のどれか")
		return nil, c.problems
	}
	m, ok := raw.(*yaml.Map)
	if !ok {
		c.err("1 件は欄の表で書く")
		return nil, c.problems
	}
	kindText, ok := m.Get(keyKind).(string)
	kind := kind(kindText)
	if _, known := kindFields[kind]; !ok || !known {
		names := make([]string, len(kindOrder))
		for i, k := range kindOrder {
			names[i] = string(k)
		}
		c.err("`kind` は " + strings.Join(names, " ") + " のどれか")
		return nil, c.problems
	}
	if kind != expect {
		c.err(fmt.Sprintf("識別子の頭が %s の種類を言っているが、`kind` は %s", expect, kind))
		return nil, c.problems
	}
	declared := m.Get(keyValues)
	var values []string
	if declared != nil {
		list, ok := yaml.Strings(declared)
		if !ok || slices.ContainsFunc(list, func(v string) bool { return !isName(v) }) || hasDuplicate(list) {
			c.err("`values` は重ならない名前（小文字・数字・`_`）の並び")
			return nil, c.problems
		}
		values = list
	}
	fields := fieldsFor(id, kind)
	loose := slices.Equal(fields.required, ruleLooseFields.required) && slices.Equal(fields.optional, ruleLooseFields.optional)
	known := append(append(slices.Clone(fields.required), fields.optional...), keyKind, keyValues)
	var unknown []string
	for k := range m.All() {
		if s, ok := k.(string); ok && slices.Contains(known, s) {
			continue
		}
		unknown = append(unknown, pyfmt.Str(k))
	}
	if len(unknown) > 0 {
		slices.Sort(unknown)
		unknown = slices.Compact(unknown)
		c.err(string(kind) + " の欄に無い: " + strings.Join(unknown, ", "))
	}
	for _, name := range fields.required {
		if !m.Has(name) {
			c.err("欄 `" + name + "` が無い")
		}
	}
	out := map[string]value{}
	for _, name := range append(slices.Clone(fields.required), fields.optional...) {
		raw, present := m.Lookup(name)
		if !present {
			continue
		}
		if loose && !slices.Contains(fields.required, name) && blank(raw) {
			continue // 言うことが無い欄は省くのと同じ（`RULE_` の文だけ）
		}
		before := len(c.problems)
		got, ok := c.checkField(name, raw)
		if !ok && len(c.problems) == before {
			c.err("`" + name + "` を読めない")
		}
		if ok {
			out[name] = got
		}
	}
	var onlyText, onlyValues []string
	for name := range c.used {
		if !slices.Contains(values, name) {
			onlyText = append(onlyText, name)
		}
	}
	for _, name := range values {
		if !c.used[name] {
			onlyValues = append(onlyValues, name)
		}
	}
	if len(onlyText) > 0 || len(onlyValues) > 0 {
		slices.Sort(onlyText)
		slices.Sort(onlyValues)
		var parts []string
		if len(onlyText) > 0 {
			parts = append(parts, "文面にだけある: "+strings.Join(onlyText, ", "))
		}
		if len(onlyValues) > 0 {
			parts = append(parts, "`values` にだけある: "+strings.Join(onlyValues, ", "))
		}
		c.err("差し込みの名前が `values` と合わない（" + strings.Join(parts, "。") + "）")
	}
	if c.sizes > limitEntry {
		c.err(fmt.Sprintf("1 件の文の長さの合計が %d 字を超える（%d 字）", limitEntry, c.sizes))
	}
	if len(c.problems) > 0 {
		return nil, c.problems
	}
	return &Entry{id: id, kind: kind, Values: values, path: path, fields: out}, nil
}

func hasDuplicate(list []string) bool {
	seen := map[string]bool{}
	for _, v := range list {
		if seen[v] {
			return true
		}
		seen[v] = true
	}
	return false
}

// parseFile はファイルの外側（version と messages）を読む。
func parseFile(text, path string) (*yaml.Map, []rules.Problem) {
	where := basename(path)
	fail := func(detail string) (*yaml.Map, []rules.Problem) {
		return nil, []rules.Problem{{Severity: rules.Error, Rule: "(messages)", Detail: where + ": " + detail}}
	}
	data, err := yaml.Load(text)
	if err != nil {
		return fail("読めない: " + err.Error())
	}
	m, ok := data.(*yaml.Map)
	if !ok || !pyfmt.EqualsInt(m.Get("version"), version) {
		return fail(fmt.Sprintf("`version: %d` の表で書く", version))
	}
	body, ok := m.Get("messages").(*yaml.Map)
	if !ok {
		return fail("`messages` が表でない")
	}
	return body, nil
}

// CheckText は文面ファイル 1 本の中身を、1 件ずつ単独で検査する。下書きの取り込み前の検査にも使う。
// 誤りのある 1 件は結果に入らない。
func CheckText(text, path string) (map[string]*Entry, []rules.Problem) {
	list, problems := checkFile(text, path)
	entries := map[string]*Entry{}
	for _, e := range list {
		entries[e.id] = e
	}
	return entries, problems
}

// checkFile は CheckText の件を書いた順に返す。
func checkFile(text, path string) ([]*Entry, []rules.Problem) {
	body, problems := parseFile(text, path)
	var entries []*Entry
	for k, raw := range body.All() {
		entry, found := checkEntry(pyfmt.Str(k), raw, path)
		problems = append(problems, found...)
		if entry != nil {
			entries = append(entries, entry)
		}
	}
	return entries, problems
}
