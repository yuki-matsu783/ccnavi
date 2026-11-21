package pyre

import (
	"strings"
	"testing"
)

// 照合の速さを測る。式は、ルールと組み込みの保護に近い形にし、長いパイプライン（区切りは shell.Sep と同じ \x00）に当てる。
var benchCases = []struct{ name, pattern, subject string }{
	{"launcher", `(?i)(^|\x00|[;&|]\s*)(&\s*)?((?:\S*[\\/])?(?:uv|poetry|pipenv)(?:\.exe)?\s+run\b(?:\s+[^\s\x00;&|]+)*?\s+)?((sh|bash)(\s+-\S+)*\s+)?(\S*[\\/])?(ccnavi(\.exe)?|ccnavi-launcher\.sh)\s+[^\x00]*\-\-yes\b`,
		strings.Repeat("ls\x00", 999) + "ls"},
	{"redirect", `((?:^|[^>|&])[|&]*>[>|&]* ?(?:[^ \x00\x01>|&](?:[^ \x00]*[^ \x00\x01])?)?|(^|\x00)(mv|rm|tee|dd|truncate|patch|shred)\b[^\x00]*)`,
		strings.Repeat("ls -la src/dir\x00", 300)},
	{"git", `(?:^|[\x00\n;&|({` + "`" + `!])\s*+(?:(?:then|do|else|elif|if|while|until|time)\s++)*+(?:(?:[^\s\x00\\/;&|({` + "`" + `!]*+[\\/])*+)git(?:\.exe)?\s+push\b`,
		strings.Repeat("echo hello world\x00", 300)},
	{"word", `(?<![\w-])(?:\-\-agree|\-\-reviewed)(?![\w-])`, strings.Repeat("git status --short ", 300)},
	{"glob", "^" + TranslateGlob("*git push*"), strings.Repeat("git status --short ", 300)},
}

func BenchmarkSearch(b *testing.B) {
	for _, c := range benchCases {
		re, err := Compile(c.pattern, 0)
		if err != nil {
			b.Fatal(err)
		}
		b.Run(c.name, func(b *testing.B) {
			for b.Loop() {
				re.Search(c.subject)
			}
		})
	}
}
