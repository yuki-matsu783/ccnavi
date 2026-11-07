package texts

import (
	"fmt"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/rules"
)

// 置き場はワークスペースルートの `.ccnavi/messages/*.yml` だけ。レイヤーごとの messages は読まない
// （文だけを替える口は持たない）。
const (
	MessagesDir = "messages"
	suffix      = ".yml"
)

// Values は差し込む値（名前 → 値）。Variants は場合分けした欄の選び方（欄の名前 → 場合）。
type (
	Values   map[string]string
	Variants map[string]string
)

// Error は文を組み立てられなかった理由（値が足りない・場合を選べない・文面が無い）。
type Error struct{ msg string }

func (e *Error) Error() string { return e.msg }

func failf(format string, args ...any) error { return &Error{msg: fmt.Sprintf(format, args...)} }

// Catalog は置き場の全件。
type Catalog struct {
	Entries  map[string]*Entry
	Problems []rules.Problem
}

// dir は文面ファイルの置き場（`<root>/.ccnavi/messages`）。
func dir(root string) string {
	return ospath.Join(root, config.DefaultProjectHome, MessagesDir)
}

func fileNames(dir string) ([]string, bool) {
	names, err := ospath.DiskListdir(dir)
	if err != nil {
		return nil, false
	}
	var out []string
	for _, n := range names {
		if strings.HasSuffix(n, suffix) {
			out = append(out, n)
		}
	}
	slices.Sort(out)
	return out, true
}

// Load は置き場の文面を全件読む（lint と点検が使う。1 件を引く Say は Find を使う）。失敗しない。
// 別のファイルに同じ識別子があれば、名前順で後ろを採らない。
func Load(root string) *Catalog {
	c := &Catalog{Entries: map[string]*Entry{}}
	dir := dir(root)
	names, ok := fileNames(dir)
	if !ok {
		return c // 置き場が無いのは正常
	}
	for _, name := range names {
		path := ospath.Join(dir, name)
		text, ok := fsio.ReadText(path, fsio.Strict)
		if !ok {
			c.Problems = append(c.Problems, rules.Problem{Severity: rules.Error, Rule: "(messages)", Detail: name + ": 読めない"})
			continue
		}
		found, notes := checkFile(text, path)
		c.Problems = append(c.Problems, notes...)
		for _, e := range found {
			if _, dup := c.Entries[e.id]; dup {
				c.Problems = append(c.Problems, rules.Problem{Severity: rules.Error, Rule: e.id, Detail: name + ": 別のファイルにも同じ識別子がある。後ろは採らない"})
				continue
			}
			c.Entries[e.id] = e
		}
	}
	return c
}

// Find は識別子の 1 件を返す。識別子の字を含むファイルだけを名前順に YAML として読み、検査を通る形で持つ
// 最初のファイルの 1 件を採る。全件を読む Load と同じ 1 件になる。無い・検査を通らないなら nil を返す。
func Find(root, id string) *Entry {
	dir := dir(root)
	names, ok := fileNames(dir)
	if !ok {
		return nil
	}
	for _, name := range names {
		path := ospath.Join(dir, name)
		text, ok := fsio.ReadText(path, fsio.Strict)
		if !ok || !strings.Contains(text, id) {
			continue
		}
		if entries, _ := CheckText(text, path); entries[id] != nil {
			return entries[id]
		}
	}
	return nil
}

// cleanValue は値を文に入れられる形にする。制御文字などを空白にして、行を足せないようにする。
func cleanValue(v string) string {
	return ospath.Strip(unsafeChars.ReplaceAllString(v, " "))
}

// fill は差し込みを置き換える。値が足りなければ *Error。
func fill(text string, values Values, root string) (string, error) {
	var b strings.Builder
	last := 0
	for _, m := range placeholders.FindAllStringSubmatchIndex(text, -1) {
		b.WriteString(text[last:m[0]])
		last = m[1]
		whole := text[m[0]:m[1]]
		switch {
		case whole == "{{":
			b.WriteString("{")
			continue
		case whole == "}}":
			b.WriteString("}")
			continue
		case m[2] < 0:
			return "", failf("対にならない `%s`", whole)
		}
		inner := text[m[2]:m[3]]
		if name, ok := strings.CutPrefix(inner, scriptPrefix); ok {
			if root == "" {
				return "", failf("sh の差し込みにはワークスペースルートが要る")
			}
			b.WriteString(config.ScriptCommand(root, name))
			continue
		}
		name, mod, _ := strings.Cut(inner, ":")
		v, ok := values[name]
		if !ok {
			return "", failf("値 %s が渡されていない", name)
		}
		v = cleanValue(v)
		switch mod {
		case "sh":
			v = config.ShellWord(v)
		case "path":
			v = config.ShellPath(v)
		}
		b.WriteString(v)
	}
	b.WriteString(text[last:])
	return b.String(), nil
}

func (e *Entry) pick(name string, variants Variants) (string, error) {
	v := e.fields[name]
	if v.byKey == nil {
		return v.text, nil
	}
	c := variants[name]
	text, ok := v.byKey[c]
	if !ok {
		return "", failf("%s: `%s` の場合 %s を選べない（%s）", e.id, name, pyfmt.Repr(c), strings.Join(v.cases, " "))
	}
	return text, nil
}

// Render は文を組み立てる。値が足りない・場合を選べないときは *Error。
func Render(e *Entry, values Values, variants Variants, root string) (string, error) {
	var missing []string
	for _, name := range e.Values {
		if _, ok := values[name]; !ok {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		return "", failf("%s: 値が足りない: %s", e.id, strings.Join(missing, ", "))
	}
	one := func(name string) (string, error) {
		text, err := e.pick(name, variants)
		if err != nil {
			return "", err
		}
		return fill(text, values, root)
	}
	noted := func(name string) ([]string, error) {
		if !e.has(name) {
			return nil, nil
		}
		text, err := one(name)
		if err != nil {
			return nil, err
		}
		var out []string
		for _, ln := range ospath.Splitlines(text) {
			if ospath.Strip(ln) != "" {
				out = append(out, NoteLabel+ln)
			}
		}
		return out, nil
	}
	var lines []string
	switch e.kind {
	case kindChoices:
		lines = append(lines, choicesHead)
		for i, item := range e.fields[fieldItems].items {
			text, err := fill(item, values, root)
			if err != nil {
				return "", err
			}
			lines = append(lines, fmt.Sprintf("  %d. %s", i+1, text))
		}
		return strings.Join(lines, "\n"), nil
	case kindNote:
		notes, err := noted(fieldNote)
		return strings.Join(notes, "\n"), err
	}
	if e.has(fieldWhat) {
		text, err := one(fieldWhat)
		if err != nil {
			return "", err
		}
		lines = append(lines, text)
	}
	for _, name := range []string{fieldWhy, fieldNext, fieldNoBypass} {
		if e.has(name) {
			text, err := one(name)
			if err != nil {
				return "", err
			}
			lines = append(lines, labels[name]+text)
		}
	}
	notes, err := noted(fieldNote)
	if err != nil {
		return "", err
	}
	return strings.Join(append(lines, notes...), "\n"), nil
}

// fileHint は既定の文で名指しする置き場。
const fileHint = config.DefaultProjectHome + "/" + MessagesDir + "/"

// Fallback は文を引けなかったときの短い既定の文。個々の文は持たず、種類ごとの数行だけ。
// 文を引けなかったことと、ユーザに確かめてもらう次の一手を言う。
func Fallback(id string) string {
	kind := kindOfID(id)
	gone := "文面（" + id + "）を引けませんでした。"
	ask := "ユーザに、文面ファイル（" + fileHint + "）が読めるかを `" + config.Launcher + " --lint` で確かめてもらい、返事を待つ"
	switch kind {
	case kindNote:
		return NoteLabel + gone
	case kindNotice:
		return gone + "\n" + NextLabel + ask
	case kindChoices:
		return choicesHead + "\n  1. " + ask + "（" + gone + "）"
	case kindNext:
		return NextLabel + ask + "（" + gone + "）"
	}
	lines := []string{gone + "止めた判定は変わらず、短い既定の文で伝えます。", NextLabel + ask}
	if kind == kindDeny {
		lines = append(lines, noBypassLabel+"言い換えや別の経路でも、同じゲートに当たります。")
	}
	return strings.Join(lines, "\n")
}

// Say は識別子の文を組み立てる。引けなければ既定の文（Fallback）。失敗しない。
func Say(root, id string, values Values, variants Variants) string {
	entry := Find(root, id)
	if entry == nil {
		return Fallback(id)
	}
	text, err := Render(entry, values, variants, root)
	if err != nil {
		return Fallback(id)
	}
	return text
}

// ラッパーの有無で文が分かれる欄の場合の名前。
const (
	caseWrapper = "wrapper"
	casePlain   = "plain"
	valueGit    = "git"
)

// SpecOptions は組み込みのルールの文の指定。Git は、文に出す git の打ち方（文が {git} を使うときだけ渡す）。
// Cased はラッパーの有無で文が分かれる欄（Wrapper なら wrapper、でなければ plain を選ぶ）。
// Variants はそれ以外の場合の選び方。
type SpecOptions struct {
	Git      string
	Wrapper  bool
	Cased    []string
	Variants Variants
}

// Spec は組み込みのルールが持つ文の指定（Python の spec）。
func Spec(id string, o SpecOptions) rules.TextRef {
	ref := rules.TextRef{ID: id, Values: map[string]string{}, Variants: map[string]string{}}
	if o.Git != "" {
		ref.Values[valueGit] = o.Git
	}
	for _, name := range o.Cased {
		ref.Variants[name] = casePlain
		if o.Wrapper {
			ref.Variants[name] = caseWrapper
		}
	}
	for k, v := range o.Variants {
		ref.Variants[k] = v
	}
	return ref
}

// Spoken はルールがモデルに渡す文。どの文面を使うかは rules.Rule.Speech が決め、文面ファイルの文はここで引く。
func Spoken(r *rules.Rule) string {
	ref, text := r.Speech()
	if ref == nil {
		return text
	}
	return Say(r.Root, ref.ID, ref.Values, ref.Variants)
}

// LinesOf は出力の `次:` の行（テストと点検が数える）。
func LinesOf(text string) []string {
	var out []string
	for _, ln := range ospath.Splitlines(text) {
		if strings.HasPrefix(ln, NextLabel) {
			out = append(out, ln)
		}
	}
	return out
}
