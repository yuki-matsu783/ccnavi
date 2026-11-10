package argparse

import (
	"fmt"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"ccnavi/internal/infra/fault"
	"ccnavi/internal/infra/pyfmt"
)

// Error は読めなかった引数。Message は誤りの文（`argument --x: expected one argument`）。
type Error struct{ fault.Detail }

func argError(a *Action, format string, args ...any) *Error {
	msg := fmt.Sprintf(format, args...)
	if a == nil {
		return &Error{fault.Plain(msg)}
	}
	return &Error{fault.Plain("argument " + a.name() + ": " + msg)}
}

// Int は整数を読む type。
var Int = Type{name: "int", convert: func(s string) (any, error) {
	n, err := pyfmt.ParseInt(s)
	if err != nil {
		return nil, err
	}
	return n, nil
}}

// optTuple は 1 語をオプションと読んだ形。explicit は `--x=v` や `-xv` の v。
type optTuple struct {
	action      *Action
	option      string
	sepEq       bool
	hasExplicit bool
	explicit    string
}

// Parse は parse_args。読めた値を返し、読めない語・余った語は *Error で返す。
func (p *Parser) Parse(args []string) (Namespace, error) {
	ns := Namespace{}
	for _, a := range p.actions {
		if _, ok := ns[a.Dest]; !ok {
			ns[a.Dest] = a.Default
		}
	}
	st := &parseState{p: p, ns: ns, args: args, optAt: map[int]optTuple{}, seen: map[*Action]bool{}}
	extras, err := st.run()
	if err != nil {
		return nil, err
	}
	if len(extras) > 0 {
		return nil, &Error{fault.Plain("unrecognized arguments: " + strings.Join(extras, " "))}
	}
	return ns, nil
}

type parseState struct {
	p           *Parser
	ns          Namespace
	args        []string
	pattern     string // 1 語に 1 字: O（オプション）・A（値）・-（最初の "--"）
	optAt       map[int]optTuple
	seen        map[*Action]bool
	extras      []string
	positionals []*Action
}

// run は語を先頭から読み、読み取れなかった語（余り）を返す。
// 先に全語を O（オプション）・A（値）・-（最初の "--" 以降は全部値）の 1 字ずつの文字列にし、
// 位置引数が取れる語数は、その文字列に正規表現を当てて決める（matchPartial）。
func (st *parseState) run() ([]string, error) {
	// 1. 各語をオプションか値かに分類して、パターンの文字列を作る。
	var pat strings.Builder
	maxOpt := -1
	for i, s := range st.args {
		if s == "--" {
			pat.WriteByte('-')
			pat.WriteString(strings.Repeat("A", len(st.args)-i-1))
			break
		}
		if t, ok := st.p.parseOptional(s); ok {
			st.optAt[i] = t
			maxOpt = i
			pat.WriteByte('O')
		} else {
			pat.WriteByte('A')
		}
	}
	st.pattern = pat.String()
	// 2. 位置引数の引数定義を集める。
	for _, a := range st.p.actions {
		if len(a.OptionStrings) == 0 {
			st.positionals = append(st.positionals, a)
		}
	}

	// 3. オプションの間にはさまれた位置引数を先に取り、次にオプションを取る、を最後のオプションまで繰り返す。
	start := 0
	for start <= maxOpt {
		next := start
		for next <= maxOpt {
			if _, ok := st.optAt[next]; ok {
				break
			}
			next++
		}
		if start != next {
			end, err := st.consumePositionals(start)
			if err != nil {
				return nil, err
			}
			if end > start {
				start = end
				continue
			}
			start = end
		}
		if _, ok := st.optAt[start]; !ok {
			st.extras = append(st.extras, st.args[start:next]...)
			start = next
		}
		var err error
		if start, err = st.consumeOptional(start); err != nil {
			return nil, err
		}
	}
	// 4. 最後のオプションより後ろの位置引数を取り、残りを余りにする。
	stop, err := st.consumePositionals(start)
	if err != nil {
		return nil, err
	}
	st.extras = append(st.extras, st.args[stop:]...)

	// 5. 渡されなかった引数は、必須なら不足として集め、文字列の既定値は型を通して置く。
	var required []string
	for _, a := range st.p.actions {
		if st.seen[a] {
			continue
		}
		if a.required {
			required = append(required, a.name())
			continue
		}
		// 渡されなかった引数の文字列の既定値は、ここで型を通す（渡されたときは通さない）。
		if s, ok := a.Default.(string); ok && a.typ.convert != nil {
			v, err := convert(a, s)
			if err != nil {
				return nil, err
			}
			st.ns[a.Dest] = v
		}
	}
	if len(required) > 0 {
		return nil, argError(nil, "the following arguments are required: %s", strings.Join(required, ", "))
	}
	return st.extras, nil
}

// parseOptional は 1 語がオプションか（前方一致は受けない）。
// action が nil の形は「オプションに見えるが、このパーサに無い」で、余った語になる。
func (p *Parser) parseOptional(s string) (optTuple, bool) {
	if s == "" || s[0] != '-' {
		return optTuple{}, false
	}
	if a, ok := p.options[s]; ok {
		return optTuple{action: a, option: s}, true
	}
	if utf8.RuneCountInString(s) == 1 {
		return optTuple{}, false
	}
	if name, value, found := strings.Cut(s, "="); found {
		if a, ok := p.options[name]; ok {
			return optTuple{action: a, option: name, sepEq: true, hasExplicit: true, explicit: value}, true
		}
	}
	if s[1] != '-' {
		_, size := utf8.DecodeRuneInString(s[1:])
		short := s[:1+size]
		if a, ok := p.options[short]; ok {
			return optTuple{action: a, option: short, hasExplicit: true, explicit: s[1+size:]}, true
		}
	}
	if negativeNumber(s) {
		return optTuple{}, false
	}
	if strings.Contains(s, " ") {
		return optTuple{}, false
	}
	return optTuple{option: s}, true
}

// negativeNumber は負の数に見る語（`^-\d+$|^-\d*\.\d+$` に当たる形）か。\d は Unicode の 10 進の数字で、$ は末尾の改行の手前にも当たる。
func negativeNumber(s string) bool {
	body := strings.TrimSuffix(s[1:], "\n")
	whole, frac, dotted := strings.Cut(body, ".")
	if !dotted {
		return whole != "" && allDigits(whole)
	}
	return frac != "" && allDigits(whole) && allDigits(frac)
}

// allDigits は s が Unicode の 10 進の数字（Nd。全角の数字も入る）だけでできているか。空も真になるので、
// 空かどうかは呼び手が先に見る。ASCII の数字だけを見る pyfmt.IsASCIIDigits や、上付き・丸数字も数える
// shell の isDigits とは答えが違うので、寄せない。
func allDigits(s string) bool {
	for _, r := range s {
		if !unicode.IsDigit(r) {
			return false
		}
	}
	return true
}

type taken struct {
	action *Action
	args   []string
	option string
}

// consumeOptional は start のオプションと、それが取る値を読み、次に読む位置を返す。
// `-xyz` のような連結は、値を取らない短いオプションを 1 字ずつ todo に積んで読む。
// 値の受け渡しは最後にまとめて行う。
func (st *parseState) consumeOptional(start int) (int, error) {
	t := st.optAt[start]
	var todo []taken
	stop := 0
	for {
		if t.action == nil {
			st.extras = append(st.extras, st.args[start])
			return start + 1, nil
		}
		if !t.hasExplicit {
			from := start + 1
			n, err := matchArgument(t.action, st.pattern[from:])
			if err != nil {
				return 0, err
			}
			stop = from + n
			todo = append(todo, taken{t.action, st.args[from:stop], t.option})
			break
		}
		n, err := matchArgument(t.action, "A")
		if err != nil {
			return 0, err
		}
		if n == 0 && t.option[1] != '-' && t.explicit != "" {
			// `-xyz` を `-x -y -z` と読む。値を取らない短いオプションの後ろだけ。
			if t.sepEq || t.explicit[0] == '-' {
				return 0, argError(t.action, "ignored explicit argument %s", pyfmt.Quote(t.explicit))
			}
			todo = append(todo, taken{t.action, nil, t.option})
			_, size := utf8.DecodeRuneInString(t.explicit)
			short := "-" + t.explicit[:size]
			a, ok := st.p.options[short]
			if !ok {
				st.extras = append(st.extras, "-"+t.explicit)
				stop = start + 1
				break
			}
			rest := t.explicit[size:]
			t = optTuple{action: a, option: short}
			switch {
			case rest == "":
			case rest[0] == '=':
				t.sepEq, t.hasExplicit, t.explicit = true, true, rest[1:]
			default:
				t.hasExplicit, t.explicit = true, rest
			}
			continue
		}
		if n == 1 {
			stop = start + 1
			todo = append(todo, taken{t.action, []string{t.explicit}, t.option})
			break
		}
		return 0, argError(t.action, "ignored explicit argument %s", pyfmt.Quote(t.explicit))
	}
	for _, d := range todo {
		if err := st.take(d.action, d.args); err != nil {
			return 0, err
		}
	}
	return stop, nil
}

// matchArgument はオプションが続く語から取る数。
func matchArgument(a *Action, pattern string) (int, error) {
	switch a.nargs {
	case noArgs:
		return 0, nil
	case one:
		if strings.HasPrefix(pattern, "A") {
			return 1, nil
		}
		return 0, argError(a, "expected one argument")
	case Optional:
		if strings.HasPrefix(pattern, "A") {
			return 1, nil
		}
		return 0, nil
	case ZeroOrMore:
		return len(pattern) - len(strings.TrimLeft(pattern, "A")), nil
	}
	n := int(a.nargs)
	if len(pattern) >= n && strings.Trim(pattern[:n], "AO") == "" {
		return n, nil
	}
	if n == 1 {
		return 0, argError(a, "expected 1 argument")
	}
	return 0, argError(a, "expected %d arguments", n)
}

// positionalPattern は位置引数の式。
func positionalPattern(a *Action) string {
	switch a.nargs {
	case one:
		return "(-*A-*)"
	case Optional:
		return "(-*A?-*)"
	case ZeroOrMore:
		return "(-*[A-]*)"
	}
	return fmt.Sprintf("((?:-*A){%d}-*)", int(a.nargs))
}

// matchPartial は、残りの位置引数の頭からできるだけ多くに語を割り当てる。
func matchPartial(actions []*Action, pattern string) []int {
	for i := len(actions); i > 0; i-- {
		var expr strings.Builder
		expr.WriteByte('^')
		for _, a := range actions[:i] {
			expr.WriteString(positionalPattern(a))
		}
		m := regexp.MustCompile(expr.String()).FindStringSubmatchIndex(pattern)
		if m == nil {
			continue
		}
		var counts []int
		for g := 1; g <= i; g++ {
			counts = append(counts, m[2*g+1]-m[2*g])
		}
		if m[1] < len(pattern) && pattern[m[1]] == 'O' {
			for len(counts) > 0 && counts[len(counts)-1] == 0 {
				counts = counts[:len(counts)-1]
			}
		}
		return counts
	}
	return nil
}

func (st *parseState) consumePositionals(start int) (int, error) {
	counts := matchPartial(st.positionals, st.pattern[start:])
	for i, n := range counts {
		a := st.positionals[i]
		args := append([]string(nil), st.args[start:start+n]...)
		if strings.Contains(st.pattern[start:start+n], "-") {
			for j, s := range args {
				if s == "--" {
					args = append(args[:j], args[j+1:]...)
					break
				}
			}
		}
		start += n
		if err := st.take(a, args); err != nil {
			return 0, err
		}
	}
	st.positionals = st.positionals[len(counts):]
	return start, nil
}

func (st *parseState) take(a *Action, args []string) error {
	st.seen[a] = true
	v, err := values(a, args)
	if err != nil {
		return err
	}
	switch a.kind {
	case StoreTrue:
		st.ns[a.Dest] = a.konst
	case Append:
		var items []any
		if old, ok := st.ns[a.Dest].([]any); ok {
			items = append(items, old...)
		}
		st.ns[a.Dest] = append(items, v)
	default:
		st.ns[a.Dest] = v
	}
	return nil
}

// values は語を値にする。
func values(a *Action, args []string) (any, error) {
	positional := len(a.OptionStrings) == 0
	switch {
	case len(args) == 0 && a.nargs == Optional:
		v := a.konst
		if positional {
			v = a.Default
		}
		if s, ok := v.(string); ok {
			return convert(a, s)
		}
		return v, nil
	case len(args) == 0 && a.nargs == ZeroOrMore && positional:
		if a.Default != nil {
			return a.Default, nil
		}
		return []any{}, nil
	case len(args) == 1 && (a.nargs == one || a.nargs == Optional):
		return convert(a, args[0])
	}
	out := []any{}
	for _, s := range args {
		v, err := convert(a, s)
		if err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, nil
}

func convert(a *Action, s string) (any, error) {
	if a.typ.convert == nil {
		return s, nil
	}
	v, err := a.typ.convert(s)
	if err != nil {
		return nil, argError(a, "invalid %s value: %s", a.typ.name, pyfmt.Quote(s))
	}
	return v, nil
}
