package pyre

import "strings"

// separator は "/" と書いた glob を "\" にも当てる式（ルールは 1 回書いて、どの機械でも同じ意味にする）。
const separator = `[\\/]`

// TranslateGlob は glob を Python 3.13 の fnmatch.translate と同じ式にし、"/" を両方の区切りに当てる
// （Python の globmatch.translate）。文字列全体に当たる形（末尾は `\Z`）で、先頭の `^` は呼び手が付ける。
// 式の字面も Python と同じにする。判定の説明に式をそのまま出すためである。
//
// 置き換えるのは翻訳後の "/" で、文字クラスの中の "/"（`[a/b]`）も置き換わる。Python と同じ答えにするため
// そのままにしてある。
func TranslateGlob(glob string) string {
	return strings.ReplaceAll(fnmatchTranslate([]rune(glob)), "/", separator)
}

// star は翻訳の途中で `*` の位置を表す印。
const star = "\x00*"

func fnmatchTranslate(pat []rune) string {
	var res []string
	n := len(pat)
	for i := 0; i < n; {
		c := pat[i]
		i++
		switch c {
		case '*':
			if len(res) == 0 || res[len(res)-1] != star {
				res = append(res, star)
			}
		case '?':
			res = append(res, ".")
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
				res = append(res, `\[`)
				break
			}
			res = append(res, classOf(pat, i, j))
			i = j + 1
		default:
			res = append(res, Escape(string(c)))
		}
	}
	return joinParts(res)
}

func classOf(pat []rune, i, j int) string {
	var stuff string
	if !containsRune(pat[i:j], '-') {
		stuff = strings.ReplaceAll(string(pat[i:j]), `\`, `\\`)
	} else {
		var chunks []string
		k := i + 1
		if pat[i] == '!' {
			k = i + 2
		}
		for {
			k = indexRune(pat, '-', k, j)
			if k < 0 {
				break
			}
			chunks = append(chunks, string(pat[i:k]))
			i = k + 1
			k += 3
		}
		if chunk := string(pat[i:j]); chunk != "" {
			chunks = append(chunks, chunk)
		} else {
			chunks[len(chunks)-1] += "-"
		}
		for k := len(chunks) - 1; k > 0; k-- {
			prev, cur := []rune(chunks[k-1]), []rune(chunks[k])
			if prev[len(prev)-1] > cur[0] {
				chunks[k-1] = string(prev[:len(prev)-1]) + string(cur[1:])
				chunks = append(chunks[:k], chunks[k+1:]...)
			}
		}
		for idx, chunk := range chunks {
			chunks[idx] = strings.ReplaceAll(strings.ReplaceAll(chunk, `\`, `\\`), "-", `\-`)
		}
		stuff = strings.Join(chunks, "-")
	}
	var b strings.Builder
	for _, r := range stuff {
		if r == '&' || r == '~' || r == '|' {
			b.WriteByte('\\')
		}
		b.WriteRune(r)
	}
	stuff = b.String()
	switch {
	case stuff == "":
		return "(?!)"
	case stuff == "!":
		return "."
	case stuff[0] == '!':
		stuff = "^" + stuff[1:]
	case stuff[0] == '^' || stuff[0] == '[':
		stuff = `\` + stuff
	}
	return "[" + stuff + "]"
}

// joinParts は Python の fnmatch._join_translated_parts。間の `*` は戻らない形（`(?>.*?固定)`）にする。
func joinParts(parts []string) string {
	var b strings.Builder
	i, n := 0, len(parts)
	for i < n && parts[i] != star {
		b.WriteString(parts[i])
		i++
	}
	for i < n {
		i++ // star
		if i == n {
			b.WriteString(".*")
			break
		}
		var fixed strings.Builder
		for i < n && parts[i] != star {
			fixed.WriteString(parts[i])
			i++
		}
		if i == n {
			b.WriteString(".*" + fixed.String())
		} else {
			b.WriteString("(?>.*?" + fixed.String() + ")")
		}
	}
	return `(?s:` + b.String() + `)\Z`
}

func containsRune(s []rune, r rune) bool { return indexRune(s, r, 0, len(s)) >= 0 }

func indexRune(s []rune, r rune, from, to int) int {
	for k := max(from, 0); k < to && k < len(s); k++ {
		if s[k] == r {
			return k
		}
	}
	return -1
}
