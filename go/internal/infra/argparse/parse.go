package argparse

import (
	"fmt"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"ccnavi/internal/infra/pyfmt"
)

// Error は読めなかった引数。Message は Python の str(ArgumentError)（`argument --x: expected one argument`）。
type Error struct{ Message string }

func (e *Error) Error() string { return e.Message }

func argError(a *Action, format string, args ...any) *Error {
	msg := fmt.Sprintf(format, args...)
	if a == nil {
		return &Error{Message: msg}
	}
	return &Error{Message: "argument " + a.name() + ": " + msg}
}

// Int は Python の type=int。
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
		return nil, &Error{Message: "unrecognized arguments: " + strings.Join(extras, " ")}
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

func (st *parseState) run() ([]string, error) {
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
	for _, a := range st.p.actions {
		if len(a.OptionStrings) == 0 {
			st.positionals = append(st.positionals, a)
		}
	}

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
	stop, err := st.consumePositionals(start)
	if err != nil {
		return nil, err
	}
	st.extras = append(st.extras, st.args[stop:]...)

	var required []string
	for _, a := range st.p.actions {
		if st.seen[a] {
			continue
		}
		if a.required {
			required = append(required, a.name())
			continue
		}
		// 渡されなかった引数の文字列の既定値は、ここで型を通す（Python と同じく、渡されたときは通さない）。
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

// parseOptional は 1 語がオプションか（Python の _parse_optional。前方一致は受けない）。
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

// negativeNumber は Python の `^-\d+$|^-\d*\.\d+$`（\d は Unicode の 10 進の数字、$ は末尾の改行の手前にも当たる）。
func negativeNumber(s string) bool {
	body := strings.TrimSuffix(s[1:], "\n")
	whole, frac, dotted := strings.Cut(body, ".")
	if !dotted {
		return whole != "" && allDigits(whole)
	}
	return frac != "" && allDigits(whole) && allDigits(frac)
}

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

// matchArgument はオプションが続く語から取る数（Python の _match_argument）。
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

// positionalPattern は位置引数の式（Python の _get_nargs_pattern の位置引数の側）。
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

// matchPartial は、残りの位置引数の頭からできるだけ多くに語を割り当てる（Python の _match_arguments_partial）。
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

// values は語を値にする（Python の _get_values）。
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
