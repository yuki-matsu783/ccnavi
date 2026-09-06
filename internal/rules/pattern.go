package rules

import (
	"regexp"
	"strings"
	"unicode"
)

// translate turns the friendly pattern syntax into a regular expression.
//
// The syntax is deliberately small, because a rule file is read and reviewed by
// people and a mistyped regular expression is a hole nobody sees. A star stands
// for any run of characters including none, a question mark for exactly one
// character, and a run of spaces matches any run of whitespace, so "git push"
// also catches "git   push".
//
// Everything else stands for itself. The result is searched for anywhere in the
// subject, so "git push" catches "/usr/bin/git push origin main".
//
// Word boundaries are added wherever a run of word characters meets a flexible
// part of the pattern. That is what keeps "sed *" off "sedate" while still
// letting it catch a bare "sed".
func translate(pattern string) string {
	runes := []rune(pattern)

	var b strings.Builder
	inWord := false // the previous rune was a literal word character

	flush := func() {
		if inWord {
			b.WriteString(`\b`)
			inWord = false
		}
	}

	for i := 0; i < len(runes); i++ {
		switch c := runes[i]; {
		case c == '*':
			flush()
			b.WriteString(`.*`)

		case c == '?':
			flush()
			b.WriteString(`.`)

		case unicode.IsSpace(c):
			flush()
			j := i
			for j < len(runes) && unicode.IsSpace(runes[j]) {
				j++
			}
			// Whitespace that runs into a wildcard is optional, so "git push *"
			// covers a bare "git push" too.
			if j < len(runes) && runes[j] == '*' {
				b.WriteString(`\s*`)
			} else {
				b.WriteString(`\s+`)
			}
			i = j - 1

		case c == '/':
			// A rule is written once and has to hold on every machine, so a
			// path separator matches either spelling of it.
			flush()
			b.WriteString(`[\\/]`)

		default:
			if isWordRune(c) && !inWord {
				b.WriteString(`\b`)
			}
			b.WriteString(regexp.QuoteMeta(string(c)))
			inWord = isWordRune(c)
		}
	}
	flush()

	return b.String()
}

func isWordRune(c rune) bool {
	return c == '_' || unicode.IsLetter(c) || unicode.IsDigit(c)
}
