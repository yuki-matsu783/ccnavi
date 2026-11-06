package diaglog

import "strings"

// MaskUserinfo は URL と scp 形に埋まった資格情報を Stars にする（sh の ccnavi_log_mask・拡張と同じ読み）。
//
// 空白・タブ・LF・CR で切った語ごとに見る。`://` を含む語は、`://` の後ろから次の `/` までを authority とし、
// `@` があれば最後の `@` より前を伏せる。含まない語は、最初の `/` より前に `@` があり、最後の `@` より前に
// `:` があれば、そこを伏せる（`user:tok@host:path`）。
func MaskUserinfo(value string) string {
	if !strings.Contains(value, "@") {
		return value
	}
	var b strings.Builder
	for value != "" {
		i := strings.IndexAny(value, gaps)
		if i < 0 {
			b.WriteString(maskWord(value))
			break
		}
		b.WriteString(maskWord(value[:i]))
		j := i
		for j < len(value) && strings.IndexByte(gaps, value[j]) >= 0 {
			j++
		}
		b.WriteString(value[i:j])
		value = value[j:]
	}
	return b.String()
}

// gaps は語を切る字（sh の ccnavi_log_space と同じ）。
const gaps = " \t\n\r"

// stars は伏せた部分の字（redact と同じ）。
const stars = "***"

func maskWord(word string) string {
	if !strings.Contains(word, "@") {
		return word
	}
	if !strings.Contains(word, "://") {
		head, _, _ := strings.Cut(word, "/")
		at := strings.LastIndex(head, "@")
		if at < 0 || !strings.Contains(head[:at], ":") {
			return word
		}
		return stars + "@" + word[at+1:]
	}
	var b strings.Builder
	rest := word
	for {
		before, after, ok := strings.Cut(rest, "://")
		if !ok {
			break
		}
		b.WriteString(before + "://")
		rest = after
		authority, _, _ := strings.Cut(rest, "/")
		if at := strings.LastIndex(authority, "@"); at >= 0 {
			b.WriteString(stars + "@" + authority[at+1:])
			rest = rest[len(authority):]
		}
	}
	b.WriteString(rest)
	return b.String()
}
