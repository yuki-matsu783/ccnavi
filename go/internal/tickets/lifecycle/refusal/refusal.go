package refusal

import (
	"regexp"
	"strings"

	"ccnavi/internal/infra/ospath"
)

// overwritten は、git が「書きかけを上書きする」と断ったときに並べるパスの行。
var overwritten = regexp.MustCompile(`would be overwritten`)

// NoRemoteRef は、fetch が「origin にその ref が無い」と断ったか（git の出力。LC_ALL=C で打ったもの）。
// 届かない・認証が通らないなどの失敗とは分ける。
func NoRemoteRef(out string) bool {
	return strings.Contains(out, "couldn't find remote ref")
}

// Of は、git が断ったときの出力（標準エラー。標準出力を足してもよい）から、理由の 1 文を作る。
// 書きかけとの重なり（「would be overwritten」の後に git が字下げして並べるパス）、索引のロック、コミットするユーザの
// 名前が無い、署名の失敗、hook の順に見て、どれでもなければ git の 1 行目を添える。理由どおりに言い、重なっていないのに
// 「重なる」とは言わない。呼ぶ側が前置き（何ができなかったか）を付ける。
func Of(out string) string {
	lines := ospath.Splitlines(out)
	first := ""
	if len(lines) > 0 {
		first = lines[0]
	}
	paths := overlapped(lines)
	low := strings.ToLower(out)
	switch {
	case len(paths) > 0:
		return "書きかけの " + strings.Join(paths, " ") + " と重なる。コミットか退避をしてから再実行してください"
	case strings.Contains(out, "index.lock"):
		return "索引のロック（index.lock）が残っている。別の git が動いていないか確かめ、異常終了して残ったものならユーザが削除します。"
	case strings.Contains(low, "tell me who you are") || strings.Contains(low, "empty ident") ||
		strings.Contains(low, "user.email") || strings.Contains(low, "user.name"):
		return "コミットするユーザの名前（user.name・user.email）が決まっていない"
	case strings.Contains(low, "gpg") || strings.Contains(low, "signing"):
		return "コミットの署名に失敗した"
	case strings.Contains(low, "hook"):
		return "git の hook が止めた（" + first + "）"
	}
	return "git が拒んだ（" + first + "）"
}

// Overlap は、git が書きかけとの重なりで断ったか（Of が「書きかけの … と重なる」と言う場合）。
func Overlap(out string) bool {
	return len(overlapped(ospath.Splitlines(out))) > 0
}

// overlapped は「would be overwritten」の後に git が字下げして並べるパス。
func overlapped(lines []string) []string {
	var paths []string
	in := false
	for _, line := range lines {
		switch {
		case overwritten.MatchString(line):
			in = true
			continue
		case line != "" && line[0] != ' ' && line[0] != '\t':
			in = false
		case in && strings.TrimSpace(line) != "":
			paths = append(paths, strings.TrimLeft(line, " \t"))
		}
	}
	return paths
}
