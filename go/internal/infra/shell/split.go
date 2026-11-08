package shell

import (
	"errors"
	"strings"
)

// errSplit は Split が読めない形（閉じない引用・末尾の `\`）。
var errSplit = errors.New("shlex.split が読めない")

// Split は Python の shlex.split(s)（posix・whitespace_split・コメント無し）と同じ答えで語に割る。
// コマンドを読む Read の割り方（`;` などの記号でも割る）とは違い、空白と引用だけで割る。
// 状態は space（語の外）・word（語の中）・squote / dquote（引用の中）・escape（\ の直後）で、lex と同じ規則。
func Split(s string) ([]string, error) {
	const (
		space   = ' '
		word    = 'a'
		escape  = '\\'
		squote  = '\''
		dquote  = '"'
		isSpace = " \t\r\n"
	)
	var out []string
	var tok strings.Builder
	state, escaped := rune(space), rune(word)
	quoted := false
	emit := func() {
		out = append(out, tok.String())
		tok.Reset()
		quoted = false
	}
	for _, c := range s {
		switch state {
		case space:
			switch {
			case strings.ContainsRune(isSpace, c):
			case c == escape:
				escaped, state = word, escape
			case c == squote || c == dquote:
				state = c
			default:
				tok.WriteRune(c)
				state = word
			}
		case squote, dquote:
			quoted = true
			switch {
			case c == state:
				state = word
			case c == escape && state == dquote:
				escaped, state = state, escape
			default:
				tok.WriteRune(c)
			}
		case escape:
			if (escaped == squote || escaped == dquote) && c != state && c != escaped {
				tok.WriteRune(state)
			}
			tok.WriteRune(c)
			state = escaped
		case word:
			switch {
			case strings.ContainsRune(isSpace, c):
				state = space
				if tok.Len() > 0 || quoted {
					emit()
				}
			case c == squote || c == dquote:
				state = c
			case c == escape:
				escaped, state = word, escape
			default:
				tok.WriteRune(c)
			}
		}
	}
	switch state {
	case squote, dquote, escape:
		return nil, errSplit
	case word:
		if tok.Len() > 0 || quoted {
			emit()
		}
	}
	return out, nil
}
