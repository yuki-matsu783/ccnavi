package useronly

import (
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
)

// PruneForm が見るのは、記録と state を消す `ccnavi --prune`（`--preview` の無い形）。チケット制御とターミナル
// 要求を切る設定に依らず、組み込みの deny で止める。実行ファイルのターミナル要求は、擬似ターミナル
// （`script -qc '…' /dev/null`）でも、ターミナル要求を切る変数を前に並べても抜けられ、しきい値の環境変数を
// 0 に近づければ自分の呼び出しの記録を消せるため。
//
// 見るのは HumanPathForm と同じく、引用符と `\` を除いた生の文字列（実行役の越しでも同じ文字列が残る）。
// コマンドの切れ目の中に ccnavi の名前（語の終わりで閉じた形）と単独の語の `--prune` が並べば止める
// （`git fetch --prune` は名前を含まないので当たらない）。免除は `--preview` が `--prune` の隣に、引用を
// またがずに単独の語として並んだ形だけ。どこかの `--preview` で免除すると、`bash -c "ccnavi --prune" x
// --preview` のように実行役の引数で免除が成立する。

// Code と組み込みのルールの id。
const (
	CodeTicketApproval   = "DENY_TICKET_APPROVAL_CLI"
	TicketApprovalRuleID = rules.ReservedIDPrefix + "guard-ticket-approval"
	CodeRecordsPrune     = "DENY_RECORDS_PRUNE"
	RecordsPruneRuleID   = rules.ReservedIDPrefix + "guard-records-prune"
	textRecordsPrune     = CodeRecordsPrune
	textTicketApproval   = "RULE_GUARD_TICKET_APPROVAL"
	pruneVerb            = "prune"
)

// ccnavi の名前（語の終わりで閉じた形）。実行ファイルのパスの名前は呼び出しごとに足す。
const pruneNames = `ccnavi(?:\.exe)?|ccnavi-launcher\.sh`

var (
	pruneName     = compile(`(?:`+pruneNames+`)(?=\s)`, pyre.IgnoreCase)
	pruneFlag     = names("", groupPrune)[0]
	userPruneRx   = compile(userVerbForms(pruneVerb), pyre.IgnoreCase)
	pruneWord     = compile(`(?<!\S)`+pyre.Escape(pruneFlag)+`(?!\S)`, 0)
	previewAfter  = compile(`[ \t]+`+optPreview+`(?![^\s;&|])`, 0)
	previewBefore = compile(`(?<!\S)`+optPreview+`[ \t]+$`, 0)
	pruneSegment  = compile(`[^;&|\n]+`, 0)
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

// PruneForm は記録を消す `ccnavi --prune`（`--preview` の無い形）があれば、見つけた文字列。無ければ空。
func PruneForm(subject, binPath string) string {
	runes, cuts := unquotedMarks(subject)
	text := string(runes)
	if !strings.Contains(text, pruneFlag) || onlyReaders(text) {
		return ""
	}
	names := namesFor(binPath)
	for _, seg := range pruneSegment.all(text) {
		found, ok := names.first(seg.Text())
		if !ok {
			continue
		}
		for _, word := range pruneWord.allIn(text, seg.Start+found.End, seg.End) {
			if !previewed(text, cuts, word.Start, word.End) {
				return pruneFlag
			}
		}
	}
	return ""
}

// UserPruneForm は `ccnavi user prune` があれば、見つけた文字列。無ければ空。入口の位置の規則は他の `user` の
// 操作と同じで、止める設定だけが違う。
func UserPruneForm(subject, binPath string) string {
	runes, _ := unquotedMarks(subject)
	text := string(runes)
	if !strings.Contains(pyfmt.Casefold(text), pruneVerb) || onlyReaders(text) {
		return ""
	}
	names := namesFor(binPath)
	for _, seg := range pruneSegment.all(text) {
		segment := seg.Text()
		found, ok := names.first(segment)
		if !ok {
			continue
		}
		if verb, ok := userPruneRx.matchAt(segment, found.End); ok {
			return ospath.Strip(verb.Text())
		}
	}
	return ""
}

// previewed は `--prune`（start から end）の隣に、単独の語の `--preview` が引用をまたがずに並ぶか。
func previewed(text string, cuts map[int]bool, start, end int) bool {
	if after, ok := previewAfter.matchAt(text, end); ok {
		crossed := false
		for i := end; i < after.End; i++ {
			crossed = crossed || cuts[i]
		}
		if !crossed {
			return true
		}
	}
	before, ok := previewBefore.firstIn(text, 0, start)
	if !ok {
		return false
	}
	for i := before.Start + utf8.RuneCountInString(optPreview); i <= start; i++ {
		if cuts[i] {
			return false
		}
	}
	return true
}

// PruneMessage は `--prune` で止めた文。
func PruneMessage(root string) string { return texts.Say(root, textRecordsPrune, texts.Values{}, nil) }
