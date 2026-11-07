package argparse

import (
	"os"
	"regexp"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/pyfmt"
)

const (
	usagePrefix     = "usage: "
	fallbackColumns = 80 // shutil.get_terminal_size の既定
	// HelpFormatter は端末の幅から 2 を引いた幅で折り返す。
	columnsMargin = 2
)

var longBreak = regexp.MustCompile(`\n\n\n+`)

// ErrorText は誤りのときに標準エラーへ書く文（usage と `<prog>: error: <message>`）。
// usage の折り返す幅は、COLUMNS か標準出力の端末から決める。
func (p *Parser) ErrorText(message string) string { return p.errorText(message, usageWidth()) }

func (p *Parser) errorText(message string, width int) string {
	return p.formatUsage(width) + p.prog + ": error: " + message + "\n"
}

func usageWidth() int { return columns(os.LookupEnv, stdoutColumns) - columnsMargin }

// columns は端末の桁数。COLUMNS が正の整数ならそれ、無ければ端末の幅、それも 0 なら 80。
func columns(lookup func(string) (string, bool), term func() (int, bool)) int {
	if s, ok := lookup(env.Columns); ok {
		if n, err := pyfmt.ParseInt(s); err == nil && n > 0 {
			return n
		}
	}
	if n, ok := term(); ok && n > 0 {
		return n
	}
	return fallbackColumns
}

func (p *Parser) formatUsage(width int) string {
	var optionals, positionals []string
	for _, a := range p.actions {
		if len(a.OptionStrings) > 0 {
			optionals = append(optionals, a.usagePart())
		} else {
			positionals = append(positionals, a.usagePart())
		}
	}
	usage := p.prog
	if parts := append(append([]string(nil), optionals...), positionals...); len(parts) > 0 {
		usage += " " + strings.Join(parts, " ")
	}
	if runes(usagePrefix)+runes(usage) > width {
		usage = strings.Join(p.wrapUsage(optionals, positionals, width), "\n")
	}
	text := longBreak.ReplaceAllString(usagePrefix+usage+"\n\n", "\n\n")
	return strings.Trim(text, "\n") + "\n"
}

// wrapUsage は HelpFormatter._format_usage の折り返し。prog が短ければ後ろに続け、長ければ 1 行に置く。
func (p *Parser) wrapUsage(optionals, positionals []string, width int) []string {
	prog := p.prog
	if float64(runes(usagePrefix)+runes(prog)) <= 0.75*float64(width) {
		indent := strings.Repeat(" ", runes(usagePrefix)+runes(prog)+1)
		switch {
		case len(optionals) > 0:
			lines := usageLines(append([]string{prog}, optionals...), indent, true, width)
			return append(lines, usageLines(positionals, indent, false, width)...)
		case len(positionals) > 0:
			return usageLines(append([]string{prog}, positionals...), indent, true, width)
		}
		return []string{prog}
	}
	indent := strings.Repeat(" ", runes(usagePrefix))
	lines := usageLines(append(append([]string(nil), optionals...), positionals...), indent, false, width)
	if len(lines) > 1 {
		lines = append(usageLines(optionals, indent, false, width), usageLines(positionals, indent, false, width)...)
	}
	return append([]string{prog}, lines...)
}

// usageLines は get_lines。prefixed なら 1 行目は "usage: " の後ろに続くので、字下げを外す。
func usageLines(parts []string, indent string, prefixed bool, width int) []string {
	var lines, line []string
	lineLen := runes(indent) - 1
	if prefixed {
		lineLen = runes(usagePrefix) - 1
	}
	for _, part := range parts {
		if lineLen+1+runes(part) > width && len(line) > 0 {
			lines = append(lines, indent+strings.Join(line, " "))
			line = nil
			lineLen = runes(indent) - 1
		}
		line = append(line, part)
		lineLen += runes(part) + 1
	}
	if len(line) > 0 {
		lines = append(lines, indent+strings.Join(line, " "))
	}
	if prefixed {
		lines[0] = lines[0][len(indent):]
	}
	return lines
}

// usagePart は usage に出す 1 つの引数。オプションは必須でなければ [] で囲む。
func (a *Action) usagePart() string {
	if len(a.OptionStrings) == 0 {
		return a.formatArgs(a.Dest)
	}
	part := a.OptionStrings[0]
	if a.nargs != noArgs {
		part += " " + a.formatArgs(pyfmt.Upper(a.Dest))
	}
	if !a.required {
		part = "[" + part + "]"
	}
	return part
}

// formatArgs は HelpFormatter._format_args。
func (a *Action) formatArgs(defaultMetavar string) string {
	metavar := func(n int) []string {
		if len(a.metavar) > 1 {
			return a.metavar
		}
		m := defaultMetavar
		if len(a.metavar) == 1 {
			m = a.metavar[0]
		}
		out := make([]string, n)
		for i := range out {
			out[i] = m
		}
		return out
	}
	switch a.nargs {
	case one:
		return metavar(1)[0]
	case Optional:
		return "[" + metavar(1)[0] + "]"
	case ZeroOrMore:
		m := metavar(1)
		if len(m) == 2 {
			return "[" + m[0] + " [" + m[1] + " ...]]"
		}
		return "[" + m[0] + " ...]"
	case noArgs:
		return ""
	}
	return strings.Join(metavar(int(a.nargs)), " ")
}

func runes(s string) int { return utf8.RuneCountInString(s) }
