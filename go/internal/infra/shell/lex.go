package shell

import (
	"errors"
	"regexp"
	"strings"
	"unicode"
)

// 語の分割は Python の shlex.shlex(posix=True, punctuation_chars=True) に whitespace_split と
// commenters="" を付けたものと同じ答えを返す。判定のルールはこの割り方を前提に書かれているので、
// 1 文字でも割り方が変わると、止まっていた形が通る。

// errUnclosed は閉じない引用か、末尾の `\` で語に割れないこと。
var errUnclosed = errors.New("No closing quotation")

const punctuation = "();<>|&"

func isPunct(r rune) bool { return strings.ContainsRune(punctuation, r) }

func isLexSpace(r rune) bool { return r == ' ' || r == '\t' || r == '\r' || r == '\n' }

// tokenize は走査が組んだ文字列を語に割り、つながった区切りを割って、算術式を除く。
// コメントは走査が除いてあるので扱わない（shlex に任せると語の途中の `#` からも消える）。
func tokenize(src string) ([]string, error) {
	tokens, err := lex(src)
	if err != nil {
		return nil, err
	}
	return dropArithmetic(splitGlued(tokens)), nil
}

func lex(src string) ([]string, error) {
	const (
		stSpace = ' '
		stWord  = 'a'
		stPunct = 'c'
		stEOF   = 0
	)
	rs := []rune(src)
	pos := 0
	var pushback []rune
	next := func() (rune, bool) {
		if n := len(pushback); n > 0 {
			r := pushback[n-1]
			pushback = pushback[:n-1]
			return r, true
		}
		if pos < len(rs) {
			pos++
			return rs[pos-1], true
		}
		return 0, false
	}
	state := rune(stSpace)
	var out []string
	for {
		quoted := false
		escaped := rune(stWord)
		var tok []rune
	token:
		for {
			c, have := next()
			switch {
			case state == stEOF:
				tok = nil
				break token
			case state == stSpace:
				switch {
				case !have:
					state = stEOF
					break token
				case isLexSpace(c):
					if len(tok) > 0 || quoted {
						break token
					}
				case c == '\\':
					escaped = stWord
					state = '\\'
				case isPunct(c):
					tok = []rune{c}
					state = stPunct
				case c == '\'' || c == '"':
					state = c
				default:
					tok = []rune{c}
					state = stWord
				}
			case state == '\'' || state == '"':
				quoted = true
				switch {
				case !have:
					return nil, errUnclosed
				case c == state:
					state = stWord
				case c == '\\' && state == '"':
					escaped = state
					state = '\\'
				default:
					tok = append(tok, c)
				}
			case state == '\\':
				if !have {
					return nil, errUnclosed
				}
				// 二重引用の中で外れるのは `\"` と `\\` だけ。ほかは `\` を残す。
				if (escaped == '\'' || escaped == '"') && c != '\\' && c != escaped {
					tok = append(tok, '\\')
				}
				tok = append(tok, c)
				state = escaped
			default: // stWord, stPunct
				switch {
				case !have:
					state = stEOF
					break token
				case isLexSpace(c):
					state = stSpace
					if len(tok) > 0 || quoted {
						break token
					}
				case state == stPunct:
					if isPunct(c) {
						tok = append(tok, c)
					} else {
						pushback = append(pushback, c)
						state = stSpace
						break token
					}
				case c == '\'' || c == '"':
					state = c
				case c == '\\':
					escaped = stWord
					state = '\\'
				case !isPunct(c):
					tok = append(tok, c)
				default:
					pushback = append(pushback, c)
					state = stSpace
					if len(tok) > 0 || quoted {
						break token
					}
				}
			}
		}
		if len(tok) == 0 && !quoted {
			return out, nil
		}
		out = append(out, string(tok))
	}
}

// quote は shlex.quote と同じ。置換を全文で書き戻すときに 1 語にする。
func quote(s string) string {
	if s == "" {
		return "''"
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		safe := c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' ||
			strings.IndexByte("_@%+=:,./-", c) >= 0
		if !safe {
			return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
		}
	}
	return s
}

// ---- Python の文字の分類。Unicode の文字でも Python と同じ答えにする。

// pyDigits は Python の str.isdigit が真になる文字（Nd に上付き・丸数字などを足したもの）。
var pyDigits = &unicode.RangeTable{
	R16: []unicode.Range16{
		{0x30, 0x39, 1}, {0xB2, 0xB3, 1}, {0xB9, 0xB9, 1}, {0x660, 0x669, 1}, {0x6F0, 0x6F9, 1},
		{0x7C0, 0x7C9, 1}, {0x966, 0x96F, 1}, {0x9E6, 0x9EF, 1}, {0xA66, 0xA6F, 1}, {0xAE6, 0xAEF, 1},
		{0xB66, 0xB6F, 1}, {0xBE6, 0xBEF, 1}, {0xC66, 0xC6F, 1}, {0xCE6, 0xCEF, 1}, {0xD66, 0xD6F, 1},
		{0xDE6, 0xDEF, 1}, {0xE50, 0xE59, 1}, {0xED0, 0xED9, 1}, {0xF20, 0xF29, 1}, {0x1040, 0x1049, 1},
		{0x1090, 0x1099, 1}, {0x1369, 0x1371, 1}, {0x17E0, 0x17E9, 1}, {0x1810, 0x1819, 1},
		{0x1946, 0x194F, 1}, {0x19D0, 0x19DA, 1}, {0x1A80, 0x1A89, 1}, {0x1A90, 0x1A99, 1},
		{0x1B50, 0x1B59, 1}, {0x1BB0, 0x1BB9, 1}, {0x1C40, 0x1C49, 1}, {0x1C50, 0x1C59, 1},
		{0x2070, 0x2070, 1}, {0x2074, 0x2079, 1}, {0x2080, 0x2089, 1}, {0x2460, 0x2468, 1},
		{0x2474, 0x247C, 1}, {0x2488, 0x2490, 1}, {0x24EA, 0x24EA, 1}, {0x24F5, 0x24FD, 1},
		{0x24FF, 0x24FF, 1}, {0x2776, 0x277E, 1}, {0x2780, 0x2788, 1}, {0x278A, 0x2792, 1},
		{0xA620, 0xA629, 1}, {0xA8D0, 0xA8D9, 1}, {0xA900, 0xA909, 1}, {0xA9D0, 0xA9D9, 1},
		{0xA9F0, 0xA9F9, 1}, {0xAA50, 0xAA59, 1}, {0xABF0, 0xABF9, 1}, {0xFF10, 0xFF19, 1},
	},
	R32: []unicode.Range32{
		{0x104A0, 0x104A9, 1}, {0x10A40, 0x10A43, 1}, {0x10D30, 0x10D39, 1}, {0x10E60, 0x10E68, 1},
		{0x11052, 0x1105A, 1}, {0x11066, 0x1106F, 1}, {0x110F0, 0x110F9, 1}, {0x11136, 0x1113F, 1},
		{0x111D0, 0x111D9, 1}, {0x112F0, 0x112F9, 1}, {0x11450, 0x11459, 1}, {0x114D0, 0x114D9, 1},
		{0x11650, 0x11659, 1}, {0x116C0, 0x116C9, 1}, {0x11730, 0x11739, 1}, {0x118E0, 0x118E9, 1},
		{0x11950, 0x11959, 1}, {0x11C50, 0x11C59, 1}, {0x11D50, 0x11D59, 1}, {0x11DA0, 0x11DA9, 1},
		{0x11F50, 0x11F59, 1}, {0x16A60, 0x16A69, 1}, {0x16AC0, 0x16AC9, 1}, {0x16B50, 0x16B59, 1},
		{0x1D7CE, 0x1D7FF, 1}, {0x1E140, 0x1E149, 1}, {0x1E2F0, 0x1E2F9, 1}, {0x1E4F0, 0x1E4F9, 1},
		{0x1E950, 0x1E959, 1}, {0x1F100, 0x1F10A, 1}, {0x1FBF0, 0x1FBF9, 1},
	},
}

// isDigits は Python の s.isdigit()。fd の番号の見分けに使う。
func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !unicode.Is(pyDigits, r) {
			return false
		}
	}
	return true
}

// separators は区切りの演算子。長いものを先に置く（`;;&` を `;;` と `&` に割らない）。
var separators = regexp.MustCompile(`;;&|;&|;;|&&|\|\||\|&|;|&|\||\(|\)`)

// splitGlued は shlex が 1 語につなげた区切りの塊（`);` など）を演算子ごとに割る。
// 割らないと `( cd x && ls ); echo > f` の `echo > f` が `ls` の引数に混ざる。
// 算術の `((` `))` とリダイレクト（`<` `>` を含む塊）は割らない。
func splitGlued(tokens []string) []string {
	out := make([]string, 0, len(tokens))
	for _, token := range tokens {
		if len(token) > 1 && !operators[token] && token != "((" && token != "))" &&
			strings.Trim(token, ";&|()") == "" && strings.ContainsAny(token, ";&|") {
			out = append(out, separators.FindAllString(token, -1)...)
		} else {
			out = append(out, token)
		}
	}
	return out
}

// dropArithmetic は算術式 $(( )) を除く。左シフトの `<<` を残すと、走査がヒアドキュメントと
// 読まなかった `<<` として縮退する。中の置換は走査が切り出してある。
func dropArithmetic(tokens []string) []string {
	kept := make([]string, 0, len(tokens))
	for i := 0; i < len(tokens); {
		if tokens[i] == "$" && i+1 < len(tokens) && tokens[i+1] == "((" {
			depth := 0
			for i++; i < len(tokens); {
				depth += strings.Count(tokens[i], "(") - strings.Count(tokens[i], ")")
				i++
				if depth <= 0 {
					break
				}
			}
			continue
		}
		kept = append(kept, tokens[i])
		i++
	}
	return kept
}
