package argparse

import "strings"

// Kind は引数を読んだときの振る舞い（store・store_true・append）。
type Kind int

const (
	store     Kind = iota // 値を 1 つ（nargs によっては並び）置く
	StoreTrue             // 値を取らず True を置く。既定は False
	Append                // 値を並びに足す
)

// Nargs は取る値の数。0 は既定の「1 つ」、正の数はその数の並び。
type Nargs int

const (
	one        Nargs = 0
	Optional   Nargs = -1 // "?"
	ZeroOrMore Nargs = -2 // "*"
	noArgs     Nargs = -3 // store_true
)

// Type は文字列を値に変える関数と、誤りの文に出す名前。
// convert が nil なら文字列のまま置く。
type Type struct {
	name    string
	convert func(string) (any, error)
}

// Arg は add_argument の引数。Flags が "-" で始まらない 1 語なら位置引数、それ以外はオプション。
// Default の nil は None。[]string は []any にして持つ。
type Arg struct {
	Flags   []string
	Kind    Kind
	Nargs   Nargs
	Const   any
	Default any
	Type    Type
	Metavar []string // 1 つなら文字列、2 つ以上ならタプル
	dest    string   // 空なら Flags から決める
}

// Action は足した 1 つの引数。上の段は名前と既定値だけを読む。
type Action struct {
	OptionStrings []string
	Dest          string
	Default       any

	kind     Kind
	nargs    Nargs
	konst    any
	typ      Type
	metavar  []string
	required bool
}

// Parser は ArgumentParser(prog=…, add_help=False, allow_abbrev=False)。
type Parser struct {
	prog    string
	actions []*Action
	options map[string]*Action
}

// New は prog の名前でパーサを作る。
func New(prog string) *Parser {
	return &Parser{prog: prog, options: map[string]*Action{}}
}

// Add は引数を足す。組み合わせの誤り（argparse なら例外で断る形）は確かめない。固定の組み立てで使う。
func (p *Parser) Add(arg Arg) *Action {
	a := &Action{kind: arg.Kind, nargs: arg.Nargs, konst: arg.Const, typ: arg.Type, metavar: arg.Metavar, Default: arg.Default}
	if list, ok := arg.Default.([]string); ok {
		a.Default = anyList(list)
	}
	if arg.Kind == StoreTrue {
		a.nargs = noArgs
		a.konst = true
		if a.Default == nil {
			a.Default = false
		}
	}
	if len(arg.Flags) == 1 && !strings.HasPrefix(arg.Flags[0], "-") {
		a.Dest = arg.Flags[0]
		a.required = a.nargs != Optional && a.nargs != ZeroOrMore && a.nargs != noArgs
	} else {
		a.OptionStrings = append([]string(nil), arg.Flags...)
		a.Dest = optionDest(arg.Flags)
		for _, s := range arg.Flags {
			p.options[s] = a
		}
	}
	if arg.dest != "" {
		a.Dest = arg.dest
	}
	p.actions = append(p.actions, a)
	return a
}

// optionDest は '--foo-bar' → 'foo_bar'、長い名前が無ければ '-x' → 'x'。
func optionDest(flags []string) string {
	name := flags[0]
	for _, s := range flags {
		if len(s) > 1 && s[1] == '-' {
			name = s
			break
		}
	}
	return strings.ReplaceAll(strings.TrimLeft(name, "-"), "-", "_")
}

// Actions は足した順の引数。
func (p *Parser) Actions() []*Action { return p.actions }

// HasOption は s がオプションの名前そのものか。
func (p *Parser) HasOption(s string) bool { return p.Option(s) != nil }

// Option は名前が s のオプション。無ければ nil。
func (p *Parser) Option(s string) *Action { return p.options[s] }

// name は誤りの文に出す引数の名前。
func (a *Action) name() string {
	if len(a.OptionStrings) > 0 {
		return strings.Join(a.OptionStrings, "/")
	}
	switch {
	case len(a.metavar) == 1:
		return a.metavar[0]
	case len(a.metavar) == 2 && a.nargs == ZeroOrMore:
		return a.metavar[0] + "[, " + a.metavar[1] + "]"
	case len(a.metavar) > 1:
		return strings.Join(a.metavar, ", ")
	}
	return a.Dest
}

func anyList(list []string) []any {
	out := make([]any, len(list))
	for i, s := range list {
		out[i] = s
	}
	return out
}
