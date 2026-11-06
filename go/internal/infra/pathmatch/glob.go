package pathmatch

import (
	"regexp"
	"strings"
)

// separator は "/" と書いた glob を "\" にも当てるための式。ルールは 1 回書いて、どの機械でも同じ意味にする。
const separator = `[\\/]`

// neverMatch は空の文字クラス（Python の `(?!)`）。RE2 には先読みが無いので、何にも当たらない文字クラスで書く。
const neverMatch = `[^\x00-\x{10FFFF}]`

// Translate は glob を正規表現にする。意味は Python の fnmatch.translate そのままで、足すのは区切りの正規化だけ。
// 文字列全体に当てる形（末尾は `\z`）で返し、先頭の `^` は呼び手が付ける。
//
// 前後の `*` も語の切れ目も自動では足さない。足すと、書いたものと当たるものの間に見えない変換が挟まる
// （`foo*bar` が `foobar` に当たらなくなる）。左の切れ目が要るルールは regex を使う。
//
// 置き換えるのは翻訳後の "/" で、文字クラスの中の "/"（`[a/b]`）も置き換わる。Python と同じ答えにするため
// そのままにしてある。
func Translate(glob string) string {
	return "(?s:" + strings.ReplaceAll(translateBody([]rune(glob)), "/", separator) + `)\z`
}

func translateBody(pat []rune) string {
	var out strings.Builder
	n := len(pat)
	lastStar := false
	for i := 0; i < n; {
		c := pat[i]
		i++
		star := false
		switch c {
		case '*':
			star = true
			if !lastStar {
				out.WriteString(".*")
			}
		case '?':
			out.WriteString(".")
		case '[':
			j := i
			if j < n && pat[j] == '!' {
				j++
			}
			if j < n && pat[j] == ']' {
				j++
			}
			for j < n && pat[j] != ']' {
				j++
			}
			if j >= n {
				out.WriteString(`\[`)
				break
			}
			out.WriteString(charClass(pat, i, j))
			i = j + 1
		default:
			out.WriteString(regexp.QuoteMeta(string(c)))
		}
		lastStar = star
	}
	return out.String()
}

// charClass は pat[i:j]（`[` と `]` の間）を文字クラスにする。範囲の扱いは fnmatch と同じで、
// 逆向きの範囲（`z-a`）は捨てる。
func charClass(pat []rune, i, j int) string {
	var stuff string
	if !containsRune(pat[i:j], '-') {
		stuff = escapeInClass(string(pat[i:j]), false)
	} else {
		var chunks [][]rune
		k := i + 1
		if pat[i] == '!' {
			k = i + 2
		}
		for {
			k = indexRune(pat, '-', k, j)
			if k < 0 {
				break
			}
			chunks = append(chunks, append([]rune(nil), pat[i:k]...))
			i = k + 1
			k += 3
		}
		if chunk := pat[i:j]; len(chunk) > 0 {
			chunks = append(chunks, append([]rune(nil), chunk...))
		} else {
			chunks[len(chunks)-1] = append(chunks[len(chunks)-1], '-')
		}
		for k := len(chunks) - 1; k > 0; k-- {
			prev, cur := chunks[k-1], chunks[k]
			if prev[len(prev)-1] > cur[0] {
				chunks[k-1] = append(append([]rune(nil), prev[:len(prev)-1]...), cur[1:]...)
				chunks = append(chunks[:k], chunks[k+1:]...)
			}
		}
		parts := make([]string, len(chunks))
		for idx, chunk := range chunks {
			parts[idx] = escapeInClass(string(chunk), true)
		}
		stuff = strings.Join(parts, "-")
	}
	switch {
	case stuff == "":
		return neverMatch
	case stuff == "!":
		return "."
	case stuff[0] == '!':
		stuff = "^" + stuff[1:]
	case stuff[0] == '^' || stuff[0] == '[':
		stuff = `\` + stuff
	}
	return "[" + stuff + "]"
}

// escapeInClass はクラスの中の "\"（と範囲でない "-"）を文字として読ませる。Python が逃がす `&~|` に加えて、
// RE2 が `[:` を POSIX のクラスと読むので "[" も逃がす。
func escapeInClass(s string, hyphen bool) string {
	var b strings.Builder
	for _, r := range s {
		switch {
		case r == '\\':
			b.WriteString(`\\`)
		case r == '-' && hyphen:
			b.WriteString(`\-`)
		case r == '&' || r == '~' || r == '|':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '[':
			b.WriteString(`\[`)
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

func containsRune(s []rune, r rune) bool { return indexRune(s, r, 0, len(s)) >= 0 }

func indexRune(s []rune, r rune, from, to int) int {
	for k := from; k < to && k < len(s); k++ {
		if k >= 0 && s[k] == r {
			return k
		}
	}
	return -1
}
