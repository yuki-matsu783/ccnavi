package cli

import (
	"fmt"
	"strconv"
	"strings"
	"unicode"

	"ccnavi/internal/infra/pyfmt"
)

// argKind は引数を 1 つ読んだときの振る舞い。
type argKind int

const (
	argValue    argKind = iota // 値を 1 つ取って置く
	argSwitch                  // 値を取らず true を置く
	argList                    // 値を 1 つ取って並びに足す（2 度来たら 2 つ並ぶ）
	argInt                     // 値を 1 つ取って整数にする
	argOptional                // 値は付けても付けなくてもよい（付けなければ bareYes）
	argPair                    // 値を 2 つ取って並びで置く
)

// action は 1 つのオプション。値は Namespace の Dest の欄に置く。
type action struct {
	OptionStrings []string
	Dest          string
	Default       any
	kind          argKind
}

// argReader は argv を読む。位置引数は区別せず、すべて `command` の並びに入れる。
// 受けるのはオプションの綴りそのものと `--x=値` の形だけ（前方一致と、短いオプションの連結は受けない）。
type argReader struct {
	actions []*action
	options map[string]*action
}

func newArgReader() *argReader { return &argReader{options: map[string]*action{}} }

// add はオプションを足す。dest は最初の長い綴りから決める（`--foo-bar` は foo_bar）。
func (p *argReader) add(kind argKind, def any, flags ...string) {
	long := flags[0]
	for _, f := range flags {
		if strings.HasPrefix(f, "--") {
			long = f
			break
		}
	}
	a := &action{OptionStrings: flags, Dest: destOf(long), Default: def, kind: kind}
	for _, f := range flags {
		p.options[f] = a
	}
	p.actions = append(p.actions, a)
}

// Actions は足した順のオプション。
func (p *argReader) Actions() []*action { return p.actions }

// Option は綴りが s のオプション。無ければ nil。
func (p *argReader) Option(s string) *action { return p.options[s] }

// HasOption は s がオプションの綴りそのものか。
func (p *argReader) HasOption(s string) bool { return p.options[s] != nil }

// optionLike は語がオプションに見えるか。`-` だけの語、負の数、空白を含む語は値として読む。
func optionLike(s string) bool {
	if len(s) < 2 || s[0] != '-' || strings.Contains(s, " ") {
		return false
	}
	digits := func(t string) bool {
		return t != "" && strings.IndexFunc(t, func(r rune) bool { return !unicode.IsDigit(r) }) < 0
	}
	whole, frac, dotted := strings.Cut(s[1:], ".")
	if !dotted && digits(whole) || dotted && (whole == "" || digits(whole)) && digits(frac) {
		return false
	}
	return true
}

// valueWord は args[i] が、直前のオプションの値になれる語か。
func valueWord(args []string, i int) bool {
	return i < len(args) && args[i] != "--" && !optionLike(args[i])
}

// Parse は argv を読む。誤りは最初のものを返す（知らないオプションだけは、読み終えてからまとめて言う）。
func (p *argReader) Parse(args []string) (Namespace, error) {
	ns := Namespace{"command": []any{}}
	for _, a := range p.actions {
		ns[a.Dest] = a.Default
	}
	var command []any
	var unknown []string
	for i := 0; i < len(args); i++ {
		w := args[i]
		if w == "--" {
			for _, rest := range args[i+1:] {
				command = append(command, rest)
			}
			break
		}
		a, explicit, hasExplicit := p.options[w], "", false
		if a == nil {
			if name, v, found := strings.Cut(w, "="); found {
				a, explicit, hasExplicit = p.options[name], v, true
			}
		}
		if a == nil {
			if optionLike(w) {
				unknown = append(unknown, w)
			} else {
				command = append(command, w)
			}
			continue
		}
		fail := func(format string, v ...any) error {
			return fmt.Errorf("argument %s: %s", strings.Join(a.OptionStrings, "/"), fmt.Sprintf(format, v...))
		}
		switch a.kind {
		case argSwitch:
			if hasExplicit {
				return nil, fail("ignored explicit argument %q", explicit)
			}
			ns[a.Dest] = true
		case argPair:
			if hasExplicit || !valueWord(args, i+1) || !valueWord(args, i+2) {
				return nil, fail("expected 2 arguments")
			}
			ns[a.Dest] = []any{args[i+1], args[i+2]}
			i += 2
		case argOptional:
			switch {
			case hasExplicit:
				ns[a.Dest] = explicit
			case valueWord(args, i+1):
				i++
				ns[a.Dest] = args[i]
			default:
				ns[a.Dest] = bareYes
			}
		default:
			value := explicit
			if !hasExplicit {
				if !valueWord(args, i+1) {
					return nil, fail("expected one argument")
				}
				i++
				value = args[i]
			}
			switch a.kind {
			case argInt:
				n, err := strconv.Atoi(value)
				if err != nil {
					return nil, fail("invalid int value: %q", value)
				}
				ns[a.Dest] = n
			case argList:
				old, _ := ns[a.Dest].([]any)
				ns[a.Dest] = append(old[:len(old):len(old)], value)
			default:
				ns[a.Dest] = value
			}
		}
	}
	if len(unknown) > 0 {
		return nil, fmt.Errorf("unrecognized arguments: %s", strings.Join(unknown, " "))
	}
	if command != nil {
		ns["command"] = command
	}
	return ns, nil
}

// Namespace は読めた値（dest から値への対応）。値は nil・bool・string・int・[]any（並び）。
type Namespace map[string]any

// IsNone は値が無い（nil）か。
func (ns Namespace) IsNone(dest string) bool { return ns[dest] == nil }

// Str は文字列の値。nil と文字列でない値は空。
func (ns Namespace) Str(dest string) string {
	s, _ := ns[dest].(string)
	return s
}

// Truthy は値が真か。nil・false・0・空は偽。
func (ns Namespace) Truthy(dest string) bool { return pyfmt.Truthy(ns[dest]) }

// Int は整数の値。無ければ false。
func (ns Namespace) Int(dest string) (int, bool) {
	n, ok := ns[dest].(int)
	return n, ok
}

// Strings は文字列の並び。並びでなければ nil。
func (ns Namespace) Strings(dest string) []string {
	list, ok := ns[dest].([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(list))
	for _, v := range list {
		s, _ := v.(string)
		out = append(out, s)
	}
	return out
}

// Changed は値が既定値と違うか。
func (ns Namespace) Changed(a *action) bool {
	v, ok := ns[a.Dest]
	return ok && !sameValue(v, a.Default)
}

// sameValue は値の等しさ。bool は int と比べられる（false は 0 と同じ）。
func sameValue(a, b any) bool {
	if x, ok := asInt(a); ok {
		y, ok := asInt(b)
		return ok && x == y
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !sameValue(x[i], y[i]) {
				return false
			}
		}
		return true
	}
	return false
}

func asInt(v any) (int, bool) {
	switch x := v.(type) {
	case bool:
		if x {
			return 1, true
		}
		return 0, true
	case int:
		return x, true
	}
	return 0, false
}
