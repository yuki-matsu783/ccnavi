package tally

import (
	"fmt"
	"sort"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/audit"
)

// 止められたエージェントは、言い回しを変えて打ち直すことがある。
// 同じルールが同じ呼び出しを止めた回数を数え、N 回目から拒否の文面に「言い換えずに相談する」一文を足す。ターンの終わりにも報告する。
//
// 数えるキーは、ルールの id と、均した対象のハッシュの組。均すのは、引用符を除くことと空白をまとめることだけ。
// 大文字と小文字は分けたままにする。パスもコマンドも、大小を区別する環境があるため。
// 認証情報を残さないように、記録にはハッシュだけを置く。

// DefaultDenyThreshold は N の既定（CCNAVI_DENY_REPEAT）。
const DefaultDenyThreshold = 3

// minDenyThreshold より小さい N は既定に戻す。N が 1 だと、1 回目の拒否の時点で、代わりの方法を試す前に止まってしまう。
const minDenyThreshold = 2

const (
	// DenyPrefix は拒否の数えの名前の先頭（後ろはセッション）。後始末（records/prune）が名前からセッションを読む。
	DenyPrefix = "denied-"
	// keyHashLen はキーに使うハッシュの長さ（16 進の字数）。
	keyHashLen = 16
	// noRule は理由の名前が無いときの名前。
	noRule = "(deny)"
)

// DenyThreshold は書かれた N。読めない値と 2 未満は既定値になる。
func DenyThreshold(written string) int {
	return config.Int(written, DefaultDenyThreshold, minDenyThreshold)
}

// Normalize は同じ呼び出しとして数えるための均し（引用符を除き、空白をまとめる）。
func Normalize(subject string) string {
	subject = strings.Map(func(r rune) rune {
		if r == '\'' || r == '"' || r == '`' {
			return -1
		}
		return r
	}, subject)
	return strings.Join(strings.FieldsFunc(subject, ospath.IsSpace), " ")
}

// RuleOf は、止めた理由の名前を返す。判定を下したルールの id。無ければ根拠の種別。
func RuleOf(r *audit.Record) string {
	if len(r.Rules) > 0 {
		return r.Rules[0]
	}
	if r.Code != "" {
		return r.Code
	}
	return noRule
}

func keyOf(rule, subject string) string {
	return rule + " " + digest.Hex(digest.SHA256, Normalize(subject), keyHashLen)
}

func denyPath(stateDir, session string) string {
	return fsio.RecordPath(stateDir, DenyPrefix, session, fsio.JSONExt)
}

// CountDeny はこの拒否を数え、同じキーのこれまでの回数（今回を含む）を返す。数えられなければ 0 を返す。
// 置き場が無いとき（--test）と、セッションが無いときは数えない。別のセッションの拒否と混ぜると、打ち直していない相手に「言い換えるな」と言ってしまうため。
func CountDeny(stateDir string, r *audit.Record) int {
	if stateDir == "" || r.Subject == "" || r.Session == "" {
		return 0
	}
	path := denyPath(stateDir, r.Session)
	data, ok := fsio.ReadObject(path)
	if !ok {
		data = pyjson.NewObject()
	}
	rule := RuleOf(r)
	key := keyOf(rule, r.Subject)
	entry, isObject := data.Value(key).(*pyjson.Object)
	if !isObject {
		entry = pyjson.NewObject("rule", rule, "tool", r.Tool, "count", 0, "told", 0)
	}
	count, ok := recordedInt(entry.Value("count"))
	if !ok {
		return 0
	}
	count++
	entry.Set("count", count)
	data.Set(key, entry)
	if fsio.WriteJSON(path, data) != "" {
		return 0
	}
	return count
}

// DenyNote は N 回目から拒否の文面の末尾に足す一文。
func DenyNote(rule string, n int) string {
	return fmt.Sprintf("[ccnavi] 同じ理由（%s）でこの呼び出しを %d 回止めました。", rule, n) +
		"言い換えて再実行せず、何をしたいのかと止まった理由をユーザに伝えて相談してください。"
}

const denyReportHead = "[ccnavi] このセッションで、同じ理由で同じ呼び出しを繰り返し止めました。" +
	"言い換えて同じ呼び出しを繰り返していないか、ユーザの判断が必要な場面ではないかを確かめてください。"

// DenyReport は、ターンの終わりにユーザへ言うことを返す。N 回に達したキーのうち、まだ言っていない回数のもの。
// 言った回数は記録に残す。記録に書けなければ言わない。覚えられないまま言うと、ターンの終わりごとに同じ行が並ぶため。
func DenyReport(stateDir, session string, n int) string {
	if stateDir == "" || session == "" {
		return ""
	}
	path := denyPath(stateDir, session)
	data, ok := fsio.ReadObject(path)
	if !ok || data.Len() == 0 {
		return ""
	}
	var lines []string
	for _, key := range data.Keys() {
		entry, isObject := data.Value(key).(*pyjson.Object)
		if !isObject {
			continue
		}
		got, ok := recordedInt(entry.Value("count"))
		if !ok {
			return ""
		}
		if got < n {
			continue
		}
		told, ok := recordedInt(entry.Value("told"))
		if !ok {
			return ""
		}
		if got <= told {
			continue
		}
		lines = append(lines, fmt.Sprintf("  %s（%s）: %d 回", orText(entry.Value("rule"), noRule), orText(entry.Value("tool"), "?"), got))
		entry.Set("told", got)
	}
	if len(lines) == 0 || fsio.WriteJSON(path, data) != "" {
		return ""
	}
	sort.Strings(lines)
	return strings.Join(append([]string{denyReportHead}, lines...), "\n")
}

// orText は Python の f"{v or fallback}"。
func orText(v any, fallback string) string {
	if !pyfmt.Truthy(v) {
		return fallback
	}
	return pyfmt.Str(v)
}
