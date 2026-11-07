package learning

import (
	"errors"
	"slices"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/records/redact"
	"ccnavi/internal/records/wiki"
)

// 気づき 1 件の欄の選び方。
var (
	targets = []string{"rule", "risk", "phase", "template", "skill", "docs"}
	signals = []string{"訂正", "落とし穴", "失敗と回避", signalError}
)

const (
	undecided     = "未定"
	defaultSignal = "訂正"
	// noteLimit は本文の上限（符号位置）。超えた分は切って cutMark を付ける。
	noteLimit  = 2000
	cutMark    = "…(切り詰め)"
	titleLimit = 60
	// inboxTries は受信箱の名前を重ならないように選ぶ回数。
	inboxTries = 50
	digestExt  = ".digest.json"
)

// errNoFrontmatter は手で書いた記録が、先頭に learnings: を持つ frontmatter を持たないこと。雛形を推測して
// 付けず、止めて直させる。
var errNoFrontmatter = errors.New("先頭に learnings: を持つ frontmatter がありません")

// recordRel は子の気づきの記録の置き場。
func recordRel(ident string) string { return LearningDir + "/" + ident + config.MdExt }

func digestRel(ident string) string { return LearningDir + "/" + ident + digestExt }

func inboxRel(ident, stamp string, n int) string {
	suffix := ""
	if n > 0 {
		suffix = "-" + strconv.Itoa(n)
	}
	return wiki.InboxDir + "/" + ident + "-" + stamp + suffix + config.MdExt
}

func utc14() string { return time.Now().UTC().Format(fsio.IDStampLayout) }

func scaffoldText(ident, parent string) string {
	return fence + "\nticket: " + ident + "\nparent: " + parent + "\n" + learningsKey + ": " + noneCount + "\n" + fence + "\n\n"
}

// cleanBody は本文を 1 つの節の中に閉じ込める。CR や行の区切りの字で見出しを偽造させない。
func cleanBody(body string) string {
	text := strings.NewReplacer("\r\n", "\n", "\r", "\n", "\u2028", "\n", "\u2029", "\n", "\u0085", "\n").Replace(body)
	text = strings.Map(func(r rune) rune {
		if (r <= 0x08) || r == 0x0b || r == 0x0c || (r >= 0x0e && r <= 0x1f) || r == 0x7f {
			return -1
		}
		return r
	}, text)
	text = redact.Redact(ospath.Strip(text))
	if rs := []rune(text); len(rs) > noteLimit {
		return string(rs[:noteLimit]) + cutMark
	}
	return text
}

func titleOf(body string) string {
	for _, line := range ospath.Splitlines(body) {
		if stripped := ospath.Strip(line); stripped != "" {
			if rs := []rune(stripped); len(rs) > titleLimit {
				return string(rs[:titleLimit])
			}
			return stripped
		}
	}
	return ""
}

func validate(body, target, signal string) string {
	switch {
	case ospath.Strip(body) == "":
		return "本文が空"
	case target != "" && !slices.Contains(targets, target):
		return "--target は " + strings.Join(targets, " / ") + " のどれか"
	case signal != "" && !slices.Contains(signals, signal):
		return "--signal は " + strings.Join(signals, " / ") + " のどれか"
	}
	return ""
}

func entryText(body, target, signal, now string) string {
	cleaned := cleanBody(body)
	if target == "" {
		target = undecided
	}
	if signal == "" {
		signal = defaultSignal
	}
	lines := strings.Split(cleaned, "\n")
	for i := 1; i < len(lines); i++ {
		if lines[i] != "" {
			lines[i] = "  " + lines[i]
		}
	}
	return headingMk + titleOf(cleaned) + "\n" +
		"\n" +
		"- 種類: " + target + "\n" +
		"- 信号: " + signal + "\n" +
		"- 内容: " + strings.Join(lines, "\n") + "\n" +
		"- 記録: " + now + "\n"
}

func hasUsableFrontmatter(text string) bool {
	lines := strings.Split(text, "\n")
	end := frontmatterEnd(lines)
	if end < 0 {
		return false
	}
	for _, line := range lines[1:end] {
		if strings.HasPrefix(line, learningsKey+":") {
			return true
		}
	}
	return false
}

// countEntries は frontmatter の learnings: を、frontmatter の外の `## ` の行の数に合わせる。
func countEntries(text string) string {
	lines := strings.Split(text, "\n")
	end := frontmatterEnd(lines)
	if end < 0 {
		return text
	}
	n := 0
	for _, line := range lines[end+1:] {
		if strings.HasPrefix(line, headingMk) {
			n++
		}
	}
	for i := 1; i < end; i++ {
		if strings.HasPrefix(lines[i], learningsKey+":") {
			lines[i] = learningsKey + ": " + strconv.Itoa(n)
			break
		}
	}
	return strings.Join(lines, "\n")
}

// appendEntry は記録の末尾に節を足し、件数を数え直す。空の記録は雛形から始める。
func appendEntry(text, entry, ident, parent string) (string, error) {
	text = strings.ReplaceAll(strings.TrimLeft(text, yaml.BOM), "\r\n", "\n")
	if ospath.Strip(text) == "" {
		text = scaffoldText(ident, parent)
	} else if !hasUsableFrontmatter(text) {
		return "", errNoFrontmatter
	}
	if !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	if !strings.HasSuffix(text, "\n\n") {
		text += "\n"
	}
	return countEntries(text + entry), nil
}

func inboxText(ident, entry, now string) string {
	return fence + "\n" +
		"type: " + wiki.DocType + "\n" +
		"title: 受信箱 " + ident + " " + now + "\n" +
		"description: 対話セッションで依頼された気づき 1 件（統合の前）\n" +
		"tags: [inbox]\n" +
		"keywords: [受信箱, 気づき]\n" +
		"ticket: " + ident + "\n" +
		learningsKey + ": 1\n" +
		fence + "\n" +
		"\n" +
		entry
}
