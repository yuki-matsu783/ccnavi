package suggest

import (
	"regexp"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/policy/rules"
)

var (
	// envAssign は先頭の環境変数の代入（語は空白で切ってあるので、= の後ろは何でもよい）。
	envAssign  = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*=`)
	command    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+-]*$`)
	subcommand = regexp.MustCompile(`^[a-z][a-z0-9_-]*$`)
	globMeta   = regexp.MustCompile(`[*?\[\]\\]`)
	separator  = regexp.MustCompile(`[\\/]`)
	notSlug    = regexp.MustCompile(`[^a-z0-9]+`)
)

// slugLimit は候補の id の元にする部分の長さの上限（字）。
const slugLimit = 40

// slug は候補の id に使える形（小文字の英数字をハイフンでつないだもの）にする。空になったら "x"。
func slug(text string) string {
	s := strings.Trim(notSlug.ReplaceAllString(pyfmt.Lower(text), "-"), "-")
	if len(s) > slugLimit {
		s = s[:slugLimit]
	}
	if s == "" {
		return "x"
	}
	return s
}

// shaped は渡った呼び出しの形。spelled は数えるキー、form はそれに当てるルールの本体（glob か regex の 1 つ）、
// name は候補の id の元。
type shaped struct {
	spelled string
	form    *pyjson.Object
	name    string
}

// shapeOf は渡った呼び出しの形。形にできなければ偽。Bash は先頭のコマンドの名前と、続く語がサブコマンドらしければ
// それ（`npm install`）、パスのツールは置かれたディレクトリの下（`{root}/docs/*`）、WebFetch は同じ scheme とホストの下。
func shapeOf(tool, subject, root string) (shaped, bool) {
	switch {
	case tool == config.ToolBash:
		return bashShape(subject)
	case slices.Contains(config.PathTools(), tool):
		return pathShape(tool, subject, root)
	case tool == config.ToolWebFetch:
		scheme, netloc, ok := splitURL(subject)
		if !ok || scheme == "" || netloc == "" || globMeta.MatchString(netloc) {
			return shaped{}, false
		}
		spelled := scheme + "://" + netloc + "/*"
		return shaped{spelled, pyjson.NewObject(string(rules.WhenGlob), spelled), "fetch-" + netloc}, true
	}
	return shaped{}, false
}

// bashShape は Bash の呼び出しを、先頭のコマンド名（と、続く語がサブコマンドらしければそれ）の形にする。
// 先頭の環境変数の代入は飛ばす。パス付きで呼ばれても同じ形になるよう、コマンド名はパスの最後の部分だけを使う。
func bashShape(subject string) (shaped, bool) {
	// 複数行なら 1 行目だけを見る。
	first, _, _ := strings.Cut(ospath.Strip(subject), "\n")
	words := ospath.Fields(first)
	for len(words) > 0 && envAssign.MatchString(words[0]) {
		words = words[1:]
	}
	if len(words) == 0 {
		return shaped{}, false
	}
	parts := separator.Split(words[0], -1)
	head := parts[len(parts)-1]
	if !command.MatchString(head) {
		return shaped{}, false
	}
	sub := ""
	if len(words) > 1 && subcommand.MatchString(words[1]) {
		sub = words[1]
	}
	// 当てる式は、コマンド名の前が行頭か区切り（空白・`;`・`&`・`|`・`(`）で、後ろが空白か行末のときだけ当たる形。
	// 別のコマンドの名前の一部（`xnpm` など）には当たらないようにする。
	spelled := strings.TrimSpace(head + " " + sub)
	pattern := `(^|[\s;&|(])(\S*[\\/])?` + pyre.Escape(head)
	if sub != "" {
		pattern += `\s+` + pyre.Escape(sub)
	}
	pattern += `(\s|$)`
	return shaped{spelled, pyjson.NewObject(string(rules.WhenRegex), pattern), spelled}, true
}

// pathShape はパスのツールの呼び出しを、置かれたディレクトリの下全部を指す glob の形にする。
// ワークスペースの下のパスは、機械ごとに違う絶対パスを避けるため、ルートの合言葉に置き換える。
// 次のパスは形にしない。glob の記号を含むもの（記号がパターンとして読まれてしまう）と、`/` 直下のもの（広すぎる）。
func pathShape(tool, subject, root string) (shaped, bool) {
	where := ospath.Dirname(strings.ReplaceAll(subject, `\`, "/"))
	top := rules.RootGlob(root)
	if where == "" || globMeta.MatchString(where) {
		return shaped{}, false
	}
	switch {
	case where == top || strings.HasPrefix(where, top+"/"):
		where = rules.RootPlaceholder + where[len(top):]
	case where == "/":
		return shaped{}, false
	}
	// 候補の id の元は、ディレクトリ名の最後の 2 段。
	spelled := where + "/*"
	parts := strings.Split(strings.Trim(strings.ReplaceAll(where, rules.RootPlaceholder, ""), "/"), "/")
	name := strings.Join(parts[max(0, len(parts)-2):], "-")
	if name == "" {
		name = "root"
	}
	return shaped{spelled, pyjson.NewObject(string(rules.WhenGlob), spelled), tool + "-" + name}, true
}

// schemeChars は URL の scheme に書ける字。
const schemeChars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-."

// splitURL は URL から scheme と netloc を取り出す。角括弧の片方しかない netloc は URL の形として読めないので、
// 取り出さず false を返す。
func splitURL(url string) (scheme, netloc string, ok bool) {
	url = strings.TrimLeftFunc(url, func(r rune) bool { return r <= ' ' })
	for _, unsafe := range []string{"\t", "\r", "\n"} {
		url = strings.ReplaceAll(url, unsafe, "")
	}
	// 先頭の空白と、途中のタブ・改行は取り除いてから読む（URL として解釈されるときと同じ扱いにするため）。
	// `:` より前が、英字で始まり scheme に使える字だけなら scheme とみなす。
	if i := strings.Index(url, ":"); i > 0 && isASCIILetter(url[0]) &&
		strings.IndexFunc(url[:i], func(r rune) bool { return !strings.ContainsRune(schemeChars, r) }) < 0 {
		scheme, url = strings.ToLower(url[:i]), url[i+1:]
	}
	if strings.HasPrefix(url, "//") {
		rest := url[2:]
		if end := strings.IndexAny(rest, "/?#"); end >= 0 {
			rest = rest[:end]
		}
		netloc = rest
		if strings.Contains(netloc, "[") != strings.Contains(netloc, "]") {
			return "", "", false
		}
	}
	return scheme, netloc, true
}

// isASCIILetter は ASCII の英字か。
func isASCIILetter(b byte) bool { return 'a' <= b && b <= 'z' || 'A' <= b && b <= 'Z' }
