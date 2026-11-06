package useronly

import (
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/policy/texts"
)

// PruneForm が見るのは、記録と state を消す `ccnavi --prune`（`--preview` の無い形）。チケット制御とターミナル
// 要求を切る設定に依らず、組み込みの deny で止める。実行ファイルのターミナル要求は、擬似ターミナル
// （`script -qc '…' /dev/null`）でも、ターミナル要求を切る変数を前に並べても抜けられ、しきい値の環境変数を
// 0 に近づければ自分の呼び出しの記録を消せるため。
//
// 見るのは HumanPathForm と同じく、引用符と `\` を除いた生の文字列（実行役の越しでも同じ文字列が残る）。
// コマンドの切れ目の中に ccnavi の名前（語の終わりで閉じた形）と単独の語の `--prune` が並べば止める
// （`git fetch --prune` は名前を持たないので当たらない）。免除は `--preview` が `--prune` の隣に、引用を
// またがずに単独の語として並んだ形だけ。どこかの `--preview` で免除すると、`bash -c "ccnavi --prune" x
// --preview` のように実行役の引数で免除が成立する。

// Code と組み込みのルールの id。
const (
	CodeTicketApproval    = "DENY_TICKET_APPROVAL_CLI"
	TicketApprovalRuleID  = "builtin:guard-ticket-approval"
	CodeRecordsPrune      = "DENY_RECORDS_PRUNE"
	RecordsPruneRuleID    = "builtin:guard-records-prune"
	textRecordsPrune      = CodeRecordsPrune
	textTicketApproval    = "RULE_GUARD_TICKET_APPROVAL"
	pruneVerb             = "prune"
	pruneSegmentSeparator = ";&|\n"
)

// ccnavi の名前（語の終わりで閉じた形）。実行ファイルのパスの名前は呼び出しごとに足す。
const pruneNames = `ccnavi(?:\.exe)?|ccnavi-launcher\.sh`

var (
	pruneName    = compile(`(?:`+pruneNames+`)(?=\s)`, pyre.IgnoreCase)
	pruneFlag    = names("", groupPrune)[0]
	userPruneRx  = compile(`\A(?:`+userVerbForms(pruneVerb)+`)`, pyre.IgnoreCase)
	previewAfter = compile(`\A[ \t]+--preview(?![^\s;&|])`, 0)
)

// unquotedMarks は引用符とバックスラッシュを除いた文字列（符号位置の並び）と、除いた場所（除いた後の位置）。
func unquotedMarks(subject string) ([]rune, map[int]bool) {
	var out []rune
	cuts := map[int]bool{}
	for _, ch := range flat(subject) {
		if strings.ContainsRune(`'"`+"`"+`\`, ch) {
			cuts[len(out)] = true
			continue
		}
		out = append(out, ch)
	}
	return out, cuts
}

func namesFor(binPath string) rx {
	base := ospath.Basename(strings.ReplaceAll(binPath, `\`, "/"))
	if base == "" {
		return pruneName
	}
	return compile(`(?:`+pruneNames+`|`+pyre.Escape(base)+`)(?=\s)`, pyre.IgnoreCase)
}

// segments は Python の `[^;&|\n]+` の finditer（符号位置の始めと終わり）。
func segments(text []rune) [][2]int {
	var out [][2]int
	start := -1
	for i, r := range text {
		if strings.ContainsRune(pruneSegmentSeparator, r) {
			if start >= 0 {
				out = append(out, [2]int{start, i})
				start = -1
			}
		} else if start < 0 {
			start = i
		}
	}
	if start >= 0 {
		out = append(out, [2]int{start, len(text)})
	}
	return out
}

func isSpaceAt(text []rune, i int) bool { return i >= 0 && i < len(text) && ospath.IsSpace(text[i]) }

// PruneForm は記録を消す `ccnavi --prune`（`--preview` の無い形）があれば、見つけた文字列。無ければ空。
func PruneForm(subject, binPath string) string {
	text, cuts := unquotedMarks(subject)
	if !strings.Contains(string(text), pruneFlag) || onlyReaders(string(text)) {
		return ""
	}
	names := namesFor(binPath)
	flagRunes := []rune(pruneFlag)
	for _, seg := range segments(text) {
		found, ok := names.first(string(text[seg[0]:seg[1]]))
		if !ok {
			continue
		}
		// `(?<!\S)--prune(?!\S)` を名前の後ろから切れ目まで探す（後読みは切れ目の前も見る）。
		for k := seg[0] + found.End; k+len(flagRunes) <= seg[1]; k++ {
			end := k + len(flagRunes)
			if string(text[k:end]) != pruneFlag || k > 0 && !isSpaceAt(text, k-1) || end < seg[1] && !isSpaceAt(text, end) {
				continue
			}
			if !previewed(text, cuts, k, end) {
				return pruneFlag
			}
		}
	}
	return ""
}

// UserPruneForm は `ccnavi user prune` があれば、見つけた文字列。無ければ空。入口の位置の規則は他の `user` の
// 操作と同じで、止める設定だけが違う。
func UserPruneForm(subject, binPath string) string {
	text, _ := unquotedMarks(subject)
	if !strings.Contains(folder.String(string(text)), pruneVerb) || onlyReaders(string(text)) {
		return ""
	}
	names := namesFor(binPath)
	for _, seg := range segments(text) {
		segment := text[seg[0]:seg[1]]
		found, ok := names.first(string(segment))
		if !ok {
			continue
		}
		if verb, ok := userPruneRx.text(string(segment[found.End:])); ok {
			return ospath.Strip(verb)
		}
	}
	return ""
}

// previewed は `--prune`（start から end）の隣に、単独の語の `--preview` が引用をまたがずに並ぶか。
func previewed(text []rune, cuts map[int]bool, start, end int) bool {
	if after, ok := previewAfter.first(string(text[end:])); ok {
		crossed := false
		for i := end; i < end+after.End; i++ {
			crossed = crossed || cuts[i]
		}
		if !crossed {
			return true
		}
	}
	// `(?<!\S)--preview[ \t]+$` を text[:start] に当てる。
	k := start
	for k > 0 && (text[k-1] == ' ' || text[k-1] == '\t') {
		k--
	}
	word := []rune(optPreview)
	begin := k - len(word)
	if k == start || begin < 0 || string(text[begin:k]) != optPreview || begin > 0 && !isSpaceAt(text, begin-1) {
		return false
	}
	for i := begin + len(word); i <= start; i++ {
		if cuts[i] {
			return false
		}
	}
	return true
}

// PruneMessage は `--prune` で止めた文。
func PruneMessage(root string) string { return texts.Say(root, textRecordsPrune, texts.Values{}, nil) }
