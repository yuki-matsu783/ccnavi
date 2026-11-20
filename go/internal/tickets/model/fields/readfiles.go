package fields

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
)

// ReadFilesSection は、finish が本文の末尾に書く「参照したファイル」の節を、ScriptShape の drop に入れるときの名前。
// 欄と同じく、コミット済みの版にまだ節が無いときだけ除いてよい（ScriptFieldsSet が、節があればこの名前を返す）。
const ReadFilesSection = "read_files"

const (
	// readFilesMarker は節の始まりの印。印から末尾までが節で、節は本文の最後に置く。
	readFilesMarker = "<!-- ccnavi:read-files -->"
	// ReadFilesHeading は節の見出し。その下にセッションごとの小見出しと項目の行が並ぶ。
	ReadFilesHeading = "## 参照したファイル"
	sessionHead      = "### セッション `"
	sessionTail      = "`"
	readFilesItem    = "- "
)

// readFilesPart は節の中の 1 セッションぶん。
type readFilesPart struct {
	session string
	items   []string
}

// readFilesOf は本文の最後の節の印の行の番号と、セッションごとの中身。節は、印のあとが見出し 1 つ・小見出し・項目の行・
// 空行だけのもの。ほかの行があれば節と認めず -1（照合で除く範囲を狭く保つ）。
func readFilesOf(lines []string, end int) (int, []readFilesPart) {
	at := -1
	for i := len(lines) - 1; i > end; i-- {
		if lines[i] == readFilesMarker {
			at = i
			break
		}
	}
	if at < 0 {
		return -1, nil
	}
	headed := false
	var parts []readFilesPart
	for _, line := range lines[at+1:] {
		switch {
		case ospath.Strip(line) == "":
		case line == ReadFilesHeading && !headed:
			headed = true
		case headed && strings.HasPrefix(line, sessionHead) && strings.HasSuffix(line, sessionTail) && len(line) > len(sessionHead+sessionTail):
			parts = append(parts, readFilesPart{session: strings.TrimSuffix(strings.TrimPrefix(line, sessionHead), sessionTail)})
		case len(parts) > 0 && strings.HasPrefix(line, readFilesItem):
			last := &parts[len(parts)-1]
			last.items = append(last.items, strings.TrimPrefix(line, readFilesItem))
		default:
			return -1, nil
		}
	}
	if !headed {
		return -1, nil
	}
	return at, parts
}

// withoutReadFiles は節を除き、末尾の空行を落とした行。節が無くても末尾の空行は落とす（除いた版と除く前の版を揃える）。
func withoutReadFiles(lines []string, end int) []string {
	if at, _ := readFilesOf(lines, end); at >= 0 {
		lines = lines[:at]
	}
	for len(lines) > end+1 && ospath.Strip(lines[len(lines)-1]) == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

// HasReadFiles はチケットの本文の最後に節があるか。
func HasReadFiles(text string) bool {
	lines, end, ok := split(text)
	if !ok {
		return false
	}
	at, _ := readFilesOf(lines, end)
	return at >= 0
}

// SetReadFiles は本文の最後の節に、session の小見出しと項目を書く。ほかのセッションのぶんは残し、同じセッションのぶんは
// 置き換え、新しいセッションは後ろに足す。items は項目の行の中身（頭の `- ` は付けない）。items が空、または
// frontmatter が無ければ text のまま。
func SetReadFiles(text, session string, items []string) string {
	lines, end, ok := split(text)
	if !ok || len(items) == 0 || session == "" {
		return text
	}
	_, parts := readFilesOf(lines, end)
	part := readFilesPart{session: session, items: items}
	if i := slices.IndexFunc(parts, func(p readFilesPart) bool { return p.session == session }); i >= 0 {
		parts[i] = part
	} else {
		parts = append(parts, part)
	}
	lines = append(slices.Clone(withoutReadFiles(lines, end)), "", readFilesMarker, ReadFilesHeading)
	for _, p := range parts {
		lines = append(lines, "", sessionHead+p.session+sessionTail, "")
		for _, item := range p.items {
			lines = append(lines, readFilesItem+strings.ReplaceAll(item, "\n", " "))
		}
	}
	return join(lines, text+"\n")
}
